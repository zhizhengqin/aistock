"""Behavior coverage for the one-shot LLM bootstrap/readiness/smoke CLI."""

from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.base import Base
from app.models.llm_config import (
    LlmModelConfig,
    LlmModelTestRun,
    LlmRuntimeSetting,
)
from app.models.llm_execution import LlmCallAttempt, LlmDailyBudget, LlmTokenReservation
from app.models.llm_usage import LlmUsage
from app.services.llm.errors import LlmError
from app.services.llm.budget import TokenBudgetService
from app.services.llm.call_executor import LlmCallExecutor
from app.services.llm.provider_client import ProviderResult
from app.services.llm.provider_client import ProviderClient
from app.services.llm.types import Provider


@pytest.fixture()
def db_factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    Base.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture()
def keyring(monkeypatch):
    from app.core.config import settings

    key = base64.b64encode(b"b" * 32).decode("ascii")
    monkeypatch.setattr(settings, "LLM_CONFIG_ENCRYPTION_KEY_ID", "cli-current")
    monkeypatch.setattr(settings, "LLM_CONFIG_ENCRYPTION_KEYS", {"cli-current": key})
    monkeypatch.setattr(settings, "DAILY_TOKEN_LIMIT", 100_000)
    monkeypatch.setattr(settings, "DEEPSEEK_API_KEY", "")
    monkeypatch.setattr(settings, "LLM_MODEL", "deepseek-chat")
    monkeypatch.setattr(settings, "LLM_BASE_URL", "https://api.deepseek.com/v1")
    return key


class RecordingExecutor:
    def __init__(self, result=None, error=None):
        self.calls: list[dict] = []
        self.result = result or ProviderResult(
            result_json={"decision": "hold", "confidence": 0.5, "rationale": "probe"},
            model="deepseek-chat",
            prompt_tokens=8,
            completion_tokens=4,
            usage_source="provider",
            response_metadata={"provider_model_present": True},
        )
        self.error = error

    async def call(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.result


class ReleasedBudget(TokenBudgetService):
    """Test-only budget adapter that proves released is not settled evidence."""

    def settle(self, reservation, actual_tokens=None, *, actual=None, unknown=False):
        return self.release(reservation)


def _real_executor(
    db_factory,
    *,
    api_key="sk-smoke-secret",
    expected_max_tokens=256,
    status_code=200,
    error_message="invalid key",
):
    db = db_factory()

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == f"Bearer {api_key}"
        body = request.read()
        assert json.loads(body)["max_tokens"] == expected_max_tokens
        if status_code != 200:
            return httpx.Response(
                status_code,
                json={"error": {"message": error_message}},
                request=request,
            )
        return httpx.Response(
            200,
            json={
                "model": "deepseek-chat",
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {"decision": "hold", "confidence": 0.5, "rationale": "probe"}
                            )
                        }
                    }
                ],
                "usage": {"prompt_tokens": 8, "completion_tokens": 4},
            },
            request=request,
        )

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        follow_redirects=False,
    )
    provider_client = ProviderClient(client=client)
    executor = LlmCallExecutor(
        db=db,
        provider_client=provider_client,
        budget=TokenBudgetService(db, daily_token_limit=100_000),
    )
    return db, client, executor


def _cli(monkeypatch):
    from app.cli import llm_config

    monkeypatch.setattr(llm_config, "_notify_admins", lambda *args, **kwargs: None)
    return llm_config


def test_bootstrap_empty_without_legacy_key_creates_settings_only(db_factory, keyring, monkeypatch):
    cli = _cli(monkeypatch)
    result = cli.run_bootstrap(session_factory=db_factory)

    assert result == 0
    with db_factory() as db:
        assert db.query(LlmRuntimeSetting).count() == 1
        assert db.query(LlmModelConfig).count() == 0


