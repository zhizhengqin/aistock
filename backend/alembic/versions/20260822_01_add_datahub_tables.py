"""Add DataHub configuration, probe, audit and snapshot tables."""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260822_01"
down_revision: Union[str, None] = "20260821_13"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_JSON_DOCUMENT = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "data_source_configs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("provider", sa.String(64), nullable=False, unique=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("public_config_json", _JSON_DOCUMENT, nullable=True),
        sa.Column("encrypted_credentials", sa.Text(), nullable=True),
        sa.Column("credential_version", sa.String(64), nullable=True),
        sa.Column("fingerprint", sa.String(128), nullable=True),
        sa.Column("key_hint", sa.String(32), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("last_probe_status", sa.String(32), nullable=True),
        sa.Column("last_probe_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_probe_latency_ms", sa.Integer(), nullable=True),
        sa.Column("updated_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_data_source_configs_provider", "data_source_configs", ["provider"], unique=True)

    op.create_table(
        "data_source_routes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("capability", sa.String(96), nullable=False, unique=True),
        sa.Column("mode", sa.String(16), nullable=False, server_default="auto"),
        sa.Column("provider_order_json", _JSON_DOCUMENT, nullable=False),
        sa.Column("contract_version", sa.String(32), nullable=False, server_default="1.0"),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("updated_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_data_source_routes_capability", "data_source_routes", ["capability"], unique=True)

    op.create_table(
        "data_source_probe_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("provider", sa.String(64), nullable=False),
        sa.Column("capability", sa.String(96), nullable=False),
        sa.Column("fingerprint", sa.String(128), nullable=True),
        sa.Column("contract_version", sa.String(32), nullable=False, server_default="1.0"),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("rows", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("safe_sample_json", _JSON_DOCUMENT, nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_data_source_probe_runs_provider", "data_source_probe_runs", ["provider"])
    op.create_index("ix_data_source_probe_runs_capability", "data_source_probe_runs", ["capability"])
    op.create_index(
        "ix_data_source_probe_runs_lookup",
        "data_source_probe_runs",
        ["provider", "capability", "created_at"],
    )

    op.create_table(
        "data_source_audit_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("actor", sa.Integer(), nullable=True),
        sa.Column("event", sa.String(64), nullable=False),
        sa.Column("config_id", sa.String(36), nullable=True),
        sa.Column("route_id", sa.String(36), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=True),
        sa.Column("payload_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_data_source_audit_events_config_id", "data_source_audit_events", ["config_id"])
    op.create_index("ix_data_source_audit_events_route_id", "data_source_audit_events", ["route_id"])
    op.create_index("ix_data_source_audit_events_created", "data_source_audit_events", ["created_at"])

    op.create_table(
        "data_ingestion_runs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("dataset", sa.String(96), nullable=False),
        sa.Column("trade_date", sa.String(16), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("counts_json", _JSON_DOCUMENT, nullable=True),
        sa.Column("payload_hash", sa.String(128), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_data_ingestion_runs_dataset", "data_ingestion_runs", ["dataset"])
    op.create_index("ix_data_ingestion_runs_trade_date", "data_ingestion_runs", ["trade_date"])

    op.create_table(
        "data_snapshots",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("dataset", sa.String(96), nullable=False),
        sa.Column("trade_date", sa.String(16), nullable=False),
        sa.Column("scope_key", sa.String(128), nullable=False),
        sa.Column("schema_version", sa.String(32), nullable=False),
        sa.Column("source", sa.String(64), nullable=False),
        sa.Column("payload_json", _JSON_DOCUMENT, nullable=False),
        sa.Column("payload_hash", sa.String(128), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint(
            "dataset", "trade_date", "scope_key", "schema_version", "source",
            name="uq_data_snapshots_identity",
        ),
    )
    op.create_index("ix_data_snapshots_dataset_trade_date", "data_snapshots", ["dataset", "trade_date"])


def downgrade() -> None:
    op.drop_index("ix_data_snapshots_dataset_trade_date", table_name="data_snapshots")
    op.drop_table("data_snapshots")
    op.drop_index("ix_data_ingestion_runs_trade_date", table_name="data_ingestion_runs")
    op.drop_index("ix_data_ingestion_runs_dataset", table_name="data_ingestion_runs")
    op.drop_table("data_ingestion_runs")
    op.drop_index("ix_data_source_audit_events_created", table_name="data_source_audit_events")
    op.drop_index("ix_data_source_audit_events_route_id", table_name="data_source_audit_events")
    op.drop_index("ix_data_source_audit_events_config_id", table_name="data_source_audit_events")
    op.drop_table("data_source_audit_events")
    op.drop_index("ix_data_source_probe_runs_lookup", table_name="data_source_probe_runs")
    op.drop_index("ix_data_source_probe_runs_capability", table_name="data_source_probe_runs")
    op.drop_index("ix_data_source_probe_runs_provider", table_name="data_source_probe_runs")
    op.drop_table("data_source_probe_runs")
    op.drop_index("ix_data_source_routes_capability", table_name="data_source_routes")
    op.drop_table("data_source_routes")
    op.drop_index("ix_data_source_configs_provider", table_name="data_source_configs")
    op.drop_table("data_source_configs")
