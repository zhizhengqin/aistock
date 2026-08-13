import base64
import binascii
import json
from collections.abc import Mapping

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application configuration loaded from environment variables."""

    # General
    APP_NAME: str = "睿见投研"
    ENV: str = "dev"
    DEBUG: bool = True

    # API
    API_PREFIX: str = "/api"

    # Database
    DATABASE_URL: str = "postgresql+psycopg2://aistock:aistock_dev@localhost:5433/aistock"
    DB_POOL_SIZE: int = 5
    DB_MAX_OVERFLOW: int = 10

    # Redis
    REDIS_URL: str = "redis://localhost:6380/0"

    # JWT
    JWT_SECRET: str = "dev-secret-change-in-prod"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 120
    REFRESH_TOKEN_EXPIRE_DAYS: int = 14

    # Auth
    VERIFY_CODE_TTL_SECONDS: int = 300
    VERIFY_CODE_LENGTH: int = 6

    # LLM
    DEEPSEEK_API_KEY: str = ""
    LLM_MOCK: bool = True
    LLM_MODEL: str = "deepseek-chat"
    LLM_BASE_URL: str = "https://api.deepseek.com/v1"
    TASK_INLINE: bool = True
    # LLM API keys are encrypted before persistence.  Values in this keyring
    # are standard Base64-encoded 256-bit AES keys.  An empty keyring remains
    # valid for local mock mode; production validation below requires the
    # active write key and validates every configured historical key.
    LLM_CONFIG_ENCRYPTION_KEY_ID: str = ""
    LLM_CONFIG_ENCRYPTION_KEYS: dict[str, str] = {}

    # LLM cost guard
    DAILY_TOKEN_LIMIT: int = 2_000_000

    # Email / SMTP
    SMTP_HOST: str = ""
    SMTP_PORT: int = 465
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM: str = ""
    SMTP_USE_SSL: bool = True
    EMAIL_ENABLED: bool = False  # false = log to console (dev mode)

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"

    @field_validator("LLM_CONFIG_ENCRYPTION_KEYS", mode="before")
    @classmethod
    def _parse_encryption_keyring(cls, value):
        """Accept the JSON object supplied by an environment variable."""

        if value is None or value == "":
            return {}
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except (TypeError, ValueError, json.JSONDecodeError) as exc:
                raise ValueError("LLM_CONFIG_ENCRYPTION_KEYS 必须是 JSON 对象") from None
        if not isinstance(value, Mapping):
            raise ValueError("LLM_CONFIG_ENCRYPTION_KEYS 必须是 JSON 对象")
        return dict(value)

    @model_validator(mode="after")
    def _validate_encryption_keyring(self):
        keyring = self.LLM_CONFIG_ENCRYPTION_KEYS
        write_key_id = self.LLM_CONFIG_ENCRYPTION_KEY_ID

        for key_id, encoded_key in keyring.items():
            if not isinstance(key_id, str) or not key_id:
                raise ValueError("LLM 加密密钥 ID 不能为空")
            if not isinstance(encoded_key, str):
                raise ValueError("LLM 加密密钥必须是 Base64 字符串")
            try:
                decoded_key = base64.b64decode(encoded_key.encode("ascii"), validate=True)
            except (binascii.Error, UnicodeEncodeError, ValueError):
                raise ValueError("LLM 加密密钥必须是有效的 Base64") from None
            if len(decoded_key) != 32:
                raise ValueError("LLM 加密密钥必须解码为 32 字节")

        if self.ENV.lower() in {"prod", "production"}:
            if not write_key_id:
                raise ValueError("生产环境必须配置 LLM_CONFIG_ENCRYPTION_KEY_ID")
            if write_key_id not in keyring:
                raise ValueError("生产环境写入密钥 ID 不存在于 LLM_CONFIG_ENCRYPTION_KEYS")

        # If a keyring is configured outside production, still reject a typo
        # in the write ID; this keeps local rotation tests honest while
        # allowing the existing mock/dev defaults with an empty keyring.
        if write_key_id and keyring and write_key_id not in keyring:
            raise ValueError("LLM_CONFIG_ENCRYPTION_KEY_ID 不存在于密钥环")
        return self


settings = Settings()
