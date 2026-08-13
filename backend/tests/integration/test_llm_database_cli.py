"""Real PostgreSQL migration/readiness evidence for the Task5 database CLI."""

from __future__ import annotations

import os
import re
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from app.cli import database


_DATABASE_PATTERN = re.compile(r"^aistock_llm_database_test_[0-9a-f]{12}$")


def _admin_url():
    value = os.getenv("TEST_DATABASE_URL")
    if not value:
        pytest.skip("需要显式 TEST_DATABASE_URL")
    parsed = make_url(value)
    if parsed.get_backend_name() != "postgresql":
        pytest.fail("TEST_DATABASE_URL 必须使用 PostgreSQL")
    if (parsed.database or "").lower() not in {"postgres", "template1"} and "test" not in (
        parsed.database or ""
    ).lower():
        pytest.fail("TEST_DATABASE_URL 只能指向 PostgreSQL admin/test 数据库")
    return parsed


@contextmanager
def _disposable_database():
    admin_url = _admin_url()
    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    database_name = f"aistock_llm_database_test_{uuid4().hex[:12]}"
    assert _DATABASE_PATTERN.fullmatch(database_name)
    target_engine = None
    previous_url = os.environ.get("TEST_DATABASE_URL")
    created = False
    try:
        with admin_engine.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{database_name}"'))
        created = True
        target_url = admin_url.set(database=database_name).render_as_string(hide_password=False)
        os.environ["TEST_DATABASE_URL"] = target_url
        target_engine = create_engine(target_url)
        yield target_url, target_engine
    finally:
        if previous_url is None:
            os.environ.pop("TEST_DATABASE_URL", None)
        else:
            os.environ["TEST_DATABASE_URL"] = previous_url
        if target_engine is not None:
            target_engine.dispose()
        if created:
            with admin_engine.connect() as connection:
                connection.execute(
                    text(
                        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                        "WHERE datname = :name AND pid <> pg_backend_pid()"
                    ),
                    {"name": database_name},
                )
                connection.execute(text(f'DROP DATABASE IF EXISTS "{database_name}"'))
        admin_engine.dispose()


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要显式 TEST_DATABASE_URL")
def test_real_migrate_and_wait_for_exact_heads_from_empty_and_old_revision():
    with _disposable_database() as (_target_url, _target_engine):
        # The CLI, not a direct Alembic command, owns both upgrade and exact
        # head verification.  A brand-new database must fail readiness first.
        assert database.run_wait_for_head().exit_code != 0
        migrated = database.run_migrate()
        assert migrated.exit_code == 0, migrated.message
        assert database.run_wait_for_head().exit_code == 0


    # A separate protected database is genuinely upgraded only to the previous
    # revision.  The exact-head wait must fail there, while the migrator then
    # applies the remaining real migration without duplicate-DDL tricks.
    with _disposable_database() as (_old_target_url, _old_target_engine):
        script = ScriptDirectory.from_config(database._alembic_config())
        current = script.get_current_head()
        revision = script.get_revision(current)
        parent = revision.down_revision
        old_revision = parent[0] if isinstance(parent, tuple) else parent
        assert old_revision
        old_config = database._alembic_config()
        command.upgrade(old_config, old_revision)
        assert database.run_wait_for_head().exit_code != 0
        upgraded = database.run_migrate()
        assert upgraded.exit_code == 0, upgraded.message
        assert database.run_wait_for_head().exit_code == 0
