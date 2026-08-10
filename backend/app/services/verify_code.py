import random
import string
from app.core.redis import redis_client
from app.core.config import settings
from app.core.logger import logger
from app.services.email_sender import send_email


def _key(email: str) -> str:
    return f"verify_code:{email.lower()}"


async def gen_and_send_code(email: str) -> str:
    code = "".join(random.choices(string.digits, k=settings.VERIFY_CODE_LENGTH))
    redis_client.setex(_key(email), settings.VERIFY_CODE_TTL_SECONDS, code)

    subject = f"【{settings.APP_NAME}】您的验证码：{code}"
    body = (
        f"您正在操作 {settings.APP_NAME} 账户。\n\n"
        f"验证码：{code}\n"
        f"有效期：{settings.VERIFY_CODE_TTL_SECONDS // 60} 分钟\n\n"
        f"如非本人操作，请忽略此邮件。"
    )
    try:
        await send_email(email, subject, body)
    except Exception as e:
        logger.error(f"verify_code send failed: {e}")
        # Don't expose SMTP errors to the user; code is still in Redis for dev
    return code


def verify_code(email: str, code: str) -> bool:
    stored = redis_client.get(_key(email))
    if stored is None:
        return False
    return stored == code


def delete_code(email: str) -> None:
    redis_client.delete(_key(email))
