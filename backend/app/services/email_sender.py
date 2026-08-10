"""SMTP email sender — sends verification codes and system notifications.

When EMAIL_ENABLED=false (dev mode), logs to console instead of sending.
"""
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import aiosmtplib

from app.core.config import settings
from app.core.logger import logger


async def send_email(to: str, subject: str, body: str) -> None:
    """Send a plain-text email. Falls back to console log in dev mode."""
    if not settings.EMAIL_ENABLED:
        logger.warning(f"[EMAIL-DEV] to={to} subject={subject} body={body[:80]}")
        return

    if not settings.SMTP_HOST or not settings.SMTP_USER:
        logger.error("[EMAIL] SMTP not configured but EMAIL_ENABLED=true — falling back to console")
        logger.warning(f"[EMAIL-FALLBACK] to={to} subject={subject} body={body[:80]}")
        return

    msg = MIMEMultipart()
    msg["From"] = settings.SMTP_FROM or settings.SMTP_USER
    msg["To"] = to
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain", "utf-8"))

    try:
        await aiosmtplib.send(
            msg,
            hostname=settings.SMTP_HOST,
            port=settings.SMTP_PORT,
            username=settings.SMTP_USER,
            password=settings.SMTP_PASSWORD,
            use_tls=settings.SMTP_USE_SSL,
        )
        logger.info(f"[EMAIL] sent to {to}: {subject}")
    except Exception as e:
        logger.error(f"[EMAIL] failed to send to {to}: {e}")
        raise
