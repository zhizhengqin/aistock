"""Worker-owned transactional outbox dispatcher."""

from __future__ import annotations

import asyncio
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models.task_outbox import TaskOutbox
from app.models.task_record import TaskRecord


_TASK_FUNCTIONS = {
    "stock_analysis": "analyze_stock_task",
    "main_force": "main_force_task",
    "sector_analysis": "sector_analysis_task",
    "dragon_tiger": "dragon_tiger_task",
    "portfolio_diagnosis": "portfolio_diagnosis_task",
    "stock_risk": "stock_risk_task",
    "portfolio_risk": "portfolio_risk_task",
    "news_collect": "news_collect_task",
    "us_research": "us_research_task",
}


class UnknownTaskType(ValueError):
    """A task type without an explicit, safe ARQ argument mapping."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


class OutboxDispatcher:
    """Claim pending outbox rows and enqueue at most one logical job each."""

    def __init__(
        self,
        session_factory: Callable[[], Session] | Session,
        *,
        sender: Any | None = None,
        worker_id: str | None = None,
        batch_size: int = 50,
        lock_timeout_seconds: int = 300,
        base_backoff_seconds: int = 2,
        max_backoff_seconds: int = 300,
        max_attempts: int = 8,
    ):
        if batch_size < 1 or batch_size > 50:
            raise ValueError("batch_size must be between 1 and 50")
        self.session_factory = session_factory
        self.sender = sender
        self.worker_id = worker_id or f"outbox-{secrets.token_hex(8)}"
        self.batch_size = batch_size
        self.lock_timeout = timedelta(seconds=max(1, lock_timeout_seconds))
        self.base_backoff_seconds = max(1, base_backoff_seconds)
        self.max_backoff_seconds = max(self.base_backoff_seconds, max_backoff_seconds)
        self.max_attempts = max(1, max_attempts)

    def _session(self) -> tuple[Session, bool]:
        if isinstance(self.session_factory, Session):
            return self.session_factory, False
        return self.session_factory(), True

    async def _sender(self):
        if self.sender is not None:
            return self.sender, False
        from arq import create_pool
        from app.tasks.queue import get_redis_settings

        return await create_pool(get_redis_settings()), True

    def _recover_stale(self, db: Session, now: datetime) -> None:
        cutoff = now - self.lock_timeout
        db.execute(
            update(TaskOutbox)
            .where(
                TaskOutbox.status == "locked",
                TaskOutbox.locked_at.isnot(None),
                TaskOutbox.locked_at < cutoff,
            )
            .values(status="pending", locked_at=None, locked_by=None, available_at=now)
        )

    def _claim(self) -> list[tuple[int, int]]:
        db, owned = self._session()
        now = _now()
        try:
            self._recover_stale(db, now)
            rows = db.execute(
                select(TaskOutbox)
                .where(
                    TaskOutbox.status == "pending",
                    TaskOutbox.available_at <= now,
                )
                .order_by(TaskOutbox.available_at.asc(), TaskOutbox.id.asc())
                .with_for_update(skip_locked=True)
                .limit(self.batch_size)
            ).scalars().all()
            claimed: list[tuple[int, int]] = []
            for row in rows:
                row.status = "locked"
                row.locked_at = now
                row.locked_by = self.worker_id
                row.attempts = int(row.attempts or 0) + 1
                claimed.append((int(row.id), int(row.task_id)))
            db.commit()
            return claimed
        except Exception:
            db.rollback()
            raise
        finally:
            if owned:
                db.close()

    @staticmethod
    def _args(task: TaskRecord) -> dict[str, Any]:
        snapshot = task.input_snapshot if isinstance(task.input_snapshot, dict) else {}
        args = snapshot.get("_args", {})
        if not isinstance(args, dict):
            raise ValueError("任务参数格式无效")
        return args

    def _job(self, task: TaskRecord) -> tuple[str, tuple[Any, ...]]:
        task_type = task.task_type
        function = _TASK_FUNCTIONS.get(task_type)
        if not function:
            raise UnknownTaskType(task_type)
        args = self._args(task)
        user_id = args.get("user_id", task.user_id)
        if task_type == "stock_analysis":
            return function, (task.id, str(args["stock_code"]), user_id)
        if task_type in {"main_force", "sector_analysis", "portfolio_diagnosis", "portfolio_risk"}:
            return function, (task.id, user_id)
        if task_type == "dragon_tiger":
            return function, (task.id, int(args.get("period_days", 5)), user_id)
        if task_type == "stock_risk":
            return function, (task.id, str(args["stock_code"]), int(args.get("days", 30)), user_id)
        if task_type == "news_collect":
            return function, (task.id,)
        if task_type == "us_research":
            return function, (task.id, str(args["trade_date"]), user_id)
        raise UnknownTaskType(task_type)

    @staticmethod
    def _safe_error(error: Exception, *, unknown: bool = False) -> str:
        if unknown:
            return "不支持的任务类型，任务未投递"
        # Keep operational context useful without exposing provider input,
        # credentials or arbitrary exception strings.
        return "任务投递失败，将自动重试"

    def _mark_success(self, outbox_id: int) -> None:
        db, owned = self._session()
        try:
            row = db.get(TaskOutbox, outbox_id)
            if row and row.status == "locked" and row.locked_by == self.worker_id:
                row.status = "delivered"
                row.locked_at = None
                row.locked_by = None
                row.last_error = None
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            if owned:
                db.close()

    def _mark_failure(self, outbox_id: int, *, unknown: bool = False) -> None:
        db, owned = self._session()
        try:
            row = db.get(TaskOutbox, outbox_id)
            if row and row.status == "locked" and row.locked_by == self.worker_id:
                attempts = int(row.attempts or 0)
                if unknown and attempts >= self.max_attempts:
                    row.status = "dead_letter"
                elif attempts >= self.max_attempts:
                    row.status = "dead_letter"
                else:
                    row.status = "pending"
                delay = min(
                    self.max_backoff_seconds,
                    self.base_backoff_seconds * (2 ** max(0, attempts - 1)),
                )
                row.available_at = _now() + timedelta(seconds=delay)
                row.locked_at = None
                row.locked_by = None
                row.last_error = self._safe_error(ValueError(), unknown=unknown)
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            if owned:
                db.close()

    async def dispatch_once(self) -> int:
        claimed = self._claim()
        if not claimed:
            return 0
        sender, close_sender = await self._sender()
        dispatched = 0
        try:
            for outbox_id, task_id in claimed:
                db, owned = self._session()
                try:
                    task = db.get(TaskRecord, task_id)
                    if task is None:
                        raise ValueError("任务不存在")
                    job_name, args = self._job(task)
                except UnknownTaskType:
                    if owned:
                        db.close()
                    self._mark_failure(outbox_id, unknown=True)
                    continue
                except Exception:
                    if owned:
                        db.close()
                    self._mark_failure(outbox_id)
                    continue
                finally:
                    if owned and db.is_active:
                        db.close()
                try:
                    await sender.enqueue_job(job_name, *args, _job_id=f"task:{task_id}")
                except Exception:
                    self._mark_failure(outbox_id)
                    continue
                self._mark_success(outbox_id)
                dispatched += 1
        finally:
            if close_sender:
                close = getattr(sender, "close", None)
                if close is not None:
                    result = close()
                    if asyncio.iscoroutine(result):
                        await result
        return dispatched


__all__ = ["OutboxDispatcher", "UnknownTaskType"]
