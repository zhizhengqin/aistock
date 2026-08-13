"""APScheduler integration for timed tasks.

Full job set per architecture doc section 9.1:
- news collect: every 15 min (07:00-23:00)
- US overnight research: Tue-Sat 03:30 (after US market close)
- sector analysis: Mon-Fri 09:30
- dragon-tiger list: Mon-Fri 17:05 (after exchange T+1 publish)
- monitor polling: every 5 min (trading-hours check inside the engine)

Every job runs through _guarded, which logs failures and creates an in-app
notification for admin users so job failures are visible without email infra.
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


async def _submit_scheduled(task_type: str, inline_func, args: list, *, requires_llm: bool = True) -> int:
    """Submit a worker-owned scheduled task through the same atomic service."""
    from app.core.database import SessionLocal
    from app.services.task_submission import (
        TaskSubmission,
        TaskSubmissionService,
        schedule_inline_after_commit,
    )

    db = SessionLocal()
    try:
        args_dict = {"user_id": 0}
        if task_type == "dragon_tiger":
            args_dict["period_days"] = args[0]
        elif task_type == "us_research":
            args_dict["trade_date"] = args[0]
        submission = TaskSubmission(
            task_type=task_type,
            user_id=None,
            feature=None,
            feature_cost=0,
            args=args_dict,
            input_snapshot={"args": args},
            prompt_version=f"{task_type}-scheduled-v1",
            requires_llm=requires_llm,
        )
        result = TaskSubmissionService(db).submit(submission)
        if settings.TASK_INLINE:
            await schedule_inline_after_commit(db, result, inline_func, tuple(args))
        return result.task.id
    finally:
        db.close()


def _notify_admins(title: str, content: str):
    """In-app failure alert: write a notification row for every admin user."""
    try:
        from app.core.database import SessionLocal
        from app.models.user import User
        from app.models.monitor_notification import MonitorNotification
        db = SessionLocal()
        try:
            admins = db.query(User).filter(User.role == "admin", User.is_active == True).all()  # noqa: E712
            for admin in admins:
                db.add(MonitorNotification(
                    user_id=admin.id, config_id=None, stock_code="", stock_name="系统",
                    ntype="scheduler_failure", title=title, content=content,
                    status="pending",
                ))
            db.commit()
        finally:
            db.close()
    except Exception as e:
        logger.error(f"Failed to write admin failure notification: {e}")


async def _guarded(job_name: str, coro):
    try:
        await coro
        logger.info(f"Scheduled job {job_name} done")
    except Exception as e:
        logger.error(f"Scheduled job {job_name} failed: {e}")
        _notify_admins(f"定时任务失败：{job_name}", str(e)[:500])


async def _run_sector_analysis_scheduled():
    from app.tasks.sector_analysis import sector_analysis_task
    await _guarded(
        "sector_analysis",
        _submit_scheduled("sector_analysis", sector_analysis_task, [0]),
    )


async def _run_dragon_tiger_scheduled():
    from app.tasks.dragon_tiger import dragon_tiger_task
    await _guarded(
        "dragon_tiger",
        _submit_scheduled("dragon_tiger", dragon_tiger_task, [5, 0]),
    )


async def _run_news_collect_scheduled():
    from app.tasks.news_collect import news_collect_task
    await _guarded(
        "news_collect",
        _submit_scheduled("news_collect", news_collect_task, [], requires_llm=False),
    )


async def _run_us_research_scheduled():
    from app.tasks.us_research import us_research_task
    from app.services.us_research_orchestrator import latest_us_trade_date
    await _guarded(
        "us_research",
        _submit_scheduled("us_research", us_research_task, [latest_us_trade_date(), 0]),
    )


async def _run_monitor_poll_scheduled():
    import asyncio
    from app.services.monitor_engine import run_monitor_check
    count = await asyncio.to_thread(run_monitor_check)
    logger.info(f"Monitor poll done, triggered={count}")


async def _run_membership_expire_scheduled():
    import asyncio
    from app.core.database import SessionLocal
    from app.services.membership import expire_memberships

    def _run():
        db = SessionLocal()
        try:
            return expire_memberships(db)
        finally:
            db.close()

    n = await asyncio.to_thread(_run)
    if n:
        logger.info(f"Membership expire job: downgraded {n} user(s) to free")


def start_scheduler(app=None, force: bool = False):
    """Register timed jobs and start the scheduler.

    In dev the api process runs jobs inline (TASK_INLINE=true). In prod the api
    container sets TASK_INLINE=false and the arq worker container starts the
    scheduler via its on_startup hook with force=True, so jobs run exactly once.
    """

    sched = get_scheduler()
    sched.add_job(
        _run_sector_analysis_scheduled,
        CronTrigger(day_of_week="mon-fri", hour=9, minute=30),
        id="sector_analysis_daily", replace_existing=True,
    )
    sched.add_job(
        _run_dragon_tiger_scheduled,
        CronTrigger(day_of_week="mon-fri", hour=17, minute=5),
        id="dragon_tiger_daily", replace_existing=True,
    )
    sched.add_job(
        _run_news_collect_scheduled,
        CronTrigger(minute="*/15", hour="7-23"),
        id="news_collect_15min", replace_existing=True,
    )
    sched.add_job(
        _run_us_research_scheduled,
        CronTrigger(day_of_week="tue-sat", hour=3, minute=30),
        id="us_research_daily", replace_existing=True,
    )
    sched.add_job(
        _run_monitor_poll_scheduled,
        CronTrigger(minute="*/5"),
        id="monitor_poll_5min", replace_existing=True,
    )
    sched.add_job(
        _run_membership_expire_scheduled,
        CronTrigger(hour=0, minute=30),
        id="membership_expire_daily", replace_existing=True,
    )
    if settings.TASK_INLINE or force:
        if not sched.running:
            sched.start()
        logger.info("APScheduler started: 6 jobs registered")
    else:
        logger.info("APScheduler jobs registered (will run in arq worker)")


def shutdown_scheduler():
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("APScheduler stopped")
