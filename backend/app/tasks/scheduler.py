"""APScheduler integration for timed tasks (sector analysis, etc.).

In TASK_INLINE mode the scheduler runs inside uvicorn's event loop.
In production it runs alongside the arq worker.
"""
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from app.core.config import settings
from app.core.logger import logger

_scheduler: AsyncIOScheduler | None = None


def get_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler(timezone="Asia/Shanghai")
    return _scheduler


async def _run_sector_analysis_scheduled():
    """Daily sector analysis: every trading day 09:30 Shanghai time."""
    logger.info("Scheduled sector analysis starting")
    try:
        from app.core.database import SessionLocal
        from app.models.task_record import TaskRecord
        from app.tasks.sector_analysis import sector_analysis_task
        from datetime import datetime, timezone

        db = SessionLocal()
        task = TaskRecord(task_type="sector_analysis", user_id=None, status="pending", progress=0)
        db.add(task)
        db.commit()
        db.refresh(task)
        db.close()

        await sector_analysis_task(None, task.id, 0)
        logger.info(f"Scheduled sector analysis done: task_id={task.id}")
    except Exception as e:
        logger.error(f"Scheduled sector analysis failed: {e}")


def start_scheduler(app=None):
    """Register timed jobs and start the scheduler."""
    sched = get_scheduler()
    # Sector analysis: weekday 09:30
    sched.add_job(
        _run_sector_analysis_scheduled,
        CronTrigger(day_of_week="mon-fri", hour=9, minute=30),
        id="sector_analysis_daily",
        replace_existing=True,
    )
    if settings.TASK_INLINE:
        sched.start()
        logger.info("APScheduler started (inline mode): sector_analysis daily 09:30")
    else:
        logger.info("APScheduler jobs registered (will run in arq worker)")


def shutdown_scheduler():
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("APScheduler stopped")
