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

    # LLM cost guard
    DAILY_TOKEN_LIMIT: int = 2_000_000

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
