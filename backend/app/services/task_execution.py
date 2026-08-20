"""Durable, short-transaction task execution runner.

The runner is deliberately independent of any one task wrapper.  It claims a
task in one small transaction, invokes business code without a SQLAlchemy
session, and commits a final report plus terminal task state in one transaction.
Repeated ARQ/outbox delivery therefore becomes a cheap terminal no-op.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Mapping
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models.llm_execution import LlmCallAttempt, LlmDailyBudget, LlmTokenReservation
from app.models.llm_usage import LlmUsage
from app.models.task_record import TaskRecord


TERMINAL_STATUSES = frozenset({"success", "failed", "failed_unknown"})


class TaskExecutionFenced(RuntimeError):
    """Raised when a worker no longer owns the task execution lease."""


class TaskExecutionInputError(ValueError):
    """Raised when delivery arguments do not match the durable input snapshot."""

    code = "task_input_mismatch"
    user_message = "任务参数与持久化快照不一致"


@dataclass(frozen=True, slots=True)
class TaskExecutionContext:
    """Immutable task snapshot handed to business code.

    No live ORM instance or Session is retained here.  The runner methods open
    their own short-lived sessions when a progress/heartbeat write is needed.
    """

    task_id: int
    execution_token: str
    task_type: str
    user_id: int | None
    model_config_id: str | None
    input_snapshot: Mapping[str, Any]
    prompt_version: str | None
    runtime_config: Any = None
    # Task 8 injects one task-scoped structured execution service here.  Pure
    # data tasks intentionally leave this as ``None`` and never decrypt or
    # instantiate a model client.
    llm: Any = None
    _fence_event: asyncio.Event | None = None
    _runner: "TaskExecutionRunner | None" = None

    async def set_progress(self, progress: int) -> None:
        if self._runner is None:
            return
        await self._runner.set_progress(self.task_id, self.execution_token, progress)

    async def heartbeat(self) -> None:
        if self._runner is None:
            return
        await self._runner.heartbeat(self.task_id, self.execution_token)

    async def ensure_current(self) -> None:
        """Fence a stale worker before it starts its next business step."""
        if self._runner is None:
            return
        if self._fence_event is not None and self._fence_event.is_set():
            raise TaskExecutionFenced("任务执行权已变更")
        await self._runner.ensure_current(self.task_id, self.execution_token)


@dataclass(frozen=True, slots=True)
class _Claim:
    context: TaskExecutionContext | None
    terminal: bool = False


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class TaskExecutionRunner:
    """Claim and execute one durable task with execution-token fencing."""

    def __init__(
        self,
        session_factory: Callable[[], Session] | Session | None = None,
        *,
        lease_seconds: int = 300,
        heartbeat_interval_seconds: float | None = None,
        clock: Callable[[], datetime] | None = None,
        llm_service_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.session_factory = session_factory or SessionLocal
        self.lease_seconds = max(1, int(lease_seconds))
        self.heartbeat_interval_seconds = (
            float(heartbeat_interval_seconds)
            if heartbeat_interval_seconds is not None
            else max(1.0, self.lease_seconds / 3)
        )
        self.clock = clock or _now
        self.llm_service_factory = llm_service_factory

    def _build_llm_service(
        self,
        db: Session,
        task: TaskRecord,
        execution_token: str,
    ) -> tuple[Any, Any]:
        """Decrypt one locked task config and build its scoped service.

        The resolver runs during the short claim transaction and returns an
        immutable runtime snapshot.  The resulting service and executor are
        retained on ``TaskExecutionContext`` for every business step; no step
        re-reads the global default or decrypts the key again.
        """

        if task.model_config_id is None:
            return None, None
        if self.llm_service_factory is not None:
            service = self.llm_service_factory(
                db=db,
                task=task,
                execution_token=execution_token,
                session_factory=self.session_factory,
            )
            runtime = getattr(service, "runtime_config", None)
            return runtime, service

        from app.models.llm_config import LlmModelConfig
        from app.services.llm.call_executor import LlmCallExecutor
        from app.services.llm.config_service import LlmConfigService
        from app.services.llm.execution_service import LlmExecutionService
        from app.services.llm.errors import LlmError

        config = db.get(LlmModelConfig, task.model_config_id)
        if config is None:
            raise LlmError("任务绑定的大模型配置不存在", code="llm_config_missing")
        runtime = LlmConfigService(db)._runtime(config)
        executor = LlmCallExecutor(db=self.session_factory)
        service = LlmExecutionService(
            self.session_factory,
            executor=executor,
            runtime_config=runtime,
            execution_token=execution_token,
        )
        return runtime, service

    def _session(self) -> tuple[Session, bool]:
        if isinstance(self.session_factory, Session):
            return self.session_factory, False
        return self.session_factory(), True

    def _fence_started_attempts(self, db: Session, task: TaskRecord, now: datetime) -> bool:
        """Conservatively terminate provider work before reclaiming a lease.

        A worker can crash after creating a ``started`` attempt but before the
        provider response is durable.  Reclaiming the task in that window
        would issue a second model request.  Mark those attempts unknown and
        settle every still-reserved token for this task in the same claim
        transaction, then let the caller expose a terminal task state.
        """
        reservations = db.execute(
            select(LlmTokenReservation)
            .where(
                LlmTokenReservation.task_id == task.id,
                LlmTokenReservation.status == "reserved",
            )
            .with_for_update()
        ).scalars().all()
        attempts = db.execute(
            select(LlmCallAttempt)
            .where(
                LlmCallAttempt.task_id == task.id,
                LlmCallAttempt.status.in_(("started", "failed_unknown")),
            )
            .with_for_update()
        ).scalars().all()
        started_attempts = [attempt for attempt in attempts if attempt.status == "started"]
        if not started_attempts and not reservations:
            self._ensure_unknown_usage(db, task, attempts)
            return False

        attempt_reservation_ids = {
            attempt.reservation_id
            for attempt in attempts
            if attempt.reservation_id is not None
        }
        # With no provider-attempt row, the budget reaper's invariant applies:
        # a reservation with no physical attempt was never sent and is safe
        # to release.  A prior failed_unknown attempt remains chargeable.
        if not started_attempts:
            for reservation in reservations:
                ledger = db.execute(
                    select(LlmDailyBudget)
                    .where(LlmDailyBudget.budget_date == reservation.budget_date)
                    .with_for_update()
                ).scalar_one()
                reserved = int(reservation.reserved_tokens)
                ledger.reserved_tokens = max(0, int(ledger.reserved_tokens) - reserved)
                if reservation.id in attempt_reservation_ids:
                    ledger.settled_tokens += reserved
                    reservation.status = "settled"
                    reservation.settled_tokens = reserved
                else:
                    reservation.status = "released"
                    reservation.settled_tokens = 0
            self._ensure_unknown_usage(db, task, attempts)
            return False

        for reservation in reservations:
            ledger = db.execute(
                select(LlmDailyBudget)
                .where(LlmDailyBudget.budget_date == reservation.budget_date)
                .with_for_update()
            ).scalar_one()
            reserved = int(reservation.reserved_tokens)
            ledger.reserved_tokens = max(0, int(ledger.reserved_tokens) - reserved)
            if reservation.id in attempt_reservation_ids:
                ledger.settled_tokens += reserved
                reservation.status = "settled"
                reservation.settled_tokens = reserved
            else:
                reservation.status = "released"
                reservation.settled_tokens = 0

        for attempt in started_attempts:
            attempt.status = "failed_unknown"
            attempt.error_code = "llm_failed_unknown"
            attempt.error_message = "大模型响应状态未知，请勿自动重试"
            attempt.usage_source = "unknown"
        self._ensure_unknown_usage(db, task, attempts)

        task.status = "failed_unknown"
        task.error = "llm_failed_unknown: 模型调用结果未知，任务未自动重试"
        task.finished_at = now
        task.execution_token = None
        task.heartbeat_at = now
        task.lease_expires_at = None
        return True

    @staticmethod
    def _ensure_unknown_usage(
        db: Session,
        task: TaskRecord,
        attempts: list[LlmCallAttempt],
    ) -> None:
        """Backfill one usage row per physical unknown attempt.

        ``LlmUsage`` predates ``operation_id`` and therefore cannot carry a
        direct attempt foreign key.  Counting durable unknown rows under the
        task lock gives an idempotent cardinality guarantee without a schema
        change; new rows still copy each attempt's provider/model snapshot.
        """
        existing_usage_count = db.execute(
            select(LlmUsage.id)
            .where(
                LlmUsage.task_id == task.id,
                LlmUsage.status == "failed_unknown",
                LlmUsage.error_code == "llm_failed_unknown",
            )
        ).scalars().all()
        missing_usage_count = max(0, len(attempts) - len(existing_usage_count))
        for attempt in attempts[:missing_usage_count]:
            db.add(
                LlmUsage(
                    user_id=task.user_id,
                    module=attempt.operation_type,
                    model=attempt.model_snapshot,
                    prompt_tokens=0,
                    completion_tokens=0,
                    cost_fen=0,
                    task_id=task.id,
                    model_config_id=attempt.model_config_id,
                    provider_snapshot=attempt.provider_snapshot,
                    model_snapshot=attempt.model_snapshot,
                    input_price_snapshot=attempt.input_price_snapshot,
                    output_price_snapshot=attempt.output_price_snapshot,
                    status="failed_unknown",
                    error_code="llm_failed_unknown",
                )
            )

    def _claim(self, task_id: int, *, fence_event: asyncio.Event | None = None) -> _Claim:
        db, owned = self._session()
        now = self.clock()
        token = str(uuid4())
        try:
            task = db.execute(
                select(TaskRecord)
                .where(TaskRecord.id == task_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            ).scalar_one_or_none()
            if task is None or task.status in TERMINAL_STATUSES:
                db.rollback()
                return _Claim(None, terminal=task is not None)

            current_lease = _as_utc(task.lease_expires_at)
            if task.status == "running" and current_lease is not None and current_lease > now:
                db.rollback()
                return _Claim(None)

            if self._fence_started_attempts(db, task, now):
                db.commit()
                return _Claim(None, terminal=True)

            # A provider request whose outcome is unknown must never be
            # replayed merely because a worker lease expired.
            blocked = db.execute(
                select(LlmCallAttempt.id)
                .where(
                    LlmCallAttempt.task_id == task_id,
                    LlmCallAttempt.status == "failed_unknown",
                )
                .limit(1)
            ).scalar_one_or_none()
            if blocked is not None:
                task.status = "failed_unknown"
                task.error = "任务调用结果未知，未自动重放"
                task.finished_at = now
                task.execution_token = None
                task.lease_expires_at = None
                task.heartbeat_at = now
                db.commit()
                return _Claim(None, terminal=True)

            task.status = "running"
            task.execution_token = token
            task.started_at = task.started_at or now
            task.heartbeat_at = now
            task.lease_expires_at = now + timedelta(seconds=self.lease_seconds)
            runtime_config, llm_service = self._build_llm_service(db, task, token)
            db.commit()
            context = TaskExecutionContext(
                task_id=task.id,
                execution_token=token,
                task_type=task.task_type,
                user_id=task.user_id,
                model_config_id=task.model_config_id,
                input_snapshot=dict(task.input_snapshot or {}),
                prompt_version=task.prompt_version,
                runtime_config=runtime_config,
                llm=llm_service,
                _fence_event=fence_event,
                _runner=self,
            )
            return _Claim(context)
        except Exception:
            db.rollback()
            raise
        finally:
            if owned:
                db.close()

    async def heartbeat(self, task_id: int, execution_token: str) -> None:
        """Renew a lease using only the current execution token."""
        db, owned = self._session()
        now = self.clock()
        try:
            result = db.execute(
                update(TaskRecord)
                .where(
                    TaskRecord.id == task_id,
                    TaskRecord.execution_token == execution_token,
                    TaskRecord.status == "running",
                )
                .values(
                    heartbeat_at=now,
                    lease_expires_at=now + timedelta(seconds=self.lease_seconds),
                )
            )
            if result.rowcount != 1:
                db.rollback()
                raise TaskExecutionFenced("任务执行权已变更")
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            if owned:
                db.close()

    async def ensure_current(self, task_id: int, execution_token: str) -> None:
        db, owned = self._session()
        try:
            current = db.execute(
                select(TaskRecord.id)
                .where(
                    TaskRecord.id == task_id,
                    TaskRecord.execution_token == execution_token,
                    TaskRecord.status == "running",
                )
            ).scalar_one_or_none()
            if current is None:
                raise TaskExecutionFenced("任务执行权已变更")
        finally:
            if owned:
                db.close()

    async def set_progress(self, task_id: int, execution_token: str, progress: int) -> None:
        db, owned = self._session()
        try:
            result = db.execute(
                update(TaskRecord)
                .where(
                    TaskRecord.id == task_id,
                    TaskRecord.execution_token == execution_token,
                    TaskRecord.status == "running",
                )
                .values(progress=max(0, min(100, int(progress))), heartbeat_at=self.clock())
            )
            if result.rowcount != 1:
                db.rollback()
                raise TaskExecutionFenced("任务执行权已变更")
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            if owned:
                db.close()

    async def _mark_failed(self, task_id: int, token: str, error: BaseException) -> None:
        db, owned = self._session()
        now = self.clock()
        try:
            unknown = db.execute(
                select(LlmCallAttempt.id)
                .where(
                    LlmCallAttempt.task_id == task_id,
                    LlmCallAttempt.status == "failed_unknown",
                )
                .limit(1)
            ).scalar_one_or_none() is not None
            if unknown:
                status = "failed_unknown"
                code = "llm_failed_unknown"
                message = "模型调用结果未知，任务未自动重试"
            else:
                status = "failed"
                message = getattr(error, "user_message", None) or "任务执行失败"
                code = getattr(error, "code", None) or "task_execution_failed"
            result = db.execute(
                update(TaskRecord)
                .where(
                    TaskRecord.id == task_id,
                    TaskRecord.execution_token == token,
                    TaskRecord.status == "running",
                )
                .values(
                    status=status,
                    error=f"{code}: {message}"[:1000],
                    finished_at=now,
                    heartbeat_at=now,
                    lease_expires_at=None,
                )
            )
            if result.rowcount:
                db.commit()
            else:
                db.rollback()
        except Exception:
            db.rollback()
            raise
        finally:
            if owned:
                db.close()

    async def _persist_success(
        self,
        context: TaskExecutionContext,
        result: Any,
        persist_result: Callable[[Session, TaskRecord, Any], Any],
    ) -> None:
        db, owned = self._session()
        now = self.clock()
        try:
            task = db.execute(
                select(TaskRecord)
                .where(
                    TaskRecord.id == context.task_id,
                    TaskRecord.execution_token == context.execution_token,
                    TaskRecord.status == "running",
                )
                .with_for_update()
            ).scalar_one_or_none()
            if task is None:
                raise TaskExecutionFenced("任务执行权已变更")
            # The callback is a database-only synchronous boundary.  Awaiting
            # here would retain the row lock/session across arbitrary work.
            persisted = persist_result(db, task, result)
            if asyncio.iscoroutine(persisted):
                persisted.close()
                raise TypeError("persist_result must be synchronous")
            task.status = "success"
            task.progress = 100
            task.finished_at = now
            task.heartbeat_at = now
            task.lease_expires_at = None
            if isinstance(result, Mapping) and task.result_json is None:
                task.result_json = dict(result)
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            if owned:
                db.close()

    async def _heartbeat_loop(self, context: TaskExecutionContext, stop: asyncio.Event) -> None:
        try:
            while not stop.is_set():
                try:
                    await asyncio.wait_for(stop.wait(), timeout=self.heartbeat_interval_seconds)
                except asyncio.TimeoutError:
                    await self.heartbeat(context.task_id, context.execution_token)
        except TaskExecutionFenced:
            if context._fence_event is not None:
                context._fence_event.set()
            raise
        except asyncio.CancelledError:
            return
        except BaseException:
            # A storage failure is also an ownership stop signal.  Business
            # code that catches cancellation must still fail its next
            # ``ensure_current`` gate rather than advancing to another step.
            if context._fence_event is not None:
                context._fence_event.set()
            raise

    async def run(
        self,
        task_id: int,
        execute: Callable[[TaskExecutionContext], Awaitable[Any]],
        persist_result: Callable[[Session, TaskRecord, Any], Any],
    ) -> Any | None:
        """Run one claimed task; duplicate/terminal deliveries return ``None``."""
        fence_event = asyncio.Event()
        claim = self._claim(task_id, fence_event=fence_event)
        context = claim.context
        if context is None:
            return None
        stop = asyncio.Event()
        heartbeat_task = asyncio.create_task(self._heartbeat_loop(context, stop))
        execute_task = asyncio.create_task(execute(context))
        try:
            done, _ = await asyncio.wait(
                {execute_task, heartbeat_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            # A heartbeat failure/fence wins over a simultaneously completed
            # business task.  Cancel and await the business callback before
            # propagating the lease/storage error so stale owners cannot move
            # to another model step.
            if heartbeat_task in done:
                heartbeat_error: BaseException | None = None
                if not heartbeat_task.cancelled():
                    heartbeat_error = heartbeat_task.exception()
                if heartbeat_error is None:
                    heartbeat_error = TaskExecutionFenced("任务执行权已变更")
                if not execute_task.done():
                    execute_task.cancel()
                try:
                    await execute_task
                except BaseException:
                    # Drain any simultaneous business exception; the
                    # heartbeat/storage failure is the authoritative owner
                    # signal and is propagated below.
                    pass
                raise heartbeat_error

            result = execute_task.result()
            # The business callback has returned; stop the background lease
            # renewer before doing the final short transaction.  Otherwise a
            # storage failure racing with persistence could escape from the
            # cleanup path after the task was already marked successful.
            stop.set()
            if not heartbeat_task.done():
                heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass
            except BaseException as heartbeat_error:
                raise heartbeat_error
            await context.ensure_current()
            await self._persist_success(context, result, persist_result)
            return result
        except asyncio.CancelledError as exc:
            await self._mark_failed(task_id, context.execution_token, exc)
            raise
        except TaskExecutionFenced:
            raise
        except Exception as exc:
            await self._mark_failed(task_id, context.execution_token, exc)
            raise
        finally:
            stop.set()
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass
            except BaseException:
                # A heartbeat exception is handled by the main path when it
                # wins the race.  Cleanup must not replace a caller's
                # cancellation or business exception with that same error.
                pass
            if not execute_task.done():
                execute_task.cancel()
            try:
                await execute_task
            except asyncio.CancelledError:
                pass
            except BaseException:
                # The business exception was already observed by the main
                # path; draining the task here prevents an unhandled-task
                # warning during cleanup.
                pass


def snapshot_args(context: TaskExecutionContext) -> dict[str, Any]:
    """Read dispatcher arguments from the immutable persisted task snapshot."""
    raw = context.input_snapshot.get("_args")
    if not isinstance(raw, Mapping):
        raise TaskExecutionInputError("任务参数快照缺失")
    return dict(raw)


def validate_snapshot_args(
    context: TaskExecutionContext,
    supplied: Mapping[str, Any],
) -> dict[str, Any]:
    """Reject a delivery whose arguments differ from its durable snapshot."""
    args = snapshot_args(context)
    for key, value in supplied.items():
        if key not in args:
            raise TaskExecutionInputError("任务参数与持久化快照不一致")
        if args[key] != value:
            raise TaskExecutionInputError("任务参数与持久化快照不一致")
    return args


__all__ = [
    "TaskExecutionContext",
    "TaskExecutionFenced",
    "TaskExecutionInputError",
    "TaskExecutionRunner",
    "snapshot_args",
    "validate_snapshot_args",
]
