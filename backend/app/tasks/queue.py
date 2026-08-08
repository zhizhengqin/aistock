from arq.connections import RedisSettings
from app.tasks.analysis import analyze_stock_task
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


class WorkerSettings:
    functions = [analyze_stock_task]
    redis_settings = get_redis_settings()
    max_jobs = 2
    job_timeout = 300
    max_tries = 1
