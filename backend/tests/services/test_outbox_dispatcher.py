"""Behavior tests for reliable task outbox dispatch."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.models.task_outbox import TaskOutbox
from app.models.task_record import TaskRecord
from app.models.analysis_report import AnalysisReport  # noqa: F401
from app.models.user import User


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
async def test_sender_returning_none_still_marks_outbox_delivered(test_db):
    from app.services.outbox_dispatcher import OutboxDispatcher

    class NoneSender:
        async def enqueue_job(self, *args, **kwargs):
            return None

    _, session_factory = test_db
    db = session_factory()
    task = _task(db, stock_code="000858", user_id=1)

    assert await OutboxDispatcher(session_factory, sender=NoneSender()).dispatch_once() == 1

    db.expire_all()
    assert db.get(TaskOutbox, task.id).status == "delivered"
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


@pytest.mark.asyncio
async def test_enqueue_success_ack_failure_is_retried_after_stale_recovery(test_db, monkeypatch):
    from app.services.outbox_dispatcher import OutboxDispatcher

    _, session_factory = test_db
    db = session_factory()
    task = _task(db, stock_code="601318", user_id=1)
    sender = FakeSender()
    first = OutboxDispatcher(session_factory, sender=sender, lock_timeout_seconds=1)

    def fail_ack(_outbox_id):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(first, "_mark_success", fail_ack)
    with pytest.raises(RuntimeError, match="database unavailable"):
        await first.dispatch_once()

    db.expire_all()
    locked = db.get(TaskOutbox, task.id)
    assert locked.status == "locked"
    assert sender.jobs == [("analyze_stock_task", (task.id, "601318", 1), f"task:{task.id}")]

    locked.locked_at = datetime.now(timezone.utc) - timedelta(seconds=10)
    db.commit()
    second = OutboxDispatcher(session_factory, sender=sender, lock_timeout_seconds=1)
    assert await second.dispatch_once() == 1

    assert len(sender.jobs) == 2
    assert sender.jobs[0][2] == sender.jobs[1][2] == f"task:{task.id}"
    db.expire_all()
    assert db.get(TaskOutbox, task.id).status == "delivered"
    db.close()


@pytest.mark.asyncio
async def test_ack_gap_replays_dispatch_but_runner_executes_once(test_db, monkeypatch):
    """A real dispatcher ack gap invokes the wrapper twice but persists once."""
    from app.services.outbox_dispatcher import OutboxDispatcher
    from app.tasks import analysis as analysis_module
    from app.services import analysis_orchestrator

    _, session_factory = test_db
    db = session_factory()
    user = User(
        username="ack-gap-runner",
        email="ack-gap-runner@example.test",
        password_hash="test-only",
        tier="free",
        role="user",
        is_active=True,
    )
    db.add(user)
    db.flush()
    task = TaskRecord(
        task_type="stock_analysis",
        user_id=user.id,
        status="pending",
        input_snapshot={"_args": {"stock_code": "600519", "user_id": user.id}},
        prompt_version="stock-analysis-v1",
    )
    db.add(task)
    db.flush()
    db.add(TaskOutbox(task_id=task.id))
    db.commit()
    task_id = task.id
    user_id = user.id
    db.close()

    monkeypatch.setattr(analysis_module, "SessionLocal", session_factory)
    calls = 0

    async def fake_orchestrator(*args, **kwargs):
        nonlocal calls
        calls += 1
        return {
            "stock_code": "600519",
            "stock_name": "测试",
            "decision": {"rating": "观察", "confidence": 50},
        }

    monkeypatch.setattr(analysis_orchestrator, "run_full_analysis", fake_orchestrator)

    class WrapperSender:
        def __init__(self):
            self.jobs = 0

        async def enqueue_job(self, task_name, *args, _job_id=None, **kwargs):
            self.jobs += 1
            assert task_name == "analyze_stock_task"
            await analysis_module.analyze_stock_task(None, *args)

    sender = WrapperSender()
    first = OutboxDispatcher(session_factory, sender=sender, lock_timeout_seconds=1)
    original_mark_success = first._mark_success

    def fail_ack_once(outbox_id):
        first._mark_success = original_mark_success
        raise RuntimeError("ack database unavailable")

    first._mark_success = fail_ack_once
    with pytest.raises(RuntimeError, match="ack database unavailable"):
        await first.dispatch_once()

    db = session_factory()
    outbox = db.get(TaskOutbox, task_id)
    outbox.locked_at = datetime.now(timezone.utc) - timedelta(seconds=10)
    db.commit()
    db.close()

    second = OutboxDispatcher(session_factory, sender=sender, lock_timeout_seconds=1)
    assert await second.dispatch_once() == 1
    assert sender.jobs == 2
    assert calls == 1
    db = session_factory()
    assert db.get(TaskRecord, task_id).status == "success"
    assert db.query(AnalysisReport).count() == 1
    assert db.get(TaskOutbox, task_id).status == "delivered"
    db.close()
