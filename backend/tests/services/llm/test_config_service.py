"""Behavior tests for the production LLM configuration service.

These tests intentionally exercise the public service contract instead of
asserting implementation details.  The executor fake only stands in for the
network boundary; probes still enter through ``LlmCallExecutor`` in
production and the service must pass the full operation metadata to it.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

# Register the production model-center tables before the shared SQLite fixture
# creates ``Base.metadata``.  This is test setup, not an application import
# side effect.
from app.models import llm_config as _llm_config_models  # noqa: F401
from app.models import llm_execution as _llm_execution_models  # noqa: F401
from app.models import task_record as _task_record_models  # noqa: F401
from app.models import llm_usage as _llm_usage_models  # noqa: F401
from app.models.llm_usage import LlmUsage
from app.models.llm_config import LlmActivationRequest, LlmModelConfig, LlmModelTestRun, LlmRuntimeSetting
from app.services.llm.provider_client import ProviderResult
from app.services.llm.provider_client import ProviderClient
from app.services.llm.call_executor import LlmCallExecutor
from app.services.llm.budget import TokenBudgetService
from app.services.llm.types import LlmRuntimeConfig, Provider
from app.services.llm.config_service import LlmConfigService


def _candidate(**overrides):
    values = {
        "provider": Provider.DEEPSEEK,
        "display_name": "DeepSeek 主模型",
        "model_name": "deepseek-chat",
        "base_url": "https://api.deepseek.com/v1",
        "api_key": "sk-super-secret",
        "max_output_tokens": 512,
        "input_price_micro_yuan_per_million": 1_400_000,
        "output_price_micro_yuan_per_million": 2_800_000,
    }
    values.update(overrides)
    return values


class RecordingExecutor:
    def __init__(self, result=None):
        self.calls = []
        self.result = result or ProviderResult(
            result_json={
                "decision": "hold",
                "confidence": 0.5,
                "rationale": "探测样本",
            },
            model="deepseek-chat",
            prompt_tokens=12,
            completion_tokens=8,
            usage_source="provider",
            response_metadata={"status_code": 200, "provider_model_present": True},
        )

    async def call(self, **kwargs):
        self.calls.append(kwargs)
        return self.result


class DelayedExecutor(RecordingExecutor):
    def __init__(self, delay_seconds: float, result=None):
        super().__init__(result=result)
        self.delay_seconds = delay_seconds

    async def call(self, **kwargs):
        self.calls.append(kwargs)
        await asyncio.sleep(self.delay_seconds)
        return self.result


class GateExecutor(RecordingExecutor):
    def __init__(self):
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def call(self, **kwargs):
        self.calls.append(kwargs)
        self.started.set()
        await self.release.wait()
        return self.result


def _configure_keyring(monkeypatch):
    from app.core.config import settings

    key = base64.b64encode(b"k" * 32).decode("ascii")
    monkeypatch.setattr(settings, "LLM_CONFIG_ENCRYPTION_KEY_ID", "test-current")
    monkeypatch.setattr(settings, "LLM_CONFIG_ENCRYPTION_KEYS", {"test-current": key})


def _real_service(test_db, monkeypatch, *, result_content=None, delay_seconds=0):
    """Build the production executor against a deterministic HTTP boundary."""

    _configure_keyring(monkeypatch)
    _, Session = test_db
    db = Session()

    if result_content is None:
        result_content = '{"decision":"hold","confidence":0.5,"rationale":"probe"}'

    async def handler(request: httpx.Request) -> httpx.Response:
        if delay_seconds:
            await asyncio.sleep(delay_seconds)
        assert request.url.path.endswith("/chat/completions")
        assert request.headers["authorization"] == "Bearer sk-super-secret"
        return httpx.Response(
            200,
            json={
                "model": "deepseek-chat",
                "choices": [
                    {
                        "message": {
                            "content": result_content
                        }
                    }
                ],
                "usage": {"prompt_tokens": 12, "completion_tokens": 8},
            },
            request=request,
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)
    provider_client = ProviderClient(client=client)
    executor = LlmCallExecutor(
        db=db,
        provider_client=provider_client,
        budget=TokenBudgetService(db, daily_token_limit=100_000),
    )
    return db, client, LlmConfigService(db, executor=executor)


def test_fingerprint_ignores_display_and_price_but_changes_runtime_fields(monkeypatch):
    from app.services.llm.config_service import runtime_fingerprint

    first = runtime_fingerprint(
        provider=Provider.DEEPSEEK,
        model_name="deepseek-chat",
        base_url="https://api.deepseek.com/v1",
        credential_version="credential-a",
        max_output_tokens=512,
    )
    second = runtime_fingerprint(
        provider=Provider.DEEPSEEK,
        model_name="deepseek-chat",
        base_url="https://api.deepseek.com/v1/",
        credential_version="credential-a",
        max_output_tokens=512,
    )
    changed = runtime_fingerprint(
        provider=Provider.DEEPSEEK,
        model_name="deepseek-reasoner",
        base_url="https://api.deepseek.com/v1",
        credential_version="credential-a",
        max_output_tokens=512,
    )
    assert first == second
    assert first != changed


@pytest.mark.asyncio
async def test_unsaved_probe_is_audited_without_persisting_secret(test_db, monkeypatch):
    db, http_client, service = _real_service(test_db, monkeypatch)

    try:
        response = await service.test_unsaved(_candidate())

        assert response["status"] == "success"
        assert "sk-super-secret" not in repr(response)

        from app.models.llm_execution import LlmCallAttempt
        from app.models.llm_config import LlmModelTestRun

        assert db.query(LlmModelTestRun).count() == 1
        assert db.query(LlmModelTestRun).one().model_config_id is None
        attempt = db.query(LlmCallAttempt).one()
        assert attempt.model_config_id is None
        assert attempt.operation_type == "admin_probe"
        assert attempt.provider_snapshot == "deepseek"
        assert attempt.model_snapshot == "deepseek-chat"
        assert db.query(LlmUsage).one().module == "admin_probe"
        assert "sk-super-secret" not in repr(attempt)
    finally:
        await http_client.aclose()
        db.close()


@pytest.mark.asyncio
async def test_create_returns_redacted_config_and_runtime_patch_creates_successor(test_db, monkeypatch):
    from app.services.llm.config_service import LlmConfigService
    from app.models.llm_config import LlmModelConfig

    _configure_keyring(monkeypatch)
    _, Session = test_db
    db = Session()
    service = LlmConfigService(db, executor=RecordingExecutor())
    created = service.create(_candidate(), admin_user_id=1)

    assert created["version"] == 1
    assert created["created_new_version"] is False
    assert created["key_hint"] == "sk-s...cret"
    assert "api_key" not in created
    assert "encrypted_api_key" not in repr(created)

    successor = service.patch(
        created["id"],
        {"expected_version": 1, "model_name": "deepseek-reasoner"},
        admin_user_id=1,
    )
    assert successor["created_new_version"] is True
    assert successor["supersedes_id"] == created["id"]
    assert successor["id"] != created["id"]
    assert db.query(LlmModelConfig).count() == 2
    successor_row = db.get(LlmModelConfig, successor["id"])
    assert service._runtime(successor_row).api_key == "sk-super-secret"

    kept = service.patch(
        successor["id"],
        {"expected_version": 1, "api_key": "", "display_name": "只改展示"},
        admin_user_id=1,
    )
    assert kept["created_new_version"] is False
    assert service._runtime(db.get(LlmModelConfig, successor["id"])).api_key == "sk-super-secret"
    db.close()


@pytest.mark.asyncio
async def test_saved_probe_uses_real_executor_and_audits_config_snapshot(test_db, monkeypatch):
    db, http_client, service = _real_service(test_db, monkeypatch)
    try:
        created = service.create(_candidate(), admin_user_id=1)
        response = await service.test_saved(created["id"], admin_user_id=1)
        assert response["status"] == "success"
        from app.models.llm_execution import LlmCallAttempt
        from app.models.llm_config import LlmModelTestRun

        run = db.query(LlmModelTestRun).one()
        attempt = db.query(LlmCallAttempt).one()
        assert run.model_config_id == created["id"]
        assert attempt.model_config_id == created["id"]
        assert attempt.operation_type == "admin_probe"
        assert attempt.runtime_fingerprint == created["runtime_fingerprint"]
        assert db.query(LlmUsage).one().model_config_id == created["id"]
    finally:
        await http_client.aclose()
        db.close()


@pytest.mark.asyncio
async def test_real_enable_and_activate_probes_use_audited_executor(test_db, monkeypatch):
    db, http_client, service = _real_service(test_db, monkeypatch)
    try:
        first = service.create(_candidate(display_name="独立默认"), admin_user_id=1)
        enabled = await service.enable(first["id"], expected_version=1, admin_user_id=1)
        assert enabled["lifecycle_status"] == "active"

        second = service.create(_candidate(display_name="待切换"), admin_user_id=1)
        activated = await service.activate(
            second["id"],
            expected_version=1,
            idempotency_key="activate-real-1",
            admin_user_id=1,
        )
        assert db.query(LlmRuntimeSetting).filter_by(id=1).one().default_model_config_id == second["id"]

        from app.models.llm_execution import LlmCallAttempt
        attempts = db.query(LlmCallAttempt).all()
        assert len(attempts) == 2
        assert all(item.operation_type == "admin_probe" for item in attempts)
        assert all(item.task_id is None for item in attempts)
    finally:
        await http_client.aclose()
        db.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "result",
    [
        ProviderResult(
            result_json={"decision": "hold", "confidence": 0.5, "rationale": "ok"},
            model="deepseek-chat",
            prompt_tokens=None,
            completion_tokens=None,
            usage_source="missing",
            response_metadata={"provider_model_present": True},
        ),
        ProviderResult(
            result_json={"decision": "hold", "confidence": 0.5, "rationale": "ok"},
            model="deepseek-chat",
            prompt_tokens=1,
            completion_tokens=1,
            usage_source="provider",
            response_metadata={"provider_model_present": False},
        ),
        ProviderResult(
            result_json={"decision": "hold"},
            model="deepseek-chat",
            prompt_tokens=1,
            completion_tokens=1,
            usage_source="provider",
            response_metadata={"provider_model_present": True},
        ),
    ],
)
async def test_probe_rejects_missing_usage_model_or_business_shape(test_db, monkeypatch, result):
    _configure_keyring(monkeypatch)
    _, Session = test_db
    db = Session()
    service = LlmConfigService(db, executor=RecordingExecutor(result=result))
    response = await service.test_unsaved(_candidate())
    assert response["status"] == "failed"
    assert response["error_code"] == "llm_probe_invalid"
    assert db.query(LlmModelTestRun).one().status == "failed"
    db.close()


@pytest.mark.asyncio
async def test_invalid_business_shape_keeps_provider_evidence_and_usage_audit(test_db, monkeypatch):
    db, http_client, service = _real_service(
        test_db,
        monkeypatch,
        result_content='{"unexpected":"provider-body"}',
    )
    try:
        response = await service.test_unsaved(_candidate())
        assert response["status"] == "failed"
        assert response["error_code"] == "llm_probe_invalid"

        from app.models.llm_execution import LlmCallAttempt

        run = db.query(LlmModelTestRun).one()
        attempt = db.query(LlmCallAttempt).one()
        usage = db.query(LlmUsage).one()
        assert run.status == "failed"
        assert run.result_json == {"unexpected": "provider-body"}
        assert run.response_model == "deepseek-chat"
        assert (run.input_tokens, run.output_tokens) == (12, 8)
        assert attempt.status == "success"
        assert attempt.result_json == {"unexpected": "provider-body"}
        assert attempt.response_model_snapshot == "deepseek-chat"
        assert (attempt.input_tokens, attempt.output_tokens) == (12, 8)
        assert attempt.operation_type == "admin_probe"
        assert usage.status == "success"
        assert (usage.input_tokens, usage.output_tokens) == (12, 8)
    finally:
        await http_client.aclose()
        db.close()


@pytest.mark.asyncio
async def test_activation_is_idempotent_and_conflicting_payload_is_rejected(test_db, monkeypatch):
    from app.services.llm.config_service import LlmConfigService

    _configure_keyring(monkeypatch)
    _, Session = test_db
    db = Session()
    executor = RecordingExecutor()
    service = LlmConfigService(db, executor=executor)
    created = service.create(_candidate(), admin_user_id=1)
    await service.test_saved(created["id"], admin_user_id=1)

    first = await service.activate(
        created["id"],
        expected_version=1,
        idempotency_key="activate-1",
        admin_user_id=1,
    )
    replay = await service.activate(
        created["id"],
        expected_version=1,
        idempotency_key="activate-1",
        admin_user_id=1,
    )
    assert replay == first

    with pytest.raises(Exception) as exc:
        await service.activate(
            created["id"],
            expected_version=2,
            idempotency_key="activate-1",
            admin_user_id=2,
        )
    assert getattr(exc.value, "code", None) == "llm_idempotency_conflict"
    db.close()


@pytest.mark.asyncio
async def test_failed_activation_persists_only_stable_redacted_error(test_db, monkeypatch):
    _configure_keyring(monkeypatch)
    _, Session = test_db
    db = Session()
    executor = RecordingExecutor(
        result=ProviderResult(
            result_json={"unexpected": "provider-body"},
            model="deepseek-chat",
            prompt_tokens=1,
            completion_tokens=1,
            usage_source="provider",
            response_metadata={"provider_model_present": True},
        )
    )
    service = LlmConfigService(db, executor=executor)
    created = service.create(_candidate(), admin_user_id=1)

    with pytest.raises(Exception) as exc:
        await service.activate(
            created["id"],
            expected_version=1,
            idempotency_key="failed-activation",
            admin_user_id=1,
        )
    assert getattr(exc.value, "code", None) == "llm_probe_failed"
    request = db.query(LlmActivationRequest).one()
    assert request.status == "failed"
    assert request.response_json == {
        "__error__": True,
        "code": "llm_probe_failed",
        "message": "模型测试失败，默认模型未切换",
        "status_code": 503,
        "field": None,
    }
    assert "sk-super-secret" not in repr(request.response_json)
    assert "provider-body" not in repr(request.response_json)
    db.close()


@pytest.mark.asyncio
async def test_same_key_waits_for_slow_owner_without_duplicate_probe(test_db, monkeypatch):
    _configure_keyring(monkeypatch)
    _, Session = test_db
    setup = Session()
    created = LlmConfigService(setup, executor=RecordingExecutor()).create(_candidate(), admin_user_id=1)
    setup.close()
    executor = DelayedExecutor(0.2)

    async def call(admin_user_id):
        db = Session()
        try:
            return await LlmConfigService(
                db,
                executor=executor,
                probe_deadline_seconds=0.5,
                activation_completion_grace_seconds=2.0,
            ).activate(
                created["id"],
                expected_version=1,
                idempotency_key="slow-owner",
                admin_user_id=admin_user_id,
            )
        finally:
            db.close()

    first, second = await asyncio.gather(call(1), call(2))
    assert first == second
    assert len(executor.calls) == 1


@pytest.mark.asyncio
async def test_expired_pending_is_marked_owner_lost_without_reprobe(test_db, monkeypatch):
    _configure_keyring(monkeypatch)
    _, Session = test_db
    db = Session()
    created = LlmConfigService(db, executor=RecordingExecutor()).create(_candidate(), admin_user_id=1)
    request_hash = hashlib.sha256(
        json.dumps(
            {"model_config_id": created["id"], "expected_version": 1},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    db.add(
        LlmActivationRequest(
            idempotency_key="owner-crashed",
            request_hash=request_hash,
            model_config_id=created["id"],
            expected_version=1,
            status="pending",
            created_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        )
    )
    db.commit()
    executor = RecordingExecutor()
    service = LlmConfigService(
        db,
        executor=executor,
        probe_deadline_seconds=0.05,
        activation_completion_grace_seconds=0.01,
    )

    with pytest.raises(Exception) as exc:
        await service.activate(
            created["id"],
            expected_version=1,
            idempotency_key="owner-crashed",
            admin_user_id=1,
        )
    assert getattr(exc.value, "code", None) == "llm_activation_owner_lost"
    assert executor.calls == []
    request = db.query(LlmActivationRequest).one()
    assert request.status == "failed"
    assert request.response_json["code"] == "llm_activation_owner_lost"
    assert request.response_json["message"]

    with pytest.raises(Exception) as replay_exc:
        await service.activate(
            created["id"],
            expected_version=1,
            idempotency_key="owner-crashed",
            admin_user_id=2,
        )
    assert getattr(replay_exc.value, "code", None) == "llm_activation_owner_lost"
    assert executor.calls == []
    db.close()


@pytest.mark.asyncio
async def test_stale_owner_cannot_finalize_after_competitor_marks_failed(test_db, monkeypatch):
    _configure_keyring(monkeypatch)
    _, Session = test_db
    setup = Session()
    created = LlmConfigService(setup, executor=RecordingExecutor()).create(_candidate(), admin_user_id=1)
    setup.close()
    owner_db = Session()
    owner_executor = GateExecutor()
    owner = LlmConfigService(
        owner_db,
        executor=owner_executor,
        probe_deadline_seconds=0.05,
        activation_completion_grace_seconds=0.01,
    )
    owner_task = asyncio.create_task(
        owner.activate(
            created["id"],
            expected_version=1,
            idempotency_key="stale-owner",
            admin_user_id=1,
        )
    )
    await owner_executor.started.wait()

    age_db = Session()
    pending = age_db.query(LlmActivationRequest).filter_by(idempotency_key="stale-owner").one()
    pending.created_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    age_db.commit()
    age_db.close()

    competitor_db = Session()
    competitor = LlmConfigService(
        competitor_db,
        executor=RecordingExecutor(),
        probe_deadline_seconds=0.05,
        activation_completion_grace_seconds=0.01,
    )
    with pytest.raises(Exception) as competitor_exc:
        await competitor.activate(
            created["id"],
            expected_version=1,
            idempotency_key="stale-owner",
            admin_user_id=2,
        )
    assert getattr(competitor_exc.value, "code", None) == "llm_activation_owner_lost"
    owner_executor.release.set()
    with pytest.raises(Exception) as owner_exc:
        await owner_task
    assert getattr(owner_exc.value, "code", None) == "llm_activation_owner_lost"

    check_db = Session()
    setting = check_db.query(LlmRuntimeSetting).filter_by(id=1).one()
    config = check_db.get(LlmModelConfig, created["id"])
    request = check_db.query(LlmActivationRequest).filter_by(idempotency_key="stale-owner").one()
    assert setting.default_model_config_id is None
    assert config.lifecycle_status == "draft"
    assert config.deleted_at is None
    assert request.status == "failed"
    check_db.close()
    competitor_db.close()
    owner_db.close()


@pytest.mark.asyncio
async def test_probe_timeout_persists_failed_run_and_pending_terminal_error(test_db, monkeypatch):
    _configure_keyring(monkeypatch)
    _, Session = test_db
    db = Session()
    created = LlmConfigService(db, executor=RecordingExecutor()).create(_candidate(), admin_user_id=1)
    executor = DelayedExecutor(0.2)
    service = LlmConfigService(
        db,
        executor=executor,
        probe_deadline_seconds=0.05,
        activation_completion_grace_seconds=0.05,
    )

    with pytest.raises(Exception) as exc:
        await service.activate(
            created["id"],
            expected_version=1,
            idempotency_key="timeout-owner",
            admin_user_id=1,
        )
    assert getattr(exc.value, "code", None) == "llm_probe_timeout"
    run = db.query(LlmModelTestRun).one()
    request = db.query(LlmActivationRequest).one()
    assert run.status == "failed"
    assert run.error_code == "llm_probe_timeout"
    assert request.status == "failed"
    assert request.response_json["code"] == "llm_probe_timeout"
    assert executor.calls
    db.close()


@pytest.mark.asyncio
async def test_disable_then_enable_runs_a_new_matching_probe(test_db, monkeypatch):
    _configure_keyring(monkeypatch)
    _, Session = test_db
    db = Session()
    executor = RecordingExecutor()
    service = LlmConfigService(db, executor=executor)
    created = service.create(_candidate(display_name="可切换备份"), admin_user_id=1)

    enabled = await service.enable(created["id"], expected_version=1, admin_user_id=1)
    assert enabled["lifecycle_status"] == "active"
    first_test_id = enabled["verified_test_id"]
    disabled = service.disable(created["id"], expected_version=2, admin_user_id=1)
    assert disabled["lifecycle_status"] == "disabled"
    reenabled = await service.enable(created["id"], expected_version=3, admin_user_id=1)
    assert reenabled["lifecycle_status"] == "active"
    assert reenabled["verified_test_id"] != first_test_id
    assert len(executor.calls) == 2
    db.close()


@pytest.mark.asyncio
async def test_independent_default_switch_keeps_previous_model_active(test_db, monkeypatch):
    _configure_keyring(monkeypatch)
    _, Session = test_db
    db = Session()
    service = LlmConfigService(db, executor=RecordingExecutor())
    first = service.create(_candidate(display_name="第一个"), admin_user_id=1)
    await service.activate(first["id"], expected_version=1, idempotency_key="independent-a", admin_user_id=1)
    second = service.create(_candidate(display_name="第二个"), admin_user_id=1)
    await service.activate(second["id"], expected_version=1, idempotency_key="independent-b", admin_user_id=1)
    assert db.get(LlmModelConfig, first["id"]).lifecycle_status == "active"
    assert db.get(LlmModelConfig, second["id"]).lifecycle_status == "active"
    db.close()


def test_default_cannot_be_disabled_or_deleted_and_unlock_is_audited(test_db, monkeypatch):
    from app.services.llm.config_service import LlmConfigService
    from app.models.llm_config import LlmAdminAuditEvent, LlmRuntimeSetting
    from app.models.llm_execution import LlmDailyBudget
    from datetime import date

    _configure_keyring(monkeypatch)
    _, Session = test_db
    db = Session()
    service = LlmConfigService(db, executor=RecordingExecutor())
    created = service.create(_candidate(), admin_user_id=1)
    setting = db.query(LlmRuntimeSetting).filter_by(id=1).one()
    setting.default_model_config_id = created["id"]
    setting.budget_locked = True
    setting.version = 3
    ledger = LlmDailyBudget(budget_date=date(2026, 8, 13), reserved_tokens=17, settled_tokens=23)
    db.add(ledger)
    db.commit()

    with pytest.raises(Exception) as exc:
        service.disable(created["id"], expected_version=1, admin_user_id=1)
    assert getattr(exc.value, "code", None) == "llm_default_disable_forbidden"
    with pytest.raises(Exception) as exc:
        service.delete(created["id"], admin_user_id=1)
    assert getattr(exc.value, "code", None) == "llm_default_delete_forbidden"

    setting.default_model_config_id = None
    setting.budget_locked = False
    setting.version = 5
    config = db.get(LlmModelConfig, created["id"])
    config.lifecycle_status = "active"
    config.deleted_at = None
    setting.budget_locked = True
    db.commit()
    with pytest.raises(Exception) as exc:
        service.delete(created["id"], admin_user_id=1)
    assert getattr(exc.value, "code", None) == "llm_active_delete_forbidden"

    unlocked = service.unlock_settings(expected_version=5, reason="恢复额度后继续验证", admin_user_id=1)
    assert unlocked["budget_locked"] is False
    assert unlocked["version"] == 6
    assert db.query(LlmAdminAuditEvent).filter_by(event_type="budget_unlock").count() == 1
    ledger = db.query(LlmDailyBudget).one()
    assert (ledger.reserved_tokens, ledger.settled_tokens) == (17, 23)
    db.close()
