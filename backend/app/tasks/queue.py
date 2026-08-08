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
    start_scheduler(force=True)


async def _on_worker_shutdown(ctx):
    from app.tasks.scheduler import shutdown_scheduler
    shutdown_scheduler()


class WorkerSettings:
    on_startup = _on_worker_startup
    on_shutdown = _on_worker_shutdown
    functions = [analyze_stock_task, main_force_task, sector_analysis_task, dragon_tiger_task, portfolio_diagnosis_task, stock_risk_task, portfolio_risk_task, news_collect_task, us_research_task]
    redis_settings = get_redis_settings()
    max_jobs = 2
    job_timeout = 300
    max_tries = 1
