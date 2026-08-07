import json
from datetime import datetime
from app.core.redis import redis_client
from app.core.logger import logger


def cache_get(key: str):
    raw = redis_client.get(key)
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


def cache_set(key: str, value, ttl: int):
    try:
        redis_client.setex(key, ttl, json.dumps(value, default=str, ensure_ascii=False))
    except Exception as e:
        logger.warning(f"cache_set failed for {key}: {e}")


def make_key(*parts) -> str:
    return "cache:" + ":".join(str(p) for p in parts)