def test_bootstrap_success_uses_bootstrap_executor_and_activates_default(
    db_factory, keyring, monkeypatch
):
    cli = _cli(monkeypatch)
    monkeypatch.setattr(cli.settings, "DEEPSEEK_API_KEY", "sk-cli-secret")
    db, http_client, executor = _real_executor(
        db_factory,
        api_key="sk-cli-secret",
        expected_max_tokens=4096,
    )
    try:
        result = cli.run_bootstrap(session_factory=db_factory, executor=executor)
    finally:
        asyncio.run(http_client.aclose())
        db.close()

    assert result == 0
    with db_factory() as db:
        setting = db.get(LlmRuntimeSetting, 1)
        config = db.query(LlmModelConfig).one()
        run = db.query(LlmModelTestRun).one()
        assert setting.default_model_config_id == config.id
        assert config.lifecycle_status == "active"
        assert run.status == "success"
        assert "sk-cli-secret" not in json.dumps(config.__dict__, default=str)
        attempt = db.query(LlmCallAttempt).one()
        usage = db.query(LlmUsage).one()
        assert attempt.operation_type == "bootstrap"
        assert attempt.status == "success"
        assert usage.module == "bootstrap"


def test_bootstrap_real_executor_failure_keeps_audited_candidate(
    db_factory, keyring, monkeypatch
):
    cli = _cli(monkeypatch)
    monkeypatch.setattr(cli.settings, "DEEPSEEK_API_KEY", "sk-cli-secret")
    db, http_client, executor = _real_executor(
        db_factory,
        api_key="sk-cli-secret",
        expected_max_tokens=4096,
        status_code=401,
    )
    try:
        result = cli.run_bootstrap(session_factory=db_factory, executor=executor)
    finally:
        asyncio.run(http_client.aclose())
        db.close()

    assert result == 0
    with db_factory() as db:
        config = db.query(LlmModelConfig).one()
        assert config.lifecycle_status == "draft"
        assert db.get(LlmRuntimeSetting, 1).default_model_config_id is None
        attempt = db.query(LlmCallAttempt).one()
        usage = db.query(LlmUsage).one()
        assert attempt.operation_type == "bootstrap"
        assert attempt.status == "failed"
        assert usage.module == "bootstrap"
        assert "sk-cli-secret" not in repr(attempt)


def test_bootstrap_probe_failure_persists_candidate_notifies_and_exits_zero(
    db_factory, keyring, monkeypatch
):
    cli = _cli(monkeypatch)
    monkeypatch.setattr(cli.settings, "DEEPSEEK_API_KEY", "sk-cli-secret")
    notifications = []
    monkeypatch.setattr(cli, "_notify_admins", lambda *args, **kwargs: notifications.append(args))
    db, http_client, executor = _real_executor(
        db_factory,
        api_key="sk-cli-secret",
        expected_max_tokens=4096,
        status_code=401,
    )
    try:
        result = cli.run_bootstrap(session_factory=db_factory, executor=executor)
    finally:
        asyncio.run(http_client.aclose())
        db.close()

    assert result == 0
    assert notifications
    assert "sk-cli-secret" not in repr(notifications)
    with db_factory() as db:
        config = db.query(LlmModelConfig).one()
        assert config.lifecycle_status == "draft"
        assert db.get(LlmRuntimeSetting, 1).default_model_config_id is None
        assert db.query(LlmModelTestRun).one().status == "failed"


def test_bootstrap_real_provider_daily_limit_is_nonfatal_with_attempt_evidence(
    db_factory, keyring, monkeypatch
):
    cli = _cli(monkeypatch)
    monkeypatch.setattr(cli.settings, "DEEPSEEK_API_KEY", "sk-provider-daily-secret")
    db, http_client, executor = _real_executor(
        db_factory,
        api_key="sk-provider-daily-secret",
        expected_max_tokens=4096,
        status_code=400,
        error_message="daily_limit reached by provider",
    )
    try:
        result = cli.run_bootstrap(session_factory=db_factory, executor=executor)
    finally:
        asyncio.run(http_client.aclose())
        db.close()

    assert result == 0
    with db_factory() as db:
        config = db.query(LlmModelConfig).one()
        attempt = db.query(LlmCallAttempt).one()
        reservation = db.get(LlmTokenReservation, attempt.reservation_id)
        assert config.lifecycle_status == "draft"
        assert attempt.error_code == "llm_daily_limit_reached"
        assert attempt.status == "failed"
        assert reservation.status == "settled"


