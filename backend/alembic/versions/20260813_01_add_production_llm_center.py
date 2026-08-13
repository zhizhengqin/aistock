"""Add production LLM model-center tables and compatibility columns.

This is the first phase of the migration.  Everything added to the existing
``task_records`` and ``llm_usage`` tables is nullable so old API/worker
versions can continue to read and write during a mixed-version rollout.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "20260813_01"
down_revision: Union[str, None] = "d7e8f9a0b1c2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "llm_model_configs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("provider", sa.String(length=16), nullable=False),
        sa.Column("display_name", sa.String(length=128), nullable=False),
        sa.Column("model_name", sa.String(length=128), nullable=False),
        sa.Column("base_url", sa.String(length=512), nullable=False),
        sa.Column("encrypted_api_key", sa.Text(), nullable=False),
        sa.Column("encryption_key_id", sa.String(length=128), nullable=False),
        sa.Column("envelope_version", sa.String(length=16), nullable=False),
        sa.Column("nonce", sa.String(length=128), nullable=False),
        sa.Column("credential_version", sa.String(length=64), nullable=False),
        sa.Column("key_hint", sa.String(length=64), nullable=True),
        sa.Column("input_price_micro_yuan_per_million", sa.Integer(), nullable=True),
        sa.Column("output_price_micro_yuan_per_million", sa.Integer(), nullable=True),
        sa.Column("max_output_tokens", sa.Integer(), nullable=True),
        sa.Column(
            "lifecycle_status",
            sa.String(length=16),
            server_default=sa.text("'draft'"),
            nullable=False,
        ),
        sa.Column("supersedes_id", sa.String(length=36), nullable=True),
        sa.Column("runtime_fingerprint", sa.String(length=128), nullable=False),
        sa.Column("verified_test_id", sa.String(length=36), nullable=True),
        sa.Column("last_probe_status", sa.String(length=32), nullable=True),
        sa.Column("last_probe_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_probe_latency_ms", sa.Integer(), nullable=True),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["supersedes_id"], ["llm_model_configs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "input_price_micro_yuan_per_million IS NULL OR input_price_micro_yuan_per_million >= 0",
            name="ck_llm_model_configs_input_price_nonnegative",
        ),
        sa.CheckConstraint(
            "output_price_micro_yuan_per_million IS NULL OR output_price_micro_yuan_per_million >= 0",
            name="ck_llm_model_configs_output_price_nonnegative",
        ),
    )
    op.create_index(
        "ix_llm_model_configs_provider_status",
        "llm_model_configs",
        ["provider", "lifecycle_status"],
    )
    op.create_index("ix_llm_model_configs_created_by", "llm_model_configs", ["created_by"])

    op.create_table(
        "llm_runtime_settings",
        sa.Column("id", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("default_model_config_id", sa.String(length=36), nullable=True),
        sa.Column(
            "daily_token_limit",
            sa.Integer(),
            server_default=sa.text("2000000"),
            nullable=False,
        ),
        sa.Column("budget_locked", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("switched_by", sa.Integer(), nullable=True),
        sa.Column("switched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["default_model_config_id"], ["llm_model_configs.id"]),
        sa.ForeignKeyConstraint(["switched_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("id = 1", name="ck_llm_runtime_settings_singleton"),
        sa.CheckConstraint(
            "daily_token_limit > 0", name="ck_llm_runtime_settings_positive_limit"
        ),
    )

    op.create_table(
        "llm_model_test_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("model_config_id", sa.String(length=36), nullable=True),
        sa.Column("runtime_fingerprint", sa.String(length=128), nullable=False),
        sa.Column("test_type", sa.String(length=32), server_default=sa.text("'probe'"), nullable=False),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("capability_json", sa.JSON(), nullable=True),
        sa.Column("result_json", sa.JSON(), nullable=True),
        sa.Column("response_model", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=32), server_default=sa.text("'started'"), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["model_config_id"], ["llm_model_configs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_llm_model_test_runs_config_created",
        "llm_model_test_runs",
        ["model_config_id", "created_at"],
    )

    op.create_table(
        "llm_activation_requests",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("model_config_id", sa.String(length=36), nullable=False),
        sa.Column("expected_version", sa.Integer(), nullable=True),
        sa.Column("created_by", sa.Integer(), nullable=True),
        sa.Column("response_json", sa.JSON(), nullable=True),
        sa.Column("status", sa.String(length=32), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["model_config_id"], ["llm_model_configs.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
    )
    op.create_index(
        "ix_llm_activation_requests_config_created",
        "llm_activation_requests",
        ["model_config_id", "created_at"],
    )

    op.create_table(
        "llm_admin_audit_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("admin_user_id", sa.Integer(), nullable=True),
        sa.Column("model_config_id", sa.String(length=36), nullable=True),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("runtime_settings_version", sa.Integer(), nullable=True),
        sa.Column("payload_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["admin_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["model_config_id"], ["llm_model_configs.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_llm_admin_audit_events_created", "llm_admin_audit_events", ["created_at"])

    op.create_table(
        "llm_daily_budgets",
        sa.Column("budget_date", sa.Date(), nullable=False),
        sa.Column("reserved_tokens", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("settled_tokens", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("budget_date"),
        sa.CheckConstraint("reserved_tokens >= 0", name="ck_llm_daily_budgets_reserved_nonnegative"),
        sa.CheckConstraint("settled_tokens >= 0", name="ck_llm_daily_budgets_settled_nonnegative"),
    )

    op.create_table(
        "llm_token_reservations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=True),
        sa.Column("step_key", sa.String(length=128), nullable=False),
        sa.Column("budget_date", sa.Date(), nullable=False),
        sa.Column("reserved_tokens", sa.BigInteger(), nullable=False),
        sa.Column("settled_tokens", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("status", sa.String(length=32), server_default=sa.text("'reserved'"), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["task_records.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "reserved_tokens >= 0", name="ck_llm_token_reservations_reserved_nonnegative"
        ),
        sa.CheckConstraint(
            "settled_tokens >= 0", name="ck_llm_token_reservations_settled_nonnegative"
        ),
    )
    op.create_index(
        "ix_llm_token_reservations_budget_status",
        "llm_token_reservations",
        ["budget_date", "status"],
    )

    op.create_table(
        "llm_call_attempts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=True),
        sa.Column("model_config_id", sa.String(length=36), nullable=True),
        sa.Column("operation_id", sa.String(length=36), nullable=False),
        sa.Column("operation_type", sa.String(length=32), nullable=False),
        sa.Column("step_key", sa.String(length=128), nullable=False),
        sa.Column("attempt_no", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("provider_snapshot", sa.String(length=16), nullable=False),
        sa.Column("model_snapshot", sa.String(length=128), nullable=False),
        sa.Column("runtime_fingerprint", sa.String(length=128), nullable=False),
        sa.Column("input_snapshot_hash", sa.String(length=64), nullable=True),
        sa.Column("prompt_version", sa.String(length=64), nullable=True),
        sa.Column("reservation_id", sa.String(length=36), nullable=True),
        sa.Column("status", sa.String(length=32), server_default=sa.text("'started'"), nullable=False),
        sa.Column("response_model_snapshot", sa.String(length=128), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("input_price_snapshot", sa.Integer(), nullable=True),
        sa.Column("output_price_snapshot", sa.Integer(), nullable=True),
        sa.Column("cost_micro_yuan", sa.BigInteger(), nullable=True),
        sa.Column("usage_source", sa.String(length=32), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("result_json", sa.JSON(), nullable=True),
        sa.Column("result_hash", sa.String(length=64), nullable=True),
        sa.Column("result_schema_version", sa.String(length=64), nullable=True),
        sa.Column("response_metadata_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["model_config_id"], ["llm_model_configs.id"]),
        sa.ForeignKeyConstraint(["reservation_id"], ["llm_token_reservations.id"]),
        sa.ForeignKeyConstraint(["task_id"], ["task_records.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("operation_id", name="uq_llm_call_attempt_operation_id"),
        sa.UniqueConstraint(
            "task_id", "step_key", "attempt_no", name="uq_llm_call_attempt_task_step_no"
        ),
    )
    op.create_index(
        "ix_llm_call_attempts_created_config_status",
        "llm_call_attempts",
        ["created_at", "model_config_id", "status"],
    )

    op.create_table(
        "task_outbox",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("task_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("locked_by", sa.String(length=128), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["task_id"], ["task_records.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("task_id"),
    )
    op.create_index(
        "ix_task_outbox_pending_available",
        "task_outbox",
        ["available_at", "id"],
        postgresql_where=sa.text("status = 'pending'"),
    )

    # Existing tables receive only nullable compatibility columns.  Existing
    # fields stay intact for old API/worker binaries during rollout.
    op.add_column(
        "task_records",
        sa.Column("model_config_id", sa.String(length=36), nullable=True),
    )
    op.create_foreign_key(
        "fk_task_records_model_config_id",
        "task_records",
        "llm_model_configs",
        ["model_config_id"],
        ["id"],
    )
    op.add_column("task_records", sa.Column("input_snapshot", sa.JSON(), nullable=True))
    op.add_column(
        "task_records", sa.Column("input_snapshot_hash", sa.String(length=64), nullable=True)
    )
    op.add_column(
        "task_records", sa.Column("prompt_version", sa.String(length=64), nullable=True)
    )
    op.add_column(
        "task_records", sa.Column("execution_token", sa.String(length=36), nullable=True)
    )
    op.add_column(
        "task_records", sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "task_records", sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_index(
        "ix_task_records_model_config_status",
        "task_records",
        ["model_config_id", "status"],
    )

    op.alter_column("llm_usage", "user_id", existing_type=sa.Integer(), nullable=True)
    op.add_column("llm_usage", sa.Column("task_id", sa.Integer(), nullable=True))
    op.create_foreign_key("fk_llm_usage_task_id", "llm_usage", "task_records", ["task_id"], ["id"])
    op.add_column("llm_usage", sa.Column("model_config_id", sa.String(length=36), nullable=True))
    op.create_foreign_key(
        "fk_llm_usage_model_config_id",
        "llm_usage",
        "llm_model_configs",
        ["model_config_id"],
        ["id"],
    )
    op.add_column("llm_usage", sa.Column("provider_snapshot", sa.String(length=16), nullable=True))
    op.add_column("llm_usage", sa.Column("model_snapshot", sa.String(length=64), nullable=True))
    op.add_column("llm_usage", sa.Column("input_price_snapshot", sa.Integer(), nullable=True))
    op.add_column("llm_usage", sa.Column("output_price_snapshot", sa.Integer(), nullable=True))
    op.add_column("llm_usage", sa.Column("input_tokens", sa.Integer(), nullable=True))
    op.add_column("llm_usage", sa.Column("output_tokens", sa.Integer(), nullable=True))
    op.add_column("llm_usage", sa.Column("cost_micro_yuan", sa.BigInteger(), nullable=True))
    op.add_column("llm_usage", sa.Column("status", sa.String(length=32), nullable=True))
    op.add_column("llm_usage", sa.Column("error_code", sa.String(length=64), nullable=True))
    op.create_index(
        "ix_llm_usage_created_config", "llm_usage", ["created_at", "model_config_id"]
    )


def downgrade() -> None:
    # Remove only this migration's additions.  Legacy columns/tables remain
    # otherwise untouched, and all new tables are dropped before their FKs.
    op.drop_index("ix_llm_usage_created_config", table_name="llm_usage")
    op.drop_constraint("fk_llm_usage_model_config_id", "llm_usage", type_="foreignkey")
    op.drop_constraint("fk_llm_usage_task_id", "llm_usage", type_="foreignkey")
    for column in (
        "error_code",
        "status",
        "cost_micro_yuan",
        "output_tokens",
        "input_tokens",
        "output_price_snapshot",
        "input_price_snapshot",
        "model_snapshot",
        "provider_snapshot",
        "model_config_id",
        "task_id",
    ):
        op.drop_column("llm_usage", column)
    op.alter_column("llm_usage", "user_id", existing_type=sa.Integer(), nullable=False)

    op.drop_index("ix_task_records_model_config_status", table_name="task_records")
    op.drop_constraint("fk_task_records_model_config_id", "task_records", type_="foreignkey")
    for column in (
        "heartbeat_at",
        "lease_expires_at",
        "execution_token",
        "prompt_version",
        "input_snapshot_hash",
        "input_snapshot",
        "model_config_id",
    ):
        op.drop_column("task_records", column)

    op.drop_index("ix_task_outbox_pending_available", table_name="task_outbox")
    op.drop_table("task_outbox")
    op.drop_index("ix_llm_call_attempts_created_config_status", table_name="llm_call_attempts")
    op.drop_table("llm_call_attempts")
    op.drop_index("ix_llm_token_reservations_budget_status", table_name="llm_token_reservations")
    op.drop_table("llm_token_reservations")
    op.drop_table("llm_daily_budgets")
    op.drop_index("ix_llm_admin_audit_events_created", table_name="llm_admin_audit_events")
    op.drop_table("llm_admin_audit_events")
    op.drop_index("ix_llm_activation_requests_config_created", table_name="llm_activation_requests")
    op.drop_table("llm_activation_requests")
    op.drop_index("ix_llm_model_test_runs_config_created", table_name="llm_model_test_runs")
    op.drop_table("llm_model_test_runs")
    op.drop_table("llm_runtime_settings")
    op.drop_index("ix_llm_model_configs_created_by", table_name="llm_model_configs")
    op.drop_index("ix_llm_model_configs_provider_status", table_name="llm_model_configs")
    op.drop_table("llm_model_configs")
