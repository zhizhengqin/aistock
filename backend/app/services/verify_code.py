import random
import string
from app.core.redis import redis_client
from app.core.config import settings
from app.core.logger import logger


def _key(email: str) -> str:
    return f"verify_code:{email.lower()}"


def gen_and_send_code(email: str) -> str:
    code = "".join(random.choices(string.digits, k=settings.VERIFY_CODE_LENGTH))
    redis_client.setex(_key(email), settings.VERIFY_CODE_TTL_SECONDS, code)
    # M1: log code to console instead of sending email (SMTP will be wired in M4)
    logger.warning(f"[VERIFY-CODE] {email} -> {code} (dev mode, no email sent)")
    return code


def verify_code(email: str, code: str) -> bool:
    stored = redis_client.get(_key(email))
    if stored is None:
        return False
    return stored == code


def delete_code(email: str) -> None:
    redis_client.delete(_key(email))