def test_bootstrap_local_budget_lock_is_fatal_without_attempt(
    db_factory, keyring, monkeypatch
):
    cli = _cli(monkeypatch)
    monkeypatch.setattr(cli.settings, "DEEPSEEK_API_KEY", "sk-local-budget-secret")
    with db_factory() as db:
        db.add(LlmRuntimeSetting(id=1, daily_token_limit=100_000, budget_locked=True))
        db.commit()
    db, http_client, executor = _real_executor(
        db_factory,
        api_key="sk-local-budget-secret",
        expected_max_tokens=4096,
    )
    try:
        result = cli.run_bootstrap(session_factory=db_factory, executor=executor)
    finally:
        asyncio.run(http_client.aclose())
        db.close()

    assert result == 1
    with db_factory() as db:
        assert db.query(LlmCallAttempt).count() == 0
        assert db.query(LlmModelConfig).count() == 0


class _FailOnceCommitSession(Session):
    def commit(self):
        if self.info.pop("fail_commit_once", False):
            raise RuntimeError("simulated cleanup persistence failure")
        return super().commit()


def test_bootstrap_cleanup_persistence_failure_recovers_same_candidate(
    db_factory, keyring, monkeypatch
):
    cli = _cli(monkeypatch)
    monkeypatch.setattr(cli.settings, "DEEPSEEK_API_KEY", "sk-recover-secret")
    engine = db_factory.kw["bind"] if hasattr(db_factory, "kw") else None
    # Build a factory which fails exactly on the cleanup commit (build and
    # started-test commits have already succeeded), then behaves normally.
    assert engine is not None
    maker = sessionmaker(bind=engine, class_=_FailOnceCommitSession, expire_on_commit=False)
    calls = 0

    def flaky_factory():
        nonlocal calls
        calls += 1
        session = maker()
        if calls == 3:
            session.info["fail_commit_once"] = True
        return session

    first = cli.run_bootstrap(
        session_factory=flaky_factory,
        executor=RecordingExecutor(error=RuntimeError("programming failure")),
    )
    assert first == 1
    with db_factory() as db:
        assert db.query(LlmModelConfig).count() == 1
        assert db.query(LlmModelTestRun).count() == 1
        assert db.query(LlmModelTestRun).one().status == "failed"

    second = cli.run_bootstrap(
        session_factory=flaky_factory,
        executor=RecordingExecutor(),
    )
    assert second == 0
    with db_factory() as db:
        config = db.query(LlmModelConfig).one()
        assert config.lifecycle_status == "active"
        assert db.get(LlmRuntimeSetting, 1).default_model_config_id == config.id
        runs = db.query(LlmModelTestRun).order_by(LlmModelTestRun.created_at).all()
        assert len(runs) == 2
        assert runs[0].status == "failed"
        assert runs[1].status == "success"


def test_bootstrap_multiple_legacy_candidates_is_safe_noop(
    db_factory, keyring, monkeypatch
):
    cli = _cli(monkeypatch)
    monkeypatch.setattr(cli.settings, "DEEPSEEK_API_KEY", "sk-ambiguous-secret")
    from app.services.llm.config_service import runtime_fingerprint
    from app.services.llm.crypto import encrypt_api_key

    with db_factory() as db:
        for suffix in ("one", "two"):
            config_id = f"legacy-candidate-{suffix}"
            envelope = encrypt_api_key(
                f"sk-{suffix}-secret",
                config_id=config_id,
                provider=Provider.DEEPSEEK,
                keyring=cli.settings.LLM_CONFIG_ENCRYPTION_KEYS,
            )
            db.add(
                LlmModelConfig(
                    id=config_id,
                    provider="deepseek",
                    display_name="DeepSeek 环境变量迁移",
                    model_name="deepseek-chat",
                    base_url="https://api.deepseek.com/v1",
                    encrypted_api_key=envelope.encrypted_api_key,
                    encryption_key_id=envelope.encryption_key_id,
                    envelope_version=envelope.envelope_version,
                    nonce=envelope.nonce,
                    runtime_fingerprint=runtime_fingerprint(
                        provider=Provider.DEEPSEEK,
                        model_name="deepseek-chat",
                        base_url="https://api.deepseek.com/v1",
                        credential_version="ambiguous-credential",
                        max_output_tokens=4096,
                    ),
                    lifecycle_status="draft",
                )
            )
        db.add(LlmRuntimeSetting(id=1))
        db.commit()
    executor = RecordingExecutor()

    result = cli.run_bootstrap(session_factory=db_factory, executor=executor)

    assert result == 0
    assert executor.calls == []
    with db_factory() as db:
        assert db.query(LlmModelConfig).count() == 2
        assert db.get(LlmRuntimeSetting, 1).default_model_config_id is None


