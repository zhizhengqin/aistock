"""PostgreSQL/Redis recovery and execution-token integration evidence."""

from __future__ import annotations

import asyncio
from datetime import date

import pytest
from sqlalchemy import update

from app.models.llm_execution import LlmDailyBudget, LlmTokenReservation
from app.models.task_record import TaskRecord
from app.services.llm.budget import TokenBudgetService
from app.services.task_execution import TaskExecutionFenced, TaskExecutionRunner


@pytest.mark.integration
def test_budget_persists_across_sessions(postgres_session_factory):
    budget_date = date(2099, 1, 2)
    with postgres_session_factory() as db:
        db.add(LlmDailyBudget(budget_date=budget_date))
        db.commit()
        reservation = TokenBudgetService(db, daily_token_limit=1000).reserve(
            120,
            step_key="integration-budget",
            budget_date=budget_date,
        )
        TokenBudgetService(db).settle(reservation.id, actual_tokens=90)

    with postgres_session_factory() as db:
        ledger = db.get(LlmDailyBudget, budget_date)
        row = db.get(LlmTokenReservation, reservation.id)
        assert ledger.reserved_tokens == 0
        assert ledger.settled_tokens == 90
        assert row.status == "settled"
        assert row.settled_tokens == 90


@pytest.mark.integration
def test_execution_token_fences_reclaimed_worker(postgres_session_factory):
    with postgres_session_factory() as db:
        task = TaskRecord(task_type="news_collect", status="pending", input_snapshot={"_args": {}})
        db.add(task)
        db.commit()
        task_id = int(task.id)

    runner = TaskExecutionRunner(postgres_session_factory, lease_seconds=60)
    claim = runner._claim(task_id)
    assert claim.context is not None
    context = claim.context

    with postgres_session_factory() as db:
        db.execute(
            update(TaskRecord)
            .where(TaskRecord.id == task_id)
            .values(execution_token="new-owner", status="running")
        )
        db.commit()

    with pytest.raises(TaskExecutionFenced):
        asyncio.run(context.ensure_current())

    with postgres_session_factory() as db:
        row = db.get(TaskRecord, task_id)
        assert row.execution_token == "new-owner"


@pytest.mark.integration
def test_isolated_redis_server_restart_reconnects(isolated_redis_server):
    """Restart only the disposable Redis process, never the shared test URL."""

    import redis

    client = redis.Redis.from_url(isolated_redis_server.url, decode_responses=True)
    key = "task12:restart"
    try:
        client.set(key, "before", ex=30)
        assert client.get(key) == "before"
        isolated_redis_server.stop()
        isolated_redis_server.start()
        assert client.ping() is True
        client.set(key, "after", ex=30)
        assert client.get(key) == "after"
    finally:
        client.close()
