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
from app.models.llm_execution import LlmCallAttempt
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
    ) -> None:
        self.session_factory = session_factory or SessionLocal
        self.lease_seconds = max(1, int(lease_seconds))
        self.heartbeat_interval_seconds = (
            float(heartbeat_interval_seconds)
            if heartbeat_interval_seconds is not None
            else max(1.0, self.lease_seconds / 3)
        )
        self.clock = clock or _now

    def _session(self) -> tuple[Session, bool]:
        if isinstance(self.session_factory, Session):
            return self.session_factory, False
        return self.session_factory(), True

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
            db.commit()

            context = TaskExecutionContext(
                task_id=task.id,
                execution_token=token,
                task_type=task.task_type,
                user_id=task.user_id,
                model_config_id=task.model_config_id,
                input_snapshot=dict(task.input_snapshot or {}),
                prompt_version=task.prompt_version,
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
                    status="failed",
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
            return
        except asyncio.CancelledError:
            return

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
        try:
            result = await execute(context)
            if heartbeat_task.done() and not heartbeat_task.cancelled():
                heartbeat_error = heartbeat_task.exception()
                if heartbeat_error is not None:
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
            continue
        expected = args[key]
        if key in {"period_days", "days", "user_id"} and expected is not None and value is not None:
            try:
                expected = int(expected)
                value = int(value)
            except (TypeError, ValueError):
                pass
        if expected != value:
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