@pytest.mark.parametrize(
    "error",
    [
        RuntimeError("programming failure"),
        LlmError("今日额度已锁定", code="llm_daily_limit_reached"),
        LlmError("传输配置无效", code="llm_transport_config"),
        LlmError("预算状态无效", code="llm_budget_locked"),
    ],
    ids=["runtime-error", "daily-limit", "transport-config", "budget-locked"],
)
def test_bootstrap_local_or_programming_probe_failure_is_fatal_and_retryable(
    db_factory, keyring, monkeypatch, error
):
    cli = _cli(monkeypatch)
    monkeypatch.setattr(cli.settings, "DEEPSEEK_API_KEY", "sk-local-fatal-secret")

    first = cli.run_bootstrap(
        session_factory=db_factory,
        executor=RecordingExecutor(error=error),
    )

    assert first == 1
    with db_factory() as db:
        # A local failure must not leave an active candidate that blocks the
        # next one-shot bootstrap.  Real call evidence may be retained as a
        # soft-deleted row, but it is never eligible for ownership.
        assert db.query(LlmModelConfig).filter(LlmModelConfig.deleted_at.is_(None)).count() == 0
        assert db.get(LlmRuntimeSetting, 1) is not None

    second = cli.run_bootstrap(
        session_factory=db_factory,
        executor=RecordingExecutor(),
    )
    assert second == 0
    with db_factory() as db:
        assert db.query(LlmModelConfig).filter(LlmModelConfig.deleted_at.is_(None)).count() == 1
        assert db.get(LlmRuntimeSetting, 1).default_model_config_id is not None


def test_bootstrap_upstream_llm_error_is_persisted_and_exits_zero(db_factory, keyring, monkeypatch):
    cli = _cli(monkeypatch)
    monkeypatch.setattr(cli.settings, "DEEPSEEK_API_KEY", "sk-upstream-secret")
    db, http_client, executor = _real_executor(
        db_factory,
        api_key="sk-upstream-secret",
        expected_max_tokens=4096,
        status_code=401,
    )
    try:
        result = cli.run_bootstrap(session_factory=db_factory, executor=executor)
    finally:
        asyncio.run(http_client.aclose())
        db.close()

    assert result == 0
    with db_factory() as db:
        config = db.query(LlmModelConfig).filter(LlmModelConfig.deleted_at.is_(None)).one()
        assert config.lifecycle_status == "draft"
        assert db.query(LlmModelTestRun).one().status == "failed"


def test_bootstrap_database_or_audit_failure_is_fatal(monkeypatch, db_factory, keyring):
    cli = _cli(monkeypatch)
    monkeypatch.setattr(cli.settings, "DEEPSEEK_API_KEY", "sk-audit-secret")

    async def broken_probe(*args, **kwargs):
        raise RuntimeError("audit database unavailable")

    monkeypatch.setattr(cli, "_probe_candidate", broken_probe)

    assert cli.run_bootstrap(session_factory=db_factory, executor=RecordingExecutor()) == 1


def test_readiness_does_not_commit_caller_pending_objects(db_factory, keyring, monkeypatch):
    cli = _cli(monkeypatch)
    caller_session = db_factory()
    caller_session.add(LlmRuntimeSetting(id=1, daily_token_limit=1234))
    try:
        result = cli.run_readiness(session_factory=caller_session)
    finally:
        caller_session.close()

    assert result.exit_code == 1
    with db_factory() as db:
        assert db.query(LlmRuntimeSetting).count() == 0


