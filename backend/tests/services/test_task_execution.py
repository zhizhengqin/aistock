"""Focused behavior tests for the short-transaction execution runner."""

import asyncio
from datetime import date, datetime, timedelta, timezone
import time

import pytest

from app.models.llm_execution import LlmCallAttempt, LlmDailyBudget, LlmTokenReservation
from app.models.llm_usage import LlmUsage
from app.models.analysis_report import AnalysisReport  # noqa: F401
from app.models.task_outbox import TaskOutbox
from app.models.task_record import TaskRecord
from app.models.user import User
from app.services.task_execution import (
    TaskExecutionFenced,
    TaskExecutionInputError,
    TaskExecutionRunner,
    TaskExecutionContext,
    validate_snapshot_args,
)


def _task(db) -> int:
    task = TaskRecord(
        task_type="stock_analysis",
        user_id=None,
        status="pending",
        input_snapshot={"_args": {"stock_code": "600519", "user_id": 1}},
        prompt_version="test-v1",
    )
    db.add(task)
    db.flush()
    db.commit()
    return int(task.id)


class MutableClock:
    def __init__(self, value: datetime | None = None):
        self.value = value or datetime.now(timezone.utc)

    def __call__(self):
        return self.value

    def advance(self, **kwargs):
        self.value += timedelta(**kwargs)


@pytest.mark.asyncio
async def test_duplicate_wrapper_delivery_executes_once(test_db):
    """Two deliveries for one task have one durable claim and one execution."""
    _, session_factory = test_db
    db = session_factory()
    task_id = _task(db)
    db.close()

    calls = 0
    persisted = 0

    async def execute(ctx):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0)
        return {"report_id": 7}

    def persist_result(db, task, result):
        nonlocal persisted
        persisted += 1

    runner = TaskExecutionRunner(session_factory, heartbeat_interval_seconds=60)
    assert await runner.run(task_id, execute, persist_result) == {"report_id": 7}
    assert await runner.run(task_id, execute, persist_result) is None

    check = session_factory()
    row = check.get(TaskRecord, task_id)
    assert calls == 1
    assert persisted == 1
    assert row.status == "success"
    assert row.progress == 100
    check.close()


@pytest.mark.asyncio
async def test_inline_success_then_outbox_ack_gap_is_terminal_noop(test_db):
    """A pending outbox row after inline completion cannot replay the task."""
    _, session_factory = test_db
    db = session_factory()
    task_id = _task(db)
    db.add(TaskOutbox(task_id=task_id, status="pending"))
    db.commit()
    db.close()

    calls = 0

    async def execute(ctx):
        nonlocal calls
        calls += 1
        return {"ok": True}

    runner = TaskExecutionRunner(session_factory, heartbeat_interval_seconds=60)
    await runner.run(task_id, execute, lambda db, task, result: None)
    # Simulate the Task 6 ack gap: the committed outbox row is still pending.
    assert await runner.run(task_id, execute, lambda db, task, result: None) is None
    assert calls == 1


@pytest.mark.asyncio
async def test_business_await_has_no_session_open(test_db):
    """The runner closes claim sessions before awaiting business work."""
    _, session_factory = test_db
    db = session_factory()
    task_id = _task(db)
    db.close()

    active = 0

    def tracked_factory():
        nonlocal active
        session = session_factory()
        active += 1
        original_close = session.close

        def close():
            nonlocal active
            if active:
                active -= 1
            return original_close()

        session.close = close
        return session

    async def execute(ctx):
        assert active == 0
        await asyncio.sleep(0)
        assert active == 0
        return {"done": True}

    runner = TaskExecutionRunner(tracked_factory, heartbeat_interval_seconds=60)
    await runner.run(task_id, execute, lambda db, task, result: None)
    assert active == 0


@pytest.mark.asyncio
async def test_active_running_delivery_is_noop(test_db):
    _, session_factory = test_db
    db = session_factory()
    task_id = _task(db)
    db.close()

    first_started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def execute(ctx):
        nonlocal calls
        calls += 1
        first_started.set()
        await release.wait()
        return {"ok": True}

    runner = TaskExecutionRunner(session_factory, lease_seconds=30, heartbeat_interval_seconds=60)
    first = asyncio.create_task(runner.run(task_id, execute, lambda db, task, result: None))
    await first_started.wait()
    assert await runner.run(task_id, execute, lambda db, task, result: None) is None
    release.set()
    await first
    assert calls == 1


