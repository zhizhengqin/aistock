"""PostgreSQL-only, isolated upgrade/downgrade coverage for the migration."""

from __future__ import annotations

import os
import re
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import OperationalError

from app.core.config import settings


MIGRATION_REVISION = "20260813_01"
PARENT_REVISION = "d7e8f9a0b1c2"


def _database_url() -> str:
    url = os.getenv("TEST_DATABASE_URL")
    if not url:
        pytest.skip("set TEST_DATABASE_URL to run PostgreSQL migration integration tests")
    parsed = make_url(url)
    if parsed.get_backend_name() != "postgresql":
        pytest.fail("TEST_DATABASE_URL must use a PostgreSQL driver")

    configured = make_url(settings.DATABASE_URL)
    if parsed == configured:
        pytest.fail("TEST_DATABASE_URL must not equal the configured application database")

    database = (parsed.database or "").lower()
    is_admin_database = database in {"postgres", "template1"}
    is_test_database = bool(
        re.search(r"(?:^|[_-])(test|testing|ci|migration)(?:$|[_-])", database)
    )
    if not database or not (is_admin_database or is_test_database):
        pytest.fail(
            "TEST_DATABASE_URL must point to a PostgreSQL admin/test database; "
            "the provided database is never used as the migration target"
        )
    return url


def _alembic_config(target_url: str) -> Config:
    backend_dir = Path(__file__).resolve().parents[2]
    config = Config(str(backend_dir / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", target_url)
    return config


def _quote_database_identifier(database: str) -> str:
    # Names are generated locally from a fixed prefix and UUID hex.  Keep this
    # guard next to the SQL string so no caller-controlled identifier reaches
    # CREATE/DROP DATABASE.
    if not re.fullmatch(r"aistock_migration_test_[0-9a-f]+", database):
        raise ValueError("unexpected disposable database identifier")
    return f'"{database}"'


@contextmanager
def _disposable_database():
    base_url = make_url(_database_url())
    admin_engine = create_engine(base_url, isolation_level="AUTOCOMMIT")
    database_name = f"aistock_migration_test_{uuid4().hex}"
    quoted_name = _quote_database_identifier(database_name)
    target_engine = None
    previous_test_url = os.environ.get("TEST_DATABASE_URL")
    created = False

    try:
        try:
            with admin_engine.connect() as connection:
                connection.execute(text("SELECT 1"))
        except OperationalError as exc:
            pytest.skip(f"PostgreSQL integration database unavailable: {exc}")

        with admin_engine.connect() as connection:
            connection.execute(text(f"CREATE DATABASE {quoted_name}"))
        created = True

        target_url: URL = base_url.set(database=database_name)
        target_url_text = target_url.render_as_string(hide_password=False)
        # alembic/env.py intentionally reads this variable, so both the
        # command config and env.py point at the generated disposable DB.
        os.environ["TEST_DATABASE_URL"] = target_url_text
        target_engine = create_engine(target_url)
        yield target_url_text, target_engine
    finally:
        if previous_test_url is None:
            os.environ.pop("TEST_DATABASE_URL", None)
        else:
            os.environ["TEST_DATABASE_URL"] = previous_test_url

        if target_engine is not None:
            target_engine.dispose()
        if created:
            # Only terminate sessions connected to the generated database;
            # the caller-provided admin/test database is never terminated.
            with admin_engine.connect() as connection:
                connection.execute(
                    text(
                        "SELECT pg_terminate_backend(pid) "
                        "FROM pg_stat_activity "
                        "WHERE datname = :database_name "
                        "AND pid <> pg_backend_pid()"
                    ),
                    {"database_name": database_name},
                )
                connection.execute(text(f"DROP DATABASE IF EXISTS {quoted_name}"))
        admin_engine.dispose()


@pytest.fixture(scope="module")
def disposable_database():
    with _disposable_database() as database:
        yield database


def _normalise_predicate(predicate: object) -> str:
    value = str(predicate).lower()
    value = re.sub(r"::[a-z_]+", "", value)
    return re.sub(r"[\s\"'`()]+", "", value)


def _assert_target_indexes(inspector):
    expected = {
        "task_outbox": (
            "ix_task_outbox_pending_available",
            ["available_at", "id"],
        ),
        "llm_call_attempts": (
            "ix_llm_call_attempts_created_config_status",
            ["created_at", "model_config_id", "status"],
        ),
        "llm_model_test_runs": (
            "ix_llm_model_test_runs_config_created",
            ["model_config_id", "created_at"],
        ),
        "llm_usage": (
            "ix_llm_usage_created_config",
            ["created_at", "model_config_id"],
        ),
        "task_records": (
            "ix_task_records_model_config_status",
            ["model_config_id", "status"],
        ),
    }
    for table_name, (index_name, columns) in expected.items():
        indexes = {index["name"]: index for index in inspector.get_indexes(table_name)}
        assert index_name in indexes
        assert indexes[index_name]["column_names"] == columns

    outbox_predicate = next(
        index["dialect_options"].get("postgresql_where", "")
        for index in inspector.get_indexes("task_outbox")
        if index["name"] == "ix_task_outbox_pending_available"
    )
    assert _normalise_predicate(outbox_predicate) == "status=pending"


def test_upgrade_schema_downgrade_and_reupgrade(disposable_database):
    target_url, target_engine = disposable_database
    config = _alembic_config(target_url)
    command.upgrade(config, "head")

    inspector = inspect(target_engine)
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
    _assert_target_indexes(inspector)

    # The migration is additive: old fields remain available while the new
    # worker is rolled out, and scheduled usage legitimately has no user.
    with target_engine.begin() as connection:
        legacy_task_id = connection.execute(
            text(
                "INSERT INTO task_records (task_type, status, progress) "
                "VALUES ('migration_legacy', 'pending', 0) RETURNING id"
            )
        ).scalar_one()
        legacy_usage_id = connection.execute(
            text(
                "INSERT INTO llm_usage "
                "(user_id, module, model, prompt_tokens, completion_tokens, cost_fen) "
                "VALUES (NULL, 'migration_legacy', 'deepseek-chat', 0, 0, 0) "
                "RETURNING id"
            )
        ).scalar_one()

    command.downgrade(config, PARENT_REVISION)
    downgraded_inspector = inspect(target_engine)
    assert not expected_tables & set(downgraded_inspector.get_table_names())
    assert "task_records" in downgraded_inspector.get_table_names()
    downgraded_task_columns = {
        column["name"] for column in downgraded_inspector.get_columns("task_records")
    }
    assert "model_config_id" not in downgraded_task_columns

    with target_engine.connect() as connection:
        assert connection.execute(
            text("SELECT id FROM task_records WHERE id = :task_id"),
            {"task_id": legacy_task_id},
        ).scalar_one() == legacy_task_id
        assert connection.execute(
            text("SELECT user_id FROM llm_usage WHERE id = :usage_id"),
            {"usage_id": legacy_usage_id},
        ).scalar_one() is None

    command.upgrade(config, "head")
    assert expected_tables <= set(inspect(target_engine).get_table_names())
