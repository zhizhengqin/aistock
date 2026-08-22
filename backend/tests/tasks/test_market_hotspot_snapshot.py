import asyncio
from datetime import datetime, timezone

import pytest

from app.core.config import settings
from app.tasks import market_hotspot_snapshot as task_module
from app.tasks import scheduler as scheduler_module


def test_scheduler_registers_three_beijing_post_market_attempts(monkeypatch):
    jobs = []

    class FakeScheduler:
        running = False

        def add_job(self, function, trigger, **kwargs):
            jobs.append((function, trigger, kwargs))

        def start(self):
            raise AssertionError("scheduler must not start in this registration test")

    monkeypatch.setattr(scheduler_module, "get_scheduler", lambda: FakeScheduler())
    monkeypatch.setattr(settings, "TASK_INLINE", False)
    scheduler_module.start_scheduler(force=False)
    matches = [job for job in jobs if job[2].get("id") == "market_hotspot_snapshot_15m"]
    assert len(matches) == 1
    _, trigger, kwargs = matches[0]
    fields = {field.name: str(field) for field in trigger.fields}
    assert kwargs["id"] == "market_hotspot_snapshot_15m"
    assert fields["day_of_week"] == "mon-fri"
    assert fields["hour"] == "15"
    assert fields["minute"] == "10,20,30"
    assert str(trigger.timezone) == "Asia/Shanghai"


@pytest.mark.asyncio
async def test_snapshot_task_returns_non_blocking_skipped_result_when_lock_is_held(monkeypatch):
    class Row:
        status = "pending"
        progress = 0
        result_json = None
        finished_at = None

    row = Row()

    class Session:
        def get(self, model, task_id):
            return row

        def commit(self):
            return None

        def close(self):
            return None

    monkeypatch.setattr(task_module, "SessionLocal", lambda: Session())
    task_module._snapshot_lock = asyncio.Lock()
    await task_module._snapshot_lock.acquire()
    try:
        result = await task_module.market_hotspot_snapshot_task({}, 999)
    finally:
        task_module._snapshot_lock.release()
    assert result == {"status": "skipped", "reason": "already_running"}
    assert row.result_json == result
    assert row.status == "success"


@pytest.mark.asyncio
async def test_snapshot_task_uses_service_and_returns_counts(monkeypatch):
    calls = []

    class FakeService:
        async def capture_daily_snapshot(self):
            calls.append(True)
            return {"categories": {"industry": {"status": "success"}}, "constituents_saved": 1}

    monkeypatch.setattr(task_module, "HotspotService", lambda db, snapshot_store=None: FakeService())
    monkeypatch.setattr(task_module, "SessionLocal", lambda: _Session())
    result = await task_module._capture_snapshot()
    assert result["constituents_saved"] == 1
    assert calls == [True]


class _Session:
    def close(self):
        return None