@pytest.mark.asyncio
async def test_heartbeat_renews_lease_with_current_token_and_can_be_cancelled(test_db):
    _, session_factory = test_db
    db = session_factory()
    task_id = _task(db)
    db.close()

    runner = TaskExecutionRunner(session_factory, lease_seconds=10, heartbeat_interval_seconds=0.01)
    claim = runner._claim(task_id)
    assert claim.context is not None
    context = claim.context
    before = session_factory().get(TaskRecord, task_id).lease_expires_at
    await context.heartbeat()
    check = session_factory().get(TaskRecord, task_id)
    assert check.heartbeat_at is not None
    assert check.lease_expires_at >= before
    await context.set_progress(42)
    check = session_factory().get(TaskRecord, task_id)
    assert check.progress == 42
    # The private loop is cancelled by run's finally; explicit cancellation
    # must also complete cleanly without changing terminal state.
    stop = asyncio.Event()
    heartbeat = asyncio.create_task(runner._heartbeat_loop(context, stop))
    await asyncio.sleep(0)
    heartbeat.cancel()
    await heartbeat


@pytest.mark.asyncio
async def test_stale_running_task_is_reclaimed_with_new_token(test_db):
    _, session_factory = test_db
    clock = MutableClock()
    db = session_factory()
    task_id = _task(db)
    db.close()
    runner = TaskExecutionRunner(session_factory, lease_seconds=5, clock=clock)
    first = runner._claim(task_id).context
    assert first is not None
    clock.advance(seconds=6)
    second = runner._claim(task_id).context
    assert second is not None
    assert second.execution_token != first.execution_token


@pytest.mark.asyncio
async def test_started_attempt_blocks_reclaim_and_settles_reservation(test_db):
    """An expired task with an in-flight provider attempt must not replay."""
    _, session_factory = test_db
    clock = MutableClock()
    db = session_factory()
    task_id = _task(db)
    task = db.get(TaskRecord, task_id)
    task.status = "running"
    task.execution_token = "old-owner"
    task.lease_expires_at = clock() - timedelta(seconds=1)
    budget = LlmDailyBudget(budget_date=clock().date(), reserved_tokens=20, settled_tokens=0)
    db.add(budget)
    db.flush()
    reservation = LlmTokenReservation(
        task_id=task_id,
        step_key="analysis",
        budget_date=clock().date(),
        reserved_tokens=20,
        settled_tokens=0,
        status="reserved",
        lease_expires_at=clock() - timedelta(seconds=1),
    )
    db.add(reservation)
    db.flush()
    reservation_id = reservation.id
    db.add(
        LlmCallAttempt(
            task_id=task_id,
            operation_type="task",
            step_key="analysis",
            provider_snapshot="deepseek",
            model_snapshot="deepseek-chat",
            runtime_fingerprint="fp",
            reservation_id=reservation.id,
            status="started",
        )
    )
    db.commit()
    db.close()
    calls = 0

    async def execute(ctx):
        nonlocal calls
        calls += 1
        return {"must_not": "replay"}

    runner = TaskExecutionRunner(session_factory, lease_seconds=5, clock=clock)
    assert await runner.run(task_id, execute, lambda db, task, result: None) is None
    assert calls == 0
    check = session_factory()
    task = check.get(TaskRecord, task_id)
    attempt = check.query(LlmCallAttempt).filter_by(task_id=task_id).one()
    settled = check.get(LlmTokenReservation, reservation_id)
    ledger = check.get(LlmDailyBudget, clock().date())
    usage = check.query(LlmUsage).filter_by(task_id=task_id).one()
    assert task.status == "failed_unknown"
    assert attempt.status == "failed_unknown"
    assert usage.status == "failed_unknown"
    assert usage.error_code == "llm_failed_unknown"
    assert settled.status == "settled"
    assert settled.settled_tokens == 20
    assert ledger.reserved_tokens == 0
    assert ledger.settled_tokens == 20
    check.close()


