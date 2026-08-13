"""PostgreSQL-only upgrade/downgrade coverage for the model-center migration."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import OperationalError


MIGRATION_REVISION = "20260813_01"
PARENT_REVISION = "d7e8f9a0b1c2"


def _database_url() -> str:
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("set TEST_DATABASE_URL to run PostgreSQL migration integration tests")
    return url


def _alembic_config() -> Config:
    backend_dir = Path(__file__).resolve().parents[2]
    config = Config(str(backend_dir / "alembic.ini"))
    # env.py reads TEST_DATABASE_URL so that the repository settings are not
    # accidentally used for an integration database.
    config.set_main_option("sqlalchemy.url", _database_url())
    return config


@pytest.fixture(scope="module")
def postgres_engine():
    url = _database_url()
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except OperationalError as exc:
        engine.dispose()
        pytest.skip(f"PostgreSQL integration database unavailable: {exc}")
    yield engine
    engine.dispose()


def test_upgrade_schema_downgrade_and_reupgrade(postgres_engine):
    config = _alembic_config()
    command.upgrade(config, "head")

    inspector = inspect(postgres_engine)
    expected_tables = {
        "llm_model_configs",
        "llm_runtime_settings",
        "llm_model_test_runs",
        "llm_activation_requests",
        "llm_admin_audit_events",
        "llm_daily_budgets",
        "llm_token_reservations",
        "llm_call_attempts",
        "task_outbox",
    }
    assert expected_tables <= set(inspector.get_table_names())

    task_columns = {column["name"] for column in inspector.get_columns("task_records")}
    usage_columns = {column["name"] for column in inspector.get_columns("llm_usage")}
    assert {
        "model_config_id",
        "input_snapshot",
        "input_snapshot_hash",
        "prompt_version",
        "execution_token",
        "lease_expires_at",
        "heartbeat_at",
    } <= task_columns
    assert {
        "task_id",
        "model_config_id",
        "provider_snapshot",
        "model_snapshot",
        "input_price_snapshot",
        "output_price_snapshot",
        "input_tokens",
        "output_tokens",
        "cost_micro_yuan",
        "status",
        "error_code",
    } <= usage_columns

    index_names = {
        index["name"]
        for index in inspector.get_indexes("task_outbox")
        + inspector.get_indexes("llm_call_attempts")
        + inspector.get_indexes("llm_model_test_runs")
    }
    assert "ix_task_outbox_pending_available" in index_names
    assert "ix_llm_call_attempts_created_config_status" in index_names
    assert "ix_llm_model_test_runs_config_created" in index_names

    # The migration is additive: old fields remain available and old rows can
    # be inserted while the new worker is being rolled out.
    with postgres_engine.begin() as connection:
        legacy_count_before = connection.execute(
            text("SELECT count(*) FROM task_records WHERE task_type = 'migration_legacy'")
        ).scalar_one()
        connection.execute(
            text(
                "INSERT INTO task_records (task_type, status, progress) "
                "VALUES ('migration_legacy', 'pending', 0)"
            )
        )

    command.downgrade(config, PARENT_REVISION)
    downgraded_inspector = inspect(postgres_engine)
    assert not expected_tables & set(downgraded_inspector.get_table_names())
    assert "task_records" in downgraded_inspector.get_table_names()
    downgraded_task_columns = {
        column["name"] for column in downgraded_inspector.get_columns("task_records")
    }
    assert "model_config_id" not in downgraded_task_columns

    with postgres_engine.connect() as connection:
        assert connection.execute(
            text("SELECT count(*) FROM task_records WHERE task_type = 'migration_legacy'")
        ).scalar_one() == legacy_count_before + 1

    command.upgrade(config, "head")
    assert expected_tables <= set(inspect(postgres_engine).get_table_names())
