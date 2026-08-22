"""Persistence models for DataHub configuration, probes and snapshots."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import JSON, DateTime, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin


_JSON_DOCUMENT = JSON().with_variant(JSONB(), "postgresql")


class DataSourceConfig(Base, TimestampMixin):
    __tablename__ = "data_source_configs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    provider: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    enabled: Mapped[bool] = mapped_column(default=False, nullable=False)
    public_config_json: Mapped[dict[str, Any] | None] = mapped_column(_JSON_DOCUMENT, nullable=True)
    encrypted_credentials: Mapped[str | None] = mapped_column(Text, nullable=True)
    credential_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    fingerprint: Mapped[str | None] = mapped_column(String(128), nullable=True)
    key_hint: Mapped[str | None] = mapped_column(String(32), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    last_probe_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    last_probe_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_probe_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_by: Mapped[int | None] = mapped_column(Integer, nullable=True)


class DataSourceRoute(Base, TimestampMixin):
    __tablename__ = "data_source_routes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    capability: Mapped[str] = mapped_column(String(96), unique=True, index=True, nullable=False)
    mode: Mapped[str] = mapped_column(String(16), default="auto", nullable=False)
    provider_order_json: Mapped[list[str]] = mapped_column(_JSON_DOCUMENT, default=list, nullable=False)
    contract_version: Mapped[str] = mapped_column(String(32), default="1.0", nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    updated_by: Mapped[int | None] = mapped_column(Integer, nullable=True)


class DataSourceProbeRun(Base):
    __tablename__ = "data_source_probe_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    provider: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    capability: Mapped[str] = mapped_column(String(96), index=True, nullable=False)
    fingerprint: Mapped[str | None] = mapped_column(String(128), nullable=True)
    contract_version: Mapped[str] = mapped_column(String(32), default="1.0", nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    rows: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    safe_sample_json: Mapped[dict[str, Any] | None] = mapped_column(_JSON_DOCUMENT, nullable=True)
    created_by: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        Index("ix_data_source_probe_runs_lookup", "provider", "capability", "created_at"),
    )


class DataSourceAuditEvent(Base):
    __tablename__ = "data_source_audit_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    actor: Mapped[int | None] = mapped_column(Integer, nullable=True)
    event: Mapped[str] = mapped_column(String(64), nullable=False)
    config_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    route_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    payload_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (Index("ix_data_source_audit_events_created", "created_at"),)


class IngestionRun(Base):
    __tablename__ = "data_ingestion_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    dataset: Mapped[str] = mapped_column(String(96), index=True, nullable=False)
    trade_date: Mapped[str] = mapped_column(String(16), index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    counts_json: Mapped[dict[str, Any] | None] = mapped_column(_JSON_DOCUMENT, nullable=True)
    payload_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class DataSnapshot(Base):
    __tablename__ = "data_snapshots"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    dataset: Mapped[str] = mapped_column(String(96), nullable=False)
    trade_date: Mapped[str] = mapped_column(String(16), nullable=False)
    scope_key: Mapped[str] = mapped_column(String(128), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[Any] = mapped_column(_JSON_DOCUMENT, nullable=False)
    payload_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "dataset",
            "trade_date",
            "scope_key",
            "schema_version",
            "source",
            name="uq_data_snapshots_identity",
        ),
        Index("ix_data_snapshots_dataset_trade_date", "dataset", "trade_date"),
    )


__all__ = [
    "DataSourceAuditEvent",
    "DataSourceConfig",
    "DataSourceProbeRun",
    "DataSourceRoute",
    "DataSnapshot",
    "IngestionRun",
]