@pytest.mark.asyncio
async def test_failed_unknown_attempt_blocks_reclaim_without_execute(test_db):
    _, session_factory = test_db
    db = session_factory()
    task_id = _task(db)
    db.add(
        LlmCallAttempt(
            task_id=task_id,
            operation_type="task",
            step_key="analysis",
            provider_snapshot="deepseek",
            model_snapshot="deepseek-chat",
            runtime_fingerprint="fp",
            status="failed_unknown",
            error_code="llm_failed_unknown",
        )
    )
    db.commit()
    db.close()
    calls = 0

    async def execute(ctx):
        nonlocal calls
        calls += 1
        return {"bad": True}

    runner = TaskExecutionRunner(session_factory)
    assert await runner.run(task_id, execute, lambda db, task, result: None) is None
    assert calls == 0
    check = session_factory().get(TaskRecord, task_id)
    assert check.status == "failed_unknown"
    assert "重放" in check.error


@pytest.mark.asyncio
async def test_domain_error_is_stable_and_redacted(test_db):
    _, session_factory = test_db
    db = session_factory()
    task_id = _task(db)
    db.close()

    class DomainError(Exception):
        code = "provider_unavailable"
        user_message = "模型服务暂时不可用"

    async def execute(ctx):
        raise DomainError("api_key=sk-secret upstream response=private")

    runner = TaskExecutionRunner(session_factory)
    with pytest.raises(DomainError):
        await runner.run(task_id, execute, lambda db, task, result: None)
    check = session_factory().get(TaskRecord, task_id)
    assert check.status == "failed"
    assert check.error == "provider_unavailable: 模型服务暂时不可用"
    assert "sk-secret" not in (check.error or "")
    assert "private" not in (check.error or "")


@pytest.mark.asyncio
async def test_cancelled_execution_is_marked_failed_and_propagates(test_db):
    _, session_factory = test_db
    db = session_factory()
    task_id = _task(db)
    db.close()
    started = asyncio.Event()

    async def execute(ctx):
        started.set()
        await asyncio.Event().wait()

    runner = TaskExecutionRunner(session_factory, heartbeat_interval_seconds=60)
    running = asyncio.create_task(runner.run(task_id, execute, lambda db, task, result: None))
    await started.wait()
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running
    check = session_factory().get(TaskRecord, task_id)
    assert check.status == "failed"


@pytest.mark.asyncio
async def test_heartbeat_failure_is_not_silently_ignored(test_db, monkeypatch):
    _, session_factory = test_db
    db = session_factory()
    task_id = _task(db)
    db.close()

    async def broken_heartbeat(task_id, token):
        raise RuntimeError("heartbeat storage unavailable")

    runner = TaskExecutionRunner(session_factory, lease_seconds=1, heartbeat_interval_seconds=0.01)
    monkeypatch.setattr(runner, "heartbeat", broken_heartbeat)

    async def execute(ctx):
        await asyncio.sleep(0.05)
        return {"not": "committed"}

    with pytest.raises(RuntimeError, match="heartbeat storage unavailable"):
        await runner.run(task_id, execute, lambda db, task, result: None)
    check = session_factory().get(TaskRecord, task_id)
    assert check.status == "failed"
    assert check.result_json is None


@pytest.mark.asyncio
async def test_heartbeat_fence_cancels_blocked_execute_immediately(test_db, monkeypatch):
    _, session_factory = test_db
    db = session_factory()
    task_id = _task(db)
    db.close()
    cancelled = asyncio.Event()
    subsequent_steps = 0
    runner = TaskExecutionRunner(session_factory, lease_seconds=1, heartbeat_interval_seconds=0.01)

    async def fenced_heartbeat(task_id, token):
        raise TaskExecutionFenced("任务执行权已变更")

    monkeypatch.setattr(runner, "heartbeat", fenced_heartbeat)

    async def execute(ctx):
        nonlocal subsequent_steps
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise
        subsequent_steps += 1

    task = asyncio.create_task(runner.run(task_id, execute, lambda db, task, result: None))
    await asyncio.wait_for(cancelled.wait(), timeout=0.3)
    with pytest.raises(TaskExecutionFenced):
        await task
    assert subsequent_steps == 0


