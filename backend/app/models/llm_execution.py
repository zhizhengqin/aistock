"""Durable LLM budget and call-attempt records."""

from __future__ import annotations

from datetime import date, datetime
from uuid import uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


def _uuid() -> str:
    return str(uuid4())


class LlmDailyBudget(Base, TimestampMixin):
    """One UTC+8 budget ledger row per calendar day."""

    __tablename__ = "llm_daily_budgets"
    __table_args__ = (
        CheckConstraint(
            "reserved_tokens >= 0", name="ck_llm_daily_budgets_reserved_nonnegative"
        ),
        CheckConstraint(
            "settled_tokens >= 0", name="ck_llm_daily_budgets_settled_nonnegative"
        ),
    )

    def __init__(self, **kwargs):
        kwargs.setdefault("reserved_tokens", 0)
        kwargs.setdefault("settled_tokens", 0)
        super().__init__(**kwargs)

    budget_date: Mapped[date] = mapped_column(Date, primary_key=True)
    reserved_tokens: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    settled_tokens: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )


class LlmTokenReservation(Base, TimestampMixin):
    """Persistent reservation and settlement state for one call attempt."""

    __tablename__ = "llm_token_reservations"
    __table_args__ = (
        CheckConstraint(
            "reserved_tokens >= 0", name="ck_llm_token_reservations_reserved_nonnegative"
        ),
        CheckConstraint(
            "settled_tokens >= 0", name="ck_llm_token_reservations_settled_nonnegative"
        ),
        Index("ix_llm_token_reservations_budget_status", "budget_date", "status"),
    )

    def __init__(self, **kwargs):
        kwargs.setdefault("id", _uuid())
        kwargs.setdefault("settled_tokens", 0)
        kwargs.setdefault("status", "reserved")
        super().__init__(**kwargs)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    task_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("task_records.id"), nullable=True
    )
    step_key: Mapped[str] = mapped_column(String(128), nullable=False)
    budget_date: Mapped[date] = mapped_column(Date, nullable=False)
    reserved_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False)
    settled_tokens: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="reserved")
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class LlmCallAttempt(Base, TimestampMixin):
    """Audit row for every provider HTTP attempt, including retries/probes."""

    __tablename__ = "llm_call_attempts"
    __table_args__ = (
        UniqueConstraint(
            "task_id",
            "step_key",
            "attempt_no",
            name="uq_llm_call_attempt_task_step_no",
        ),
        UniqueConstraint("operation_id", name="uq_llm_call_attempt_operation_id"),
        Index(
            "ix_llm_call_attempts_created_config_status",
            "created_at",
            "model_config_id",
            "status",
        ),
    )

    def __init__(self, **kwargs):
        kwargs.setdefault("id", _uuid())
        kwargs.setdefault("operation_id", _uuid())
        kwargs.setdefault("attempt_no", 1)
        kwargs.setdefault("status", "started")
        super().__init__(**kwargs)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    task_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("task_records.id"), nullable=True
    )
    model_config_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("llm_model_configs.id"), nullable=True
    )
    operation_id: Mapped[str] = mapped_column(String(36), nullable=False, default=_uuid)
    operation_type: Mapped[str] = mapped_column(String(32), nullable=False)
    step_key: Mapped[str] = mapped_column(String(128), nullable=False)
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    provider_snapshot: Mapped[str] = mapped_column(String(16), nullable=False)
    model_snapshot: Mapped[str] = mapped_column(String(128), nullable=False)
    runtime_fingerprint: Mapped[str] = mapped_column(String(128), nullable=False)
    input_snapshot_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reservation_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("llm_token_reservations.id"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="started")
    response_model_snapshot: Mapped[str | None] = mapped_column(String(128), nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    input_price_snapshot: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_price_snapshot: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_micro_yuan: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    usage_source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    result_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    result_schema_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    response_metadata_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)


__all__ = ["LlmCallAttempt", "LlmDailyBudget", "LlmTokenReservation"]
