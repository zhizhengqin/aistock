"""Behavior tests for the production LLM configuration service.

These tests intentionally exercise the public service contract instead of
asserting implementation details.  The executor fake only stands in for the
network boundary; probes still enter through ``LlmCallExecutor`` in
production and the service must pass the full operation metadata to it.
"""

from __future__ import annotations

import base64
from datetime import datetime, timezone
from types import SimpleNamespace

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
                "ok": True,
                "capabilities": {"json_mode": True, "usage": True},
            },
            model="deepseek-chat",
            prompt_tokens=12,
            completion_tokens=8,
            usage_source="provider",
            response_metadata={"status_code": 200},
        )

    async def call(self, **kwargs):
        self.calls.append(kwargs)
        return self.result


def _configure_keyring(monkeypatch):
    from app.core.config import settings

    key = base64.b64encode(b"k" * 32).decode("ascii")
    monkeypatch.setattr(settings, "LLM_CONFIG_ENCRYPTION_KEY_ID", "test-current")
    monkeypatch.setattr(settings, "LLM_CONFIG_ENCRYPTION_KEYS", {"test-current": key})


def _real_service(test_db, monkeypatch):
    """Build the production executor against a deterministic HTTP boundary."""

    _configure_keyring(monkeypatch)
    _, Session = test_db
    db = Session()

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/chat/completions")
        assert request.headers["authorization"] == "Bearer sk-super-secret"
        return httpx.Response(
            200,
            json={
                "model": "deepseek-chat",
                "choices": [
                    {
                        "message": {
                            "content": '{"ok":true,"capabilities":{"json_mode":true,"usage":true}}'
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
    disabled = service.disable(created["id"], expected_version=1, admin_user_id=1)
    assert disabled["lifecycle_status"] == "disabled"
    reenabled = await service.enable(created["id"], expected_version=2, admin_user_id=1)
    assert reenabled["lifecycle_status"] == "active"
    assert reenabled["verified_test_id"] != first_test_id
    assert len(executor.calls) == 2
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

    unlocked = service.unlock_settings(expected_version=3, reason="恢复额度后继续验证", admin_user_id=1)
    assert unlocked["budget_locked"] is False
    assert unlocked["version"] == 4
    assert db.query(LlmAdminAuditEvent).filter_by(event_type="budget_unlock").count() == 1
    ledger = db.query(LlmDailyBudget).one()
    assert (ledger.reserved_tokens, ledger.settled_tokens) == (17, 23)
    db.close()
