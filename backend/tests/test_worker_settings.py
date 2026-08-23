"""arq WorkerSettings + scheduler wiring for the production worker container."""
import asyncio
import os
from pathlib import Path
import subprocess
import sys

import pytest

from app.core.config import settings
from app.tasks import scheduler as sched_mod
from app.tasks.queue import WorkerSettings
from app.tasks.scheduler import start_scheduler, shutdown_scheduler


COMPOSE_FILE = Path(__file__).resolve().parents[2] / "deploy" / "docker-compose.yml"


def _reset_scheduler():
    """Drop the singleton so each test gets a scheduler bound to its own loop."""
    try:
        shutdown_scheduler()
    except Exception:
        pass
    sched_mod._scheduler = None


def test_worker_registers_all_tasks():
    names = [f.__name__ for f in WorkerSettings.functions]
    assert len(names) == 10
    for expected in [
        "analyze_stock_task", "main_force_task", "sector_analysis_task",
        "dragon_tiger_task", "portfolio_diagnosis_task", "stock_risk_task",
        "portfolio_risk_task", "news_collect_task", "us_research_task", "market_hotspot_snapshot_task",
    ]:
        assert expected in names


def test_worker_has_lifecycle_hooks():
    assert callable(getattr(WorkerSettings, "on_startup", None))
    assert callable(getattr(WorkerSettings, "on_shutdown", None))


def test_worker_import_resolves_foreign_keys_in_an_isolated_process():
    """Importing only the worker module must load every FK target model."""
    script = """
from app.tasks.queue import WorkerSettings  # noqa: F401
from app.models.base import Base

missing = []
for table in Base.metadata.tables.values():
    for foreign_key in table.foreign_keys:
        try:
            foreign_key.column
        except Exception as exc:
            missing.append(f"{table.name}.{foreign_key.parent.name}: {type(exc).__name__}")

if missing:
    print("\\n".join(missing))
    raise SystemExit(1)
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_worker_compose_healthcheck_uses_arq_worker_probe():
    compose = COMPOSE_FILE.read_text(encoding="utf-8")
    worker = compose.split("\n  worker:\n", 1)[1].split("\n  nginx:\n", 1)[0]

    assert "healthcheck:" in worker
    assert '"arq", "--check", "app.tasks.queue.WorkerSettings"' in worker
    assert "localhost:8000/api/health" not in worker


@pytest.mark.asyncio
async def test_scheduler_force_starts_even_when_not_inline(monkeypatch):
    """Prod: TASK_INLINE=false in the api container, but the arq worker must
    still run the APScheduler jobs via the force flag."""
    monkeypatch.setattr(settings, "TASK_INLINE", False)
    _reset_scheduler()
    start_scheduler()
    assert sched_mod._scheduler is not None
    assert not sched_mod._scheduler.running

    start_scheduler(force=True)
    assert sched_mod._scheduler.running
    job_ids = {j.id for j in sched_mod._scheduler.get_jobs()}
    assert "membership_expire_daily" in job_ids
    assert "news_collect_15min" in job_ids

    await WorkerSettings.on_shutdown({})
    # AsyncIOScheduler.shutdown dispatches via run_in_event_loop; let it run
    await asyncio.sleep(0.05)
    assert not sched_mod._scheduler.running
    _reset_scheduler()


@pytest.mark.asyncio
async def test_worker_on_startup_starts_scheduler(monkeypatch):
    monkeypatch.setattr(settings, "TASK_INLINE", False)
    _reset_scheduler()
    await WorkerSettings.on_startup({})
    assert sched_mod._scheduler is not None and sched_mod._scheduler.running
    _reset_scheduler()