def test_bootstrap_existing_model_is_noop_and_does_not_override_state(
    db_factory, keyring, monkeypatch
):
    cli = _cli(monkeypatch)
    monkeypatch.setattr(cli.settings, "DEEPSEEK_API_KEY", "sk-new-secret")
    from app.services.llm.crypto import encrypt_api_key
    from app.services.llm.config_service import runtime_fingerprint

    config_id = "existing-cli-config"
    envelope = encrypt_api_key(
        "sk-old-secret",
        config_id=config_id,
        provider=Provider.DEEPSEEK,
        keyring=cli.settings.LLM_CONFIG_ENCRYPTION_KEYS,
    )
    with db_factory() as db:
        db.add(
            LlmModelConfig(
                id=config_id,
                provider="deepseek",
                display_name="旧配置",
                model_name="deepseek-chat",
                base_url="https://api.deepseek.com/v1",
                encrypted_api_key=envelope.encrypted_api_key,
                encryption_key_id=envelope.encryption_key_id,
                envelope_version=envelope.envelope_version,
                nonce=envelope.nonce,
                runtime_fingerprint=runtime_fingerprint(
                    provider=Provider.DEEPSEEK,
                    model_name="deepseek-chat",
                    base_url="https://api.deepseek.com/v1",
                    credential_version="old-credential",
                    max_output_tokens=256,
                ),
                lifecycle_status="active",
            )
        )
        db.add(LlmRuntimeSetting(id=1, default_model_config_id=config_id))
        db.commit()
    executor = RecordingExecutor()

    result = cli.run_bootstrap(session_factory=db_factory, executor=executor)

    assert result == 0
    assert executor.calls == []
    with db_factory() as db:
        assert db.query(LlmModelConfig).count() == 1
        assert db.get(LlmRuntimeSetting, 1).default_model_config_id == config_id


def test_readiness_is_read_only_and_fails_without_verified_active_default(
    db_factory, keyring, monkeypatch
):
    cli = _cli(monkeypatch)
    with db_factory() as db:
        db.add(LlmRuntimeSetting(id=1))
        db.commit()
    monkeypatch.setattr(cli, "decrypt_api_key", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("readiness must not decrypt")))

    output = cli.run_readiness(session_factory=db_factory)

    assert output.exit_code == 1
    assert output.status == "not_ready"
    assert output.model_name is None


def test_readiness_success_only_outputs_status_and_redacted_name(db_factory, keyring, monkeypatch):
    cli = _cli(monkeypatch)
    from app.services.llm.crypto import encrypt_api_key
    from app.services.llm.config_service import runtime_fingerprint

    config_id = "ready-cli-config"
    fp = runtime_fingerprint(
        provider=Provider.DEEPSEEK,
        model_name="deepseek-chat",
        base_url="https://api.deepseek.com/v1",
        credential_version="ready-credential",
        max_output_tokens=256,
    )
    envelope = encrypt_api_key(
        "sk-ready-secret",
        config_id=config_id,
        provider=Provider.DEEPSEEK,
        keyring=cli.settings.LLM_CONFIG_ENCRYPTION_KEYS,
    )
    with db_factory() as db:
        db.add(
            LlmModelConfig(
                id=config_id,
                provider="deepseek",
                display_name="就绪配置",
                model_name="deepseek-chat",
                base_url="https://api.deepseek.com/v1",
                encrypted_api_key=envelope.encrypted_api_key,
                encryption_key_id=envelope.encryption_key_id,
                envelope_version=envelope.envelope_version,
                nonce=envelope.nonce,
                credential_version="ready-credential",
                runtime_fingerprint=fp,
                lifecycle_status="active",
                verified_test_id="ready-test",
            )
        )
        db.add(
            LlmModelTestRun(
                id="ready-test",
                model_config_id=config_id,
                runtime_fingerprint=fp,
                status="success",
                test_type="bootstrap",
            )
        )
        db.add(LlmRuntimeSetting(id=1, default_model_config_id=config_id))
        db.commit()
    monkeypatch.setattr(cli, "decrypt_api_key", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("readiness must not decrypt")))

    output = cli.run_readiness(session_factory=db_factory)

    assert output.exit_code == 0
    assert output.status == "ready"
    assert output.model_name == "就绪配置"
    assert "sk-ready-secret" not in output.rendered
    assert "api_key" not in output.rendered


