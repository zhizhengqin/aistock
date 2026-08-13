import asyncio

from arq.connections import RedisSettings
from app.tasks.analysis import analyze_stock_task
from app.tasks.main_force import main_force_task
from app.tasks.sector_analysis import sector_analysis_task
from app.tasks.dragon_tiger import dragon_tiger_task
from app.tasks.portfolio import portfolio_diagnosis_task
from app.tasks.risk_analysis import stock_risk_task
from app.tasks.portfolio_risk import portfolio_risk_task
from app.tasks.news_collect import news_collect_task
from app.tasks.us_research import us_research_task
from app.core.config import settings
from app.services.llm.http_client import close_llm_http_client, get_llm_http_client


_outbox_loop_task: asyncio.Task | None = None


async def _outbox_loop():
    """Continuously drain transactional outbox rows in the worker process."""
    from app.core.database import SessionLocal
    from app.services.outbox_dispatcher import OutboxDispatcher

    dispatcher = OutboxDispatcher(SessionLocal)
    while True:
        try:
            await dispatcher.dispatch_once()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # A transient database/Redis outage must leave rows pending for
            # the next pass; never turn worker startup into a fatal error.
            from app.core.logger import logger
            logger.warning(f"事务 outbox 投递循环暂时失败: {type(exc).__name__}")
        await asyncio.sleep(1.0)


def get_redis_settings() -> RedisSettings:
    url = settings.REDIS_URL
    # parse redis://host:port/db
    host = "localhost"
    port = 6379
    db = 1
    if "redis://" in url:
        parts = url.replace("redis://", "").split("/")
        host_port = parts[0]
        if ":" in host_port:
            host, port_str = host_port.split(":")
            port = int(port_str)
        if len(parts) > 1 and parts[1]:
            db = int(parts[1])
    return RedisSettings(host=host, port=port, database=db)


async def _on_worker_startup(ctx):
    """arq worker boots the timed-job scheduler (prod owns scheduling here)."""
    from app.tasks.scheduler import start_scheduler
    get_llm_http_client()
    start_scheduler(force=True)
    global _outbox_loop_task
    if _outbox_loop_task is None or _outbox_loop_task.done():
        _outbox_loop_task = asyncio.create_task(_outbox_loop())
    if isinstance(ctx, dict):
        ctx["outbox_loop_task"] = _outbox_loop_task


async def _on_worker_shutdown(ctx):
    from app.tasks.scheduler import shutdown_scheduler
    try:
        global _outbox_loop_task
        if _outbox_loop_task is not None:
            _outbox_loop_task.cancel()
            try:
                await _outbox_loop_task
            except asyncio.CancelledError:
                pass
            _outbox_loop_task = None
        shutdown_scheduler()
    finally:
        await close_llm_http_client()


class WorkerSettings:
    on_startup = _on_worker_startup
    on_shutdown = _on_worker_shutdown
    functions = [analyze_stock_task, main_force_task, sector_analysis_task, dragon_tiger_task, portfolio_diagnosis_task, stock_risk_task, portfolio_risk_task, news_collect_task, us_research_task]
    redis_settings = get_redis_settings()
    max_jobs = 2
    job_timeout = 300
    max_tries = 1
