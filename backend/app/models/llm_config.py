"""Persistence models for the LLM model center.

The model-center tables deliberately keep provider credentials as encrypted
envelope fields.  A decrypted API key is a short-lived runtime value from the
LLM service layer and is never represented by an ORM column here.
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin
from app.services.llm.types import ModelLifecycle


def _uuid() -> str:
    """Create an application-side UUID string for cross-database portability."""

    return str(uuid4())


class LlmModelConfig(Base, TimestampMixin):
    """One immutable-at-runtime provider/model configuration version."""

    __tablename__ = "llm_model_configs"
    __table_args__ = (
        CheckConstraint(
            "input_price_micro_yuan_per_million IS NULL OR input_price_micro_yuan_per_million >= 0",
            name="ck_llm_model_configs_input_price_nonnegative",
        ),
        CheckConstraint(
            "output_price_micro_yuan_per_million IS NULL OR output_price_micro_yuan_per_million >= 0",
            name="ck_llm_model_configs_output_price_nonnegative",
        ),
        Index("ix_llm_model_configs_provider_status", "provider", "lifecycle_status"),
    )

    def __init__(self, **kwargs):
        # SQLAlchemy applies mapped-column defaults at INSERT time.  These
        # values are also useful before the first flush (for AAD/fingerprint
        # construction and optimistic-version handling).
        kwargs.setdefault("id", _uuid())
        kwargs.setdefault("credential_version", _uuid())
        kwargs.setdefault("lifecycle_status", ModelLifecycle.DRAFT.value)
        kwargs.setdefault("version", 1)
        super().__init__(**kwargs)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    provider: Mapped[str] = mapped_column(String(16), nullable=False)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    model_name: Mapped[str] = mapped_column(String(128), nullable=False)
    base_url: Mapped[str] = mapped_column(String(512), nullable=False)

    # CredentialEnvelope fields.  ``encrypted_api_key`` is ciphertext, not a
    # plaintext key; no ``api_key`` column is intentionally defined.
    encrypted_api_key: Mapped[str] = mapped_column(Text, nullable=False)
    encryption_key_id: Mapped[str] = mapped_column(String(128), nullable=False)
    envelope_version: Mapped[str] = mapped_column(String(16), nullable=False)
    nonce: Mapped[str] = mapped_column(String(128), nullable=False)
    credential_version: Mapped[str] = mapped_column(
        String(64), nullable=False, default=_uuid
    )
    key_hint: Mapped[str | None] = mapped_column(String(64), nullable=True)

    input_price_micro_yuan_per_million: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    output_price_micro_yuan_per_million: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    max_output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    lifecycle_status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=ModelLifecycle.DRAFT.value, server_default="draft"
    )
    supersedes_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("llm_model_configs.id"), nullable=True
    )
    runtime_fingerprint: Mapped[str] = mapped_column(String(128), nullable=False)
    verified_test_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    last_probe_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_probe_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_probe_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    created_by: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=True, index=True
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class LlmRuntimeSetting(Base, TimestampMixin):
    """Singleton row containing the globally selected model and budget state."""

    __tablename__ = "llm_runtime_settings"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_llm_runtime_settings_singleton"),
        CheckConstraint(
            "daily_token_limit > 0", name="ck_llm_runtime_settings_positive_limit"
        ),
    )

    def __init__(self, **kwargs):
        kwargs.setdefault("id", 1)
        kwargs.setdefault("daily_token_limit", 2_000_000)
        kwargs.setdefault("budget_locked", False)
        kwargs.setdefault("version", 1)
        super().__init__(**kwargs)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1, server_default="1")
    default_model_config_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("llm_model_configs.id"), nullable=True
    )
    daily_token_limit: Mapped[int] = mapped_column(
        Integer, nullable=False, default=2_000_000, server_default="2000000"
    )
    budget_locked: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    switched_by: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=True
    )
    switched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")


class LlmModelTestRun(Base, TimestampMixin):
    """A durable capability/probe result for a saved or unsaved config."""

    __tablename__ = "llm_model_test_runs"
    __table_args__ = (
        Index("ix_llm_model_test_runs_config_created", "model_config_id", "created_at"),
    )

    def __init__(self, **kwargs):
        kwargs.setdefault("id", _uuid())
        kwargs.setdefault("test_type", "probe")
        kwargs.setdefault("status", "started")
        super().__init__(**kwargs)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    model_config_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("llm_model_configs.id"), nullable=True
    )
    runtime_fingerprint: Mapped[str] = mapped_column(String(128), nullable=False)
    test_type: Mapped[str] = mapped_column(String(32), nullable=False, default="probe")
    created_by: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=True
    )
    capability_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    result_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    response_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="started")
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)


class LlmActivationRequest(Base, TimestampMixin):
    """Idempotency record for an administrator model activation request."""

    __tablename__ = "llm_activation_requests"
    __table_args__ = (
        Index("ix_llm_activation_requests_config_created", "model_config_id", "created_at"),
    )

    def __init__(self, **kwargs):
        kwargs.setdefault("id", _uuid())
        kwargs.setdefault("status", "pending")
        super().__init__(**kwargs)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    model_config_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("llm_model_configs.id"), nullable=False
    )
    expected_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_by: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=True
    )
    response_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")


class LlmAdminAuditEvent(Base):
    """Immutable administrator audit event; never stores credential material."""

    __tablename__ = "llm_admin_audit_events"
    __table_args__ = (
        Index("ix_llm_admin_audit_events_created", "created_at"),
    )

    def __init__(self, **kwargs):
        kwargs.setdefault("id", _uuid())
        super().__init__(**kwargs)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    admin_user_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("users.id"), nullable=True
    )
    model_config_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("llm_model_configs.id"), nullable=True
    )
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    runtime_settings_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    payload_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


__all__ = [
    "LlmActivationRequest",
    "LlmAdminAuditEvent",
    "LlmModelConfig",
    "LlmModelTestRun",
    "LlmRuntimeSetting",
]
