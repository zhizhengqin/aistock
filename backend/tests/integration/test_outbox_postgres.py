"""PostgreSQL/Redis integration evidence for durable task delivery."""

from __future__ import annotations

import asyncio
import pytest
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError

from app.models.llm_config import LlmModelConfig, LlmRuntimeSetting
from app.models.task_outbox import TaskOutbox
from app.models.task_record import TaskRecord
from app.services.outbox_dispatcher import OutboxDispatcher


class RecordingSender:
    def __init__(self):
        self.jobs: list[tuple[str, tuple[object, ...], str | None]] = []

    async def enqueue_job(self, name, *args, _job_id=None, **kwargs):
        self.jobs.append((name, args, _job_id))
        return object()


@pytest.mark.integration
def test_postgres_outbox_constraints_and_duplicate_delivery(
    postgres_engine,
    postgres_session_factory,
):
    """A task has one outbox row and duplicate delivery has one ARQ id."""

    with postgres_session_factory() as db:
        task = TaskRecord(
            task_type="stock_analysis",
            status="pending",
            input_snapshot={"_args": {"stock_code": "600519", "user_id": None}},
        )
        db.add(task)
        db.flush()
        db.add(TaskOutbox(task_id=task.id))
        db.commit()
        task_id = int(task.id)

        db.add(TaskOutbox(task_id=task_id))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()

    indexes = {item["name"] for item in inspect(postgres_engine).get_indexes("task_outbox")}
    assert "ix_task_outbox_pending_available" in indexes

    sender = RecordingSender()
    dispatcher = OutboxDispatcher(
        postgres_session_factory,
        sender=sender,
        worker_id="integration-worker",
    )
    assert asyncio.run(dispatcher.dispatch_once()) == 1
    assert asyncio.run(dispatcher.dispatch_once()) == 0
    assert sender.jobs == [("analyze_stock_task", (task_id, "600519", None), f"task:{task_id}")]

    with postgres_session_factory() as db:
        outbox = db.execute(select(TaskOutbox).where(TaskOutbox.task_id == task_id)).scalar_one()
        assert outbox.status == "delivered"
        assert outbox.attempts == 1


@pytest.mark.integration
def test_postgres_api_and_worker_read_same_default_model(postgres_session_factory):
    """The singleton runtime setting is a shared PostgreSQL source of truth."""

    with postgres_session_factory() as db:
        model = LlmModelConfig(
            id="cfg-integration",
            provider="deepseek",
            display_name="Integration model",
            model_name="deepseek-chat",
            base_url="https://api.deepseek.com/v1",
            encrypted_api_key="ciphertext",
            encryption_key_id="integration",
            envelope_version="v1",
            nonce="nonce",
            runtime_fingerprint="fingerprint",
        )
        db.add(model)
        db.flush()
        setting = LlmRuntimeSetting(default_model_config_id=model.id, daily_token_limit=1000)
        db.add(setting)
        db.commit()

    with postgres_session_factory() as api_db, postgres_session_factory() as worker_db:
        api_value = api_db.get(LlmRuntimeSetting, 1).default_model_config_id
        worker_value = worker_db.get(LlmRuntimeSetting, 1).default_model_config_id
    assert api_value == worker_value == "cfg-integration"


@pytest.mark.integration
def test_redis_reconnect_preserves_queue_namespace(redis_client):
    """A dropped Redis connection reconnects without changing durable semantics."""

    key = "aistock:integration:redis-reconnect"
    redis_client.set(key, "ready", ex=30)
    assert redis_client.get(key) == "ready"
    redis_client.connection_pool.disconnect()
    assert redis_client.ping() is True
    assert redis_client.get(key) == "ready"
    redis_client.delete(key)


@pytest.mark.integration
def test_migration_cycle_keeps_existing_task_rows(migration_cycle):
    """The disposable PostgreSQL database survives upgrade/downgrade/upgrade."""

    migration_cycle.upgrade()
    migration_cycle.seed_legacy_task()
    migration_cycle.downgrade()
    migration_cycle.upgrade()
    assert migration_cycle.legacy_task_exists()