@pytest.mark.asyncio
async def test_heartbeat_storage_error_cancels_blocked_execute_immediately(test_db, monkeypatch):
    _, session_factory = test_db
    db = session_factory()
    task_id = _task(db)
    db.close()
    cancelled = asyncio.Event()
    runner = TaskExecutionRunner(session_factory, lease_seconds=1, heartbeat_interval_seconds=0.01)

    async def broken_heartbeat(task_id, token):
        raise RuntimeError("heartbeat storage unavailable")

    monkeypatch.setattr(runner, "heartbeat", broken_heartbeat)

    async def execute(ctx):
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    task = asyncio.create_task(runner.run(task_id, execute, lambda db, task, result: None))
    await asyncio.wait_for(cancelled.wait(), timeout=0.3)
    with pytest.raises(RuntimeError, match="heartbeat storage unavailable"):
        await task


@pytest.mark.asyncio
async def test_cancel_with_failed_unknown_attempt_marks_unknown_terminal(test_db):
    _, session_factory = test_db
    db = session_factory()
    task_id = _task(db)
    db.close()
    started = asyncio.Event()

    async def execute(ctx):
        started.set()
        await asyncio.Event().wait()

    runner = TaskExecutionRunner(session_factory, heartbeat_interval_seconds=60)
    task = asyncio.create_task(runner.run(task_id, execute, lambda db, task, result: None))
    await started.wait()
    db = session_factory()
    db.add(
        LlmCallAttempt(
            task_id=task_id,
            operation_type="task",
            step_key="analysis",
            provider_snapshot="deepseek",
            model_snapshot="deepseek-chat",
            runtime_fingerprint="fp",
            status="failed_unknown",
            error_code="llm_failed_unknown",
        )
    )
    db.commit()
    db.close()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    check = session_factory().get(TaskRecord, task_id)
    assert check.status == "failed_unknown"


@pytest.mark.asyncio
async def test_news_fetch_runs_off_event_loop_for_heartbeat(test_db, monkeypatch):
    from app.tasks import news_collect as module
    import threading

    _, session_factory = test_db
    db = session_factory()
    task_id = _task(db)
    db.close()
    main_thread = threading.get_ident()
    state = {"fetch_done": False, "tick_before_fetch_done": False}

    def blocking_fetch():
        assert threading.get_ident() != main_thread
        time.sleep(0.05)
        state["fetch_done"] = True
        return [], []

    monkeypatch.setattr(module, "SessionLocal", session_factory)
    monkeypatch.setattr(module, "fetch_news_candidates", blocking_fetch)
    async def mark_tick():
        await asyncio.sleep(0.01)
        if not state["fetch_done"]:
            state["tick_before_fetch_done"] = True

    tick = asyncio.create_task(mark_tick())
    await module.news_collect_task(None, task_id)
    await tick
    assert state["tick_before_fetch_done"] is True


@pytest.mark.asyncio
async def test_persist_error_rolls_back_report_and_marks_task_failed(test_db):
    _, session_factory = test_db
    db = session_factory()
    task_id = _task(db)
    db.close()

    async def execute(ctx):
        return {"result": "ok"}

    def persist_result(db, task, result):
        task.result_json = {"must_rollback": True}
        raise RuntimeError("database unavailable")

    runner = TaskExecutionRunner(session_factory)
    with pytest.raises(RuntimeError):
        await runner.run(task_id, execute, persist_result)
    check = session_factory().get(TaskRecord, task_id)
    assert check.status == "failed"
    assert check.result_json is None


@pytest.mark.asyncio
async def test_old_owner_is_fenced_after_reclaim_for_progress_step_and_finalize(test_db):
    _, session_factory = test_db
    clock = MutableClock()
    db = session_factory()
    task_id = _task(db)
    db.close()
    runner = TaskExecutionRunner(session_factory, lease_seconds=5, clock=clock)
    old = runner._claim(task_id).context
    assert old is not None
    clock.advance(seconds=6)
    new = runner._claim(task_id).context
    assert new is not None
    assert new.execution_token != old.execution_token

    with pytest.raises(TaskExecutionFenced):
        await old.ensure_current()
    with pytest.raises(TaskExecutionFenced):
        await old.set_progress(80)
    with pytest.raises(TaskExecutionFenced):
        await old.heartbeat()
    with pytest.raises(TaskExecutionFenced):
        await runner._persist_success(old, {"old": True}, lambda db, task, result: None)

    # New owner can still complete the task.
    await runner._persist_success(new, {"new": True}, lambda db, task, result: None)
    check = session_factory().get(TaskRecord, task_id)
    assert check.status == "success"
    assert check.result_json == {"new": True}


