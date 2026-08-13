from sqlalchemy import Integer, String, BigInteger, DateTime, func, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base import Base, TimestampMixin


class LlmUsage(Base, TimestampMixin):
    __tablename__ = "llm_usage"
    __table_args__ = (
        Index("ix_llm_usage_created_config", "created_at", "model_config_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    module: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_fen: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    task_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("task_records.id"), nullable=True)
    model_config_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("llm_model_configs.id"), nullable=True
    )
    provider_snapshot: Mapped[str | None] = mapped_column(String(16), nullable=True)
    model_snapshot: Mapped[str | None] = mapped_column(String(64), nullable=True)
    input_price_snapshot: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_price_snapshot: Mapped[int | None] = mapped_column(Integer, nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_micro_yuan: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
