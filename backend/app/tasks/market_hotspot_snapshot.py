"""Pure-data post-market market hotspot snapshot task."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from app.core.database import SessionLocal
from app.datahub.ingestion import SnapshotStore
from app.models.task_record import TaskRecord
from app.services.market_hotspots import HotspotService
from app.services.task_execution import TaskExecutionContext, TaskExecutionRunner


_snapshot_lock = asyncio.Lock()


class _ShortSessionSnapshotStore:
    """Open one DB session per persistence operation, never per network call."""

    def _run(self, method: str, *args, **kwargs):
        db = SessionLocal()
        try:
            return getattr(SnapshotStore(db), method)(*args, **kwargs)
        finally:
            db.close()

    def upsert(self, *args, **kwargs):
        return self._run("upsert", *args, **kwargs)

    def record_run(self, *args, **kwargs):
        return self._run("record_run", *args, **kwargs)

    def cleanup(self, *args, **kwargs):
        return self._run("cleanup", *args, **kwargs)


async def _capture_snapshot() -> dict[str, Any]:
    """Run network collection with a short-lived persistence session."""

    return await HotspotService(None, snapshot_store=_ShortSessionSnapshotStore()).capture_daily_snapshot()


def _persist_skipped(task_id: int, result: dict[str, str]) -> None:
    """Make a lock-skipped delivery visible without waiting for the lock."""

    db = SessionLocal()
    try:
        task = db.get(TaskRecord, task_id) if hasattr(db, "get") else None
        if task is None:
            return
        task.result_json = result
        task.status = "success"
        task.progress = 100
        task.finished_at = datetime.now(timezone.utc)
        task.heartbeat_at = task.finished_at
        task.lease_expires_at = None
        db.commit()
    finally:
        db.close()


async def market_hotspot_snapshot_task(ctx, task_id: int):
    """ARQ entrypoint; overlapping scheduler attempts return immediately."""

    if _snapshot_lock.locked():
        result = {"status": "skipped", "reason": "already_running"}
        _persist_skipped(task_id, result)
        return result
    async with _snapshot_lock:
        async def execute(execution_ctx: TaskExecutionContext):
            return await _capture_snapshot()

        def persist_result(db, task, result):
            task.result_json = result

        return await TaskExecutionRunner(SessionLocal).run(task_id, execute, persist_result)


__all__ = ["market_hotspot_snapshot_task"]
