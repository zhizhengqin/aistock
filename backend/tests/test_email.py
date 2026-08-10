"""SMTP email sender tests — dev mode (console) + config validation."""
import pytest
from app.core.config import settings
from app.services.email_sender import send_email
from app.services.verify_code import gen_and_send_code


@pytest.mark.asyncio
async def test_send_email_dev_mode_logs_to_console(fake_redis):
    """EMAIL_ENABLED=false logs instead of sending."""
    settings.EMAIL_ENABLED = False
    # Should not raise
    await send_email("test@example.com", "Test Subject", "Test body")


@pytest.mark.asyncio
async def test_gen_and_send_code_dev_mode(fake_redis):
    settings.EMAIL_ENABLED = False
    code = await gen_and_send_code("user@example.com")
    assert len(code) == settings.VERIFY_CODE_LENGTH
    assert code.isdigit()


@pytest.mark.asyncio
async def test_gen_and_send_code_stores_in_redis(fake_redis):
    settings.EMAIL_ENABLED = False
    code = await gen_and_send_code("user@example.com")
    stored = fake_redis.get(f"verify_code:user@example.com")
    assert stored == code


def test_smtp_config_fields_exist():
    assert hasattr(settings, "SMTP_HOST")
    assert hasattr(settings, "SMTP_PORT")
    assert hasattr(settings, "SMTP_USER")
    assert hasattr(settings, "SMTP_PASSWORD")
    assert hasattr(settings, "EMAIL_ENABLED")
    assert settings.EMAIL_ENABLED is False  # dev default