@pytest.mark.asyncio
async def test_analysis_wrapper_uses_snapshot_and_duplicate_delivery_is_noop(test_db, monkeypatch):
    """The ARQ wrapper cannot alter durable args and only calls orchestration once."""
    from app.tasks import analysis as module
    from app.services import analysis_orchestrator

    _, session_factory = test_db
    db = session_factory()
    user = User(
        username="runner-wrapper",
        email="runner-wrapper@example.test",
        password_hash="test-only",
        tier="free",
        role="user",
        is_active=True,
    )
    db.add(user)
    db.flush()
    user_id = user.id
    task = TaskRecord(
        task_type="stock_analysis",
        user_id=user_id,
        status="pending",
        input_snapshot={"_args": {"stock_code": "600519", "user_id": user.id}},
        prompt_version="stock-analysis-v1",
    )
    db.add(task)
    db.commit()
    task_id = task.id
    db.close()
    monkeypatch.setattr(module, "SessionLocal", session_factory)
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
    await module.analyze_stock_task(None, task_id, "600519", user_id)
    await module.analyze_stock_task(None, task_id, "600519", user_id)
    assert calls == 1
    check = session_factory().get(TaskRecord, task_id)
    assert check.status == "success"
    assert session_factory().query(AnalysisReport).count() == 1


@pytest.mark.asyncio
async def test_analysis_wrapper_rejects_delivery_args_that_differ_from_snapshot(test_db, monkeypatch):
    from app.tasks import analysis as module

    _, session_factory = test_db
    db = session_factory()
    user = User(
        username="runner-mismatch",
        email="runner-mismatch@example.test",
        password_hash="test-only",
        tier="free",
        role="user",
        is_active=True,
    )
    db.add(user)
    db.flush()
    user_id = user.id
    task = TaskRecord(
        task_type="stock_analysis",
        user_id=user_id,
        status="pending",
        input_snapshot={"_args": {"stock_code": "600519", "user_id": user.id}},
        prompt_version="stock-analysis-v1",
    )
    db.add(task)
    db.commit()
    task_id = task.id
    db.close()
    monkeypatch.setattr(module, "SessionLocal", session_factory)

    async def unexpected_call(*args, **kwargs):
        raise AssertionError("orchestrator must not run for mismatched delivery")

    from app.services import analysis_orchestrator

    monkeypatch.setattr(analysis_orchestrator, "run_full_analysis", unexpected_call)
    with pytest.raises(TaskExecutionInputError):
        await module.analyze_stock_task(None, task_id, "000001", user_id)
    check = session_factory().get(TaskRecord, task_id)
    assert check.status == "failed"
    assert "任务参数" in check.error


def test_validate_snapshot_args_rejects_missing_or_coerced_delivery_values():
    context = TaskExecutionContext(
        task_id=1,
        execution_token="token",
        task_type="stock_analysis",
        user_id=1,
        model_config_id=None,
        input_snapshot={"_args": {"stock_code": "600519", "user_id": 1}},
        prompt_version="v1",
    )
    with pytest.raises(TaskExecutionInputError):
        validate_snapshot_args(context, {"stock_code": "600519", "days": 30})
    with pytest.raises(TaskExecutionInputError):
        validate_snapshot_args(context, {"stock_code": "600519", "user_id": "1"})


def test_failed_unknown_task_status_is_terminal_for_api_polling(auth_client, test_db, seed_user):
    _, session_factory = test_db
    db = session_factory()
    task = TaskRecord(
        task_type="stock_analysis",
        user_id=seed_user["id"],
        status="failed_unknown",
        error="llm_failed_unknown: 模型调用结果未知，任务未自动重试",
        input_snapshot={"_args": {"stock_code": "600519", "user_id": seed_user["id"]}},
        prompt_version="v1",
    )
    db.add(task)
    db.commit()
    task_id = task.id
    db.close()

    response = auth_client.get(f"/api/tasks/{task_id}")
    assert response.status_code == 200
    payload = response.json()["data"]
    assert payload["status"] == "failed_unknown"
    assert "未知" in payload["error"]
