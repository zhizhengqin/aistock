"""Behavior tests for reliable task outbox dispatch."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.models.task_outbox import TaskOutbox
from app.models.task_record import TaskRecord


class FakeSender:
    def __init__(self):
        self.jobs = []

    async def enqueue_job(self, task_name, *args, _job_id=None, **kwargs):
        self.jobs.append((task_name, args, _job_id))
        return object()


def _task(db, task_type="stock_analysis", **snapshot):
    task = TaskRecord(
        task_type=task_type,
        user_id=1,
        status="pending",
        input_snapshot={"_args": snapshot},
    )
    db.add(task)
    db.flush()
    db.add(TaskOutbox(task_id=task.id))
    db.commit()
    return task


@pytest.mark.asyncio
async def test_dispatch_once_maps_snapshot_and_uses_deterministic_job_id(test_db):
    from app.services.outbox_dispatcher import OutboxDispatcher

    _, session_factory = test_db
    db = session_factory()
    task = _task(db, stock_code="600519", user_id=1)
    sender = FakeSender()

    dispatched = await OutboxDispatcher(session_factory, sender=sender).dispatch_once()

    assert dispatched == 1
    assert sender.jobs == [("analyze_stock_task", (task.id, "600519", 1), f"task:{task.id}")]
    check = db.get(TaskOutbox, task.id)
    assert check.status == "delivered"
    assert check.last_error is None
    db.close()


@pytest.mark.asyncio
async def test_dispatch_failure_releases_lock_with_redacted_backoff(test_db):
    from app.services.outbox_dispatcher import OutboxDispatcher

    class FailingSender:
        async def enqueue_job(self, *args, **kwargs):
            raise RuntimeError("authorization=top-secret")

    _, session_factory = test_db
    db = session_factory()
    task = _task(db, stock_code="000001", user_id=1)
    dispatcher = OutboxDispatcher(session_factory, sender=FailingSender(), base_backoff_seconds=2)

    assert await dispatcher.dispatch_once() == 0

    check = db.get(TaskOutbox, task.id)
    assert check.status == "pending"
    assert check.locked_at is None
    assert check.locked_by is None
    assert check.attempts == 1
    available_at = check.available_at
    if available_at.tzinfo is None:
        available_at = available_at.replace(tzinfo=timezone.utc)
    assert available_at > datetime.now(timezone.utc) - timedelta(seconds=1)
    assert "top-secret" not in (check.last_error or "")
    db.close()


@pytest.mark.asyncio
async def test_dispatch_recovers_stale_lock(test_db):
    from app.services.outbox_dispatcher import OutboxDispatcher

    _, session_factory = test_db
    db = session_factory()
    task = _task(db, stock_code="300750", user_id=1)
    row = db.get(TaskOutbox, task.id)
    row.status = "locked"
    row.locked_at = datetime.now(timezone.utc) - timedelta(minutes=10)
    row.locked_by = "dead-worker"
    db.commit()
    sender = FakeSender()

    assert await OutboxDispatcher(session_factory, sender=sender, lock_timeout_seconds=30).dispatch_once() == 1
    assert sender.jobs[0][2] == f"task:{task.id}"
    db.close()
