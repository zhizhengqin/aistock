"""Behavior tests for atomic task submission and model locking."""

from __future__ import annotations

import asyncio

import pytest

from app.models.llm_config import LlmModelConfig, LlmModelTestRun, LlmRuntimeSetting
from app.models.task_outbox import TaskOutbox
from app.models.task_record import TaskRecord
from app.models.usage_log import UsageLog
from app.models.user import User


def _user(db, *, tier: str = "A") -> User:
    user = User(
        username=f"submission-{tier.lower()}",
        email=f"submission-{tier.lower()}@example.com",
        password_hash="test",
        tier=tier,
        role="user",
        is_active=True,
    )
    db.add(user)
    db.flush()
    return user


def _active_default(db) -> LlmModelConfig:
    from app.services.llm.config_service import runtime_fingerprint

    config = LlmModelConfig(
        provider="deepseek",
        display_name="测试默认模型",
        model_name="deepseek-chat",
        base_url="https://api.deepseek.com/v1",
        encrypted_api_key="ciphertext",
        encryption_key_id="test",
        envelope_version="v1",
        nonce="nonce",
        runtime_fingerprint="pending",
        lifecycle_status="active",
    )
    config.runtime_fingerprint = runtime_fingerprint(
        provider=config.provider,
        model_name=config.model_name,
        base_url=config.base_url,
        credential_version=config.credential_version,
        max_output_tokens=config.max_output_tokens or 4096,
    )
    db.add(config)
    db.flush()
    run = LlmModelTestRun(
        model_config_id=config.id,
        runtime_fingerprint=config.runtime_fingerprint,
        status="success",
        test_type="probe",
    )
    db.add(run)
    db.flush()
    config.verified_test_id = run.id
    db.add(LlmRuntimeSetting(default_model_config_id=config.id))
    db.flush()
    return config


def test_membership_check_and_consume_uses_caller_transaction(test_db):
    from app.services import membership

    _, session_factory = test_db
    db = session_factory()
    user = _user(db)
    db.commit()
    original_commit = db.commit

    def unexpected_commit():
        raise AssertionError("membership helper must not commit")

    db.commit = unexpected_commit
    membership.check_and_consume(db, user, "stock_analysis")
    db.commit = original_commit
    db.rollback()
    db.close()


def _submission(*, user_id: int | None, requires_llm: bool = True):
    from app.services.task_submission import TaskSubmission

    return TaskSubmission(
        task_type="stock_analysis" if requires_llm else "news_collect",
        user_id=user_id,
        feature="stock_analysis" if user_id is not None else None,
        feature_cost=1 if user_id is not None else 0,
        args={"stock_code": "600519", "user_id": user_id},
        input_snapshot={
            "stock_code": "600519",
            "credentials": {"api_key": "must-not-persist", "nested": [{"token": "secret"}]},
        },
        prompt_version="v1",
        requires_llm=requires_llm,
    )


def test_submit_is_atomic_when_outbox_creation_fails(test_db, monkeypatch):
    from app.services.task_submission import TaskSubmissionService

    _, session_factory = test_db
    db = session_factory()
    user = _user(db)
    _active_default(db)
    db.commit()

    def explode(*args, **kwargs):
        raise RuntimeError("outbox insert failed")

    monkeypatch.setattr(TaskOutbox, "__init__", explode)
    with pytest.raises(RuntimeError, match="outbox insert failed"):
        TaskSubmissionService(db).submit(_submission(user_id=user.id))

    db.rollback()
    assert db.query(TaskRecord).count() == 0
    assert db.query(UsageLog).count() == 0
    assert db.query(TaskOutbox).count() == 0
    db.close()


def test_ai_submission_without_verified_default_writes_nothing(test_db):
    from app.services.task_submission import TaskSubmissionService, TaskSubmissionError

    _, session_factory = test_db
    db = session_factory()
    user = _user(db)
    db.add(LlmRuntimeSetting())
    db.commit()

    with pytest.raises(TaskSubmissionError) as exc_info:
        TaskSubmissionService(db).submit(_submission(user_id=user.id))

    assert exc_info.value.code == "llm_not_configured"
    db.rollback()
    assert db.query(TaskRecord).count() == 0
    assert db.query(UsageLog).count() == 0
    assert db.query(TaskOutbox).count() == 0
    db.close()


