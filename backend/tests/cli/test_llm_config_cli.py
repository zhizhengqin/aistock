"""Behavior coverage for the one-shot LLM bootstrap/readiness/smoke CLI."""

from __future__ import annotations

import asyncio
import base64
import json

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
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


def _real_executor(
    db_factory,
    *,
    api_key="sk-smoke-secret",
    expected_max_tokens=256,
    status_code=200,
):
    db = db_factory()

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == f"Bearer {api_key}"
        body = request.read()
        assert json.loads(body)["max_tokens"] == expected_max_tokens
        if status_code != 200:
            return httpx.Response(status_code, json={"error": {"message": "invalid key"}}, request=request)
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
    executor = RecordingExecutor(
        error=LlmError("模型密钥无效，请管理员检查配置", code="llm_auth_failed")
    )

    result = cli.run_bootstrap(session_factory=db_factory, executor=executor)

    assert result == 0
    assert notifications
    assert "sk-cli-secret" not in repr(notifications)
    with db_factory() as db:
        config = db.query(LlmModelConfig).one()
        assert config.lifecycle_status == "draft"
        assert db.get(LlmRuntimeSetting, 1).default_model_config_id is None
        assert db.query(LlmModelTestRun).one().status == "failed"


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


def test_cli_argument_parser_has_explicit_commands(monkeypatch):
    cli = _cli(monkeypatch)
    parser = cli.build_parser()
    choices = parser._subparsers._group_actions[0].choices
    assert {"bootstrap", "readiness", "live-smoke"} <= set(choices)