def test_live_smoke_requires_matching_provider_and_uses_live_smoke_executor(
    db_factory, keyring, monkeypatch
):
    cli = _cli(monkeypatch)
    monkeypatch.setattr(cli.settings, "DEEPSEEK_API_KEY", "")
    from app.services.llm.crypto import encrypt_api_key
    from app.services.llm.config_service import runtime_fingerprint

    config_id = "smoke-cli-config"
    fp = runtime_fingerprint(
        provider=Provider.DEEPSEEK,
        model_name="deepseek-chat",
        base_url="https://api.deepseek.com/v1",
        credential_version="smoke-credential",
        max_output_tokens=256,
    )
    envelope = encrypt_api_key(
        "sk-smoke-secret",
        config_id=config_id,
        provider=Provider.DEEPSEEK,
        keyring=cli.settings.LLM_CONFIG_ENCRYPTION_KEYS,
    )
    with db_factory() as db:
        db.add(
            LlmModelConfig(
                id=config_id,
                provider="deepseek",
                display_name="Smoke 配置",
                model_name="deepseek-chat",
                base_url="https://api.deepseek.com/v1",
                encrypted_api_key=envelope.encrypted_api_key,
                encryption_key_id=envelope.encryption_key_id,
                envelope_version=envelope.envelope_version,
                nonce=envelope.nonce,
                credential_version="smoke-credential",
                runtime_fingerprint=fp,
                lifecycle_status="active",
                max_output_tokens=256,
            )
        )
        db.commit()
    mismatch = cli.run_live_smoke(
        provider="kimi", model_config_id=config_id, session_factory=db_factory
    )
    assert mismatch.exit_code != 0

    db, http_client, executor = _real_executor(db_factory)
    try:
        success = cli.run_live_smoke(
            provider="deepseek", model_config_id=config_id, session_factory=db_factory, executor=executor
        )
    finally:
        asyncio.run(http_client.aclose())
        db.close()
    assert success.exit_code == 0
    assert "sk-smoke-secret" not in success.rendered
    assert success.evidence == {
        "schema": True,
        "token_upper_bound": True,
        "provider": "deepseek",
        "model_config_id": config_id,
        "attempt": True,
        "usage": True,
        "budget": True,
    }
    with db_factory() as db:
        attempt = db.query(LlmCallAttempt).one()
        usage = db.query(LlmUsage).one()
        reservation = db.get(LlmTokenReservation, attempt.reservation_id)
        assert attempt.operation_type == "live_smoke"
        assert attempt.task_id is None
        assert usage.module == "live_smoke"
        assert reservation.status == "settled"


def test_live_smoke_rejects_released_budget_as_success_evidence(db_factory, keyring, monkeypatch):
    cli = _cli(monkeypatch)
    from app.services.llm.crypto import encrypt_api_key
    from app.services.llm.config_service import runtime_fingerprint

    config_id = "released-budget-config"
    fp = runtime_fingerprint(
        provider=Provider.DEEPSEEK,
        model_name="deepseek-chat",
        base_url="https://api.deepseek.com/v1",
        credential_version="released-credential",
        max_output_tokens=256,
    )
    envelope = encrypt_api_key(
        "sk-released-secret",
        config_id=config_id,
        provider=Provider.DEEPSEEK,
        keyring=cli.settings.LLM_CONFIG_ENCRYPTION_KEYS,
    )
    with db_factory() as db:
        db.add(
            LlmModelConfig(
                id=config_id,
                provider="deepseek",
                display_name="Released budget",
                model_name="deepseek-chat",
                base_url="https://api.deepseek.com/v1",
                encrypted_api_key=envelope.encrypted_api_key,
                encryption_key_id=envelope.encryption_key_id,
                envelope_version=envelope.envelope_version,
                nonce=envelope.nonce,
                credential_version="released-credential",
                runtime_fingerprint=fp,
                lifecycle_status="active",
                max_output_tokens=256,
            )
        )
        db.commit()
    db, http_client, executor = _real_executor(db_factory, api_key="sk-released-secret")
    executor.budget = ReleasedBudget(db, daily_token_limit=100_000)
    try:
        result = cli.run_live_smoke(
            provider="deepseek",
            model_config_id=config_id,
            session_factory=db_factory,
            executor=executor,
        )
    finally:
        asyncio.run(http_client.aclose())
        db.close()

    assert result.exit_code == 1
    assert result.status == "live_smoke_failed"


def test_compose_worker_does_not_receive_legacy_bootstrap_secrets():
    compose = (Path(__file__).resolve().parents[3] / "deploy" / "docker-compose.yml").read_text()
    worker = compose.split("\n  worker:\n", 1)[1].split("\n  nginx:\n", 1)[0]
    for name in ("DEEPSEEK_API_KEY", "LLM_MODEL", "LLM_BASE_URL"):
        assert f"{name}:" not in worker


def test_cli_argument_parser_has_explicit_commands(monkeypatch):
    cli = _cli(monkeypatch)
    parser = cli.build_parser()
    choices = parser._subparsers._group_actions[0].choices
    assert {"bootstrap", "readiness", "live-smoke"} <= set(choices)
