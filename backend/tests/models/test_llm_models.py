from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.base import Base
from app.models.llm_config import (
    LlmActivationRequest,
    LlmAdminAuditEvent,
    LlmModelConfig,
    LlmModelTestRun,
    LlmRuntimeSetting,
)
from app.models.llm_execution import (
    LlmCallAttempt,
    LlmDailyBudget,
    LlmTokenReservation,
)
from app.models.task_outbox import TaskOutbox
from app.models.task_record import TaskRecord
from app.models.llm_usage import LlmUsage


def test_model_config_defaults_to_draft_uuid_and_has_only_encrypted_secret_fields():
    config = LlmModelConfig(
        provider="kimi",
        display_name="Kimi 主模型",
        model_name="kimi-k3",
        base_url="https://api.moonshot.cn/v1",
        encrypted_api_key="ciphertext",
        encryption_key_id="current",
        envelope_version="v1",
        nonce="nonce",
        runtime_fingerprint="fingerprint",
    )

    assert config.id is not None
    # UUID strings are generated application-side before persistence, so they
    # can already be used as the credential-envelope AAD.
    assert UUID(config.id)
    assert config.lifecycle_status == "draft"
    assert config.version == 1
    assert UUID(config.credential_version)
    columns = set(LlmModelConfig.__table__.columns.keys())
    assert "api_key" not in columns
    assert {"encrypted_api_key", "nonce", "encryption_key_id"} <= columns


def test_runtime_settings_use_singleton_defaults_and_reject_second_singleton():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(LlmRuntimeSetting())
        session.commit()
        setting = session.get(LlmRuntimeSetting, 1)
        assert setting is not None
        assert setting.daily_token_limit > 0
        assert setting.budget_locked is False
        assert setting.version == 1

        session.add(LlmRuntimeSetting(id=1))
        with pytest.raises(IntegrityError):
            session.commit()


def test_scheduled_task_and_usage_user_are_nullable_and_snapshot_fields_are_present():
    task = TaskRecord(
        task_type="scheduled_news",
        user_id=None,
        model_config_id=None,
        input_snapshot={"symbol": "000001"},
        input_snapshot_hash="a" * 64,
        prompt_version="v1",
        execution_token=str(uuid4()),
    )
    usage = LlmUsage(
        user_id=None,
        module="scheduled_news",
        model="kimi-k3",
        task_id=None,
        model_config_id=None,
        provider_snapshot="kimi",
        model_snapshot="kimi-k3",
        input_price_snapshot=10,
        output_price_snapshot=20,
        input_tokens=3,
        output_tokens=5,
        cost_micro_yuan=1,
        status="success",
    )
    assert task.user_id is None
    assert usage.user_id is None
    assert task.input_snapshot_hash == "a" * 64
    assert usage.input_tokens == 3
    assert usage.output_tokens == 5

    task_columns = TaskRecord.__table__.columns
    usage_columns = LlmUsage.__table__.columns
    assert task_columns.model_config_id.nullable is True
    assert task_columns.lease_expires_at.nullable is True
    assert task_columns.heartbeat_at.nullable is True
    assert usage_columns.user_id.nullable is True
    assert usage_columns.model_config_id.nullable is True


def test_unsaved_probe_attempt_has_nullable_task_and_config_with_snapshots():
    operation_id = str(uuid4())
    attempt = LlmCallAttempt(
        task_id=None,
        model_config_id=None,
        operation_id=operation_id,
        operation_type="admin_probe",
        step_key="probe",
        attempt_no=1,
        provider_snapshot="qwen",
        model_snapshot="qwen-plus",
        runtime_fingerprint="fp-1",
        status="started",
    )

    assert attempt.operation_id == operation_id
    assert attempt.task_id is None
    assert attempt.model_config_id is None
    assert attempt.operation_type == "admin_probe"
    assert {"provider_snapshot", "model_snapshot", "runtime_fingerprint"} <= set(
        LlmCallAttempt.__table__.columns.keys()
    )


def test_attempt_task_identity_is_unique_but_probe_operations_are_independent():
    constraints = {constraint.name for constraint in LlmCallAttempt.__table__.constraints}
    assert "uq_llm_call_attempt_task_step_no" in constraints
    assert "ix_llm_call_attempts_created_config_status" in {
        index.name for index in LlmCallAttempt.__table__.indexes
    }


def test_budget_counters_are_nonnegative_and_outbox_has_pending_partial_index():
    budget_checks = {constraint.name for constraint in LlmDailyBudget.__table__.constraints}
    assert "ck_llm_daily_budgets_reserved_nonnegative" in budget_checks
    assert "ck_llm_daily_budgets_settled_nonnegative" in budget_checks
    assert "ix_task_outbox_pending_available" in {
        index.name for index in TaskOutbox.__table__.indexes
    }


def test_all_model_center_entities_are_registered_with_metadata():
    expected = {
        "llm_model_configs",
        "llm_runtime_settings",
        "llm_model_test_runs",
        "llm_activation_requests",
        "llm_admin_audit_events",
        "llm_daily_budgets",
        "llm_token_reservations",
        "llm_call_attempts",
        "task_outbox",
    }
    assert expected <= set(Base.metadata.tables)