def test_verified_test_from_another_config_is_rejected_atomically(test_db):
    from app.services.task_submission import TaskSubmissionError, TaskSubmissionService

    _, session_factory = test_db
    db = session_factory()
    user = _user(db)
    config = _active_default(db)
    other = LlmModelConfig(
        provider=config.provider,
        display_name="另一个配置",
        model_name=config.model_name,
        base_url=config.base_url,
        encrypted_api_key="other-ciphertext",
        encryption_key_id=config.encryption_key_id,
        envelope_version=config.envelope_version,
        nonce="other-nonce",
        credential_version=config.credential_version,
        runtime_fingerprint=config.runtime_fingerprint,
        lifecycle_status="active",
    )
    db.add(other)
    db.flush()
    foreign_run = LlmModelTestRun(
        model_config_id=other.id,
        runtime_fingerprint=config.runtime_fingerprint,
        status="success",
        test_type="probe",
    )
    db.add(foreign_run)
    db.flush()
    config.verified_test_id = foreign_run.id
    db.commit()

    with pytest.raises(TaskSubmissionError) as exc_info:
        TaskSubmissionService(db).submit(_submission(user_id=user.id))

    assert exc_info.value.code == "llm_not_configured"
    db.rollback()
    assert db.query(TaskRecord).count() == 0
    assert db.query(UsageLog).count() == 0
    assert db.query(TaskOutbox).count() == 0
    db.close()


def test_news_submission_does_not_require_default_model(test_db):
    from app.services.task_submission import TaskSubmissionService

    _, session_factory = test_db
    db = session_factory()
    user = _user(db)
    db.add(LlmRuntimeSetting())
    db.commit()

    result = TaskSubmissionService(db).submit(_submission(user_id=user.id, requires_llm=False))

    assert result.task.model_config_id is None
    assert result.task.input_snapshot["credentials"]["api_key"] == "[REDACTED]"
    assert result.task.input_snapshot["credentials"]["nested"][0]["token"] == "[REDACTED]"
    assert len(result.task.input_snapshot_hash) == 64
    assert db.query(UsageLog).count() == 1
    assert db.query(TaskOutbox).count() == 1
    db.close()


def test_submit_batch_deducts_total_cost_once(test_db):
    from app.services.task_submission import TaskSubmission, TaskSubmissionService

    _, session_factory = test_db
    db = session_factory()
    user = _user(db, tier="C")
    _active_default(db)
    db.commit()
    submissions = [
        TaskSubmission(
            task_type="stock_analysis",
            user_id=user.id,
            feature="stock_analysis",
            feature_cost=1,
            args={"stock_code": code, "user_id": user.id},
            input_snapshot={"stock_code": code},
            prompt_version="v1",
        )
        for code in ("600519", "000001")
    ]

    result = TaskSubmissionService(db).submit_batch(submissions)

    assert [task.task_type for task in result.tasks] == ["stock_analysis", "stock_analysis"]
    usage = db.query(UsageLog).one()
    assert usage.count == 2
    assert db.query(TaskOutbox).count() == 2
    db.close()


@pytest.mark.asyncio
async def test_inline_is_acknowledged_only_after_schedule_succeeds(test_db):
    from app.services.task_submission import schedule_inline_after_commit

    _, session_factory = test_db
    db = session_factory()
    user = _user(db)
    db.add(LlmRuntimeSetting())
    db.commit()
    result = __import__("app.services.task_submission", fromlist=["TaskSubmissionService"]).TaskSubmissionService(db).submit(
        _submission(user_id=user.id, requires_llm=False)
    )

    ran = asyncio.Event()

    async def inline_task(ctx, task_id):
        ran.set()

    assert await schedule_inline_after_commit(db, result, inline_task) is True
    await asyncio.wait_for(ran.wait(), timeout=1)
    db.expire_all()
    assert db.get(TaskOutbox, result.task.id).status == "delivered"
    db.close()
