"""Real PostgreSQL 16 and Redis 7 fixtures for integration-only tests.

Every PostgreSQL fixture uses a uniquely named disposable database derived from
the explicitly supplied TEST_DATABASE_URL.  The caller's database is never
dropped or mutated.
"""

from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import pytest
import redis
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import sessionmaker


_DATABASE_NAME = re.compile(r"^aistock_task12_test_[0-9a-f]{12}$")
_MIGRATION_DATABASE_NAME = re.compile(r"^aistock_task12_migration_[0-9a-f]{12}$")


def pytest_configure(config):
    config.addinivalue_line("markers", "integration: requires real PostgreSQL and Redis services")


def _database_url() -> str:
    value = os.getenv("TEST_DATABASE_URL")
    if not value:
        pytest.skip("需要显式 TEST_DATABASE_URL")
    parsed = make_url(value)
    if parsed.get_backend_name() != "postgresql":
        pytest.fail("TEST_DATABASE_URL 必须使用 PostgreSQL")
    database = (parsed.database or "").lower()
    if database not in {"postgres", "template1"} and "test" not in database:
        pytest.fail("TEST_DATABASE_URL 只能指向 PostgreSQL admin/test 数据库")
    return value


def _redis_url() -> str:
    value = os.getenv("TEST_REDIS_URL")
    if not value:
        pytest.skip("需要显式 TEST_REDIS_URL")
    if not value.startswith("redis://"):
        pytest.fail("TEST_REDIS_URL 必须使用 redis://")
    return value


def _alembic_config(target_url: str) -> Config:
    backend_dir = Path(__file__).resolve().parents[2]
    config = Config(str(backend_dir / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", target_url)
    return config


@contextmanager
def _temporary_postgres_database(pattern: re.Pattern[str]):
    base_url = make_url(_database_url())
    admin_engine = create_engine(base_url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    prefix = "aistock_task12_test" if pattern is _DATABASE_NAME else "aistock_task12_migration"
    database_name = f"{prefix}_{uuid4().hex[:12]}"
    if not pattern.fullmatch(database_name):
        raise AssertionError("temporary database name does not match the protected pattern")
    target_url = base_url.set(database=database_name).render_as_string(hide_password=False)
    target_engine: Engine | None = None
    created = False
    previous_url = os.environ.get("TEST_DATABASE_URL")
    try:
        with admin_engine.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{database_name}"'))
        created = True
        target_engine = create_engine(target_url, pool_pre_ping=True)
        os.environ["TEST_DATABASE_URL"] = target_url
        command.upgrade(_alembic_config(target_url), "head")
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


@pytest.fixture(scope="function")
def postgres_engine():
    with _temporary_postgres_database(_DATABASE_NAME) as (_target_url, engine):
        yield engine


@pytest.fixture(scope="function")
def postgres_session_factory(postgres_engine):
    return sessionmaker(bind=postgres_engine, expire_on_commit=False, autoflush=False)


@pytest.fixture(scope="function")
def redis_client():
    client = redis.Redis.from_url(_redis_url(), decode_responses=True)
    try:
        client.ping()
    except Exception:
        client.close()
        raise
    try:
        yield client
    finally:
        client.close()


@dataclass
class _MigrationCycle:
    target_url: str
    engine: Engine

    def _run_with_target(self, callback, *args):
        previous_url = os.environ.get("TEST_DATABASE_URL")
        os.environ["TEST_DATABASE_URL"] = self.target_url
        try:
            return callback(*args)
        finally:
            if previous_url is None:
                os.environ.pop("TEST_DATABASE_URL", None)
            else:
                os.environ["TEST_DATABASE_URL"] = previous_url

    def upgrade(self):
        return self._run_with_target(command.upgrade, _alembic_config(self.target_url), "head")

    def downgrade(self):
        return self._run_with_target(command.downgrade, _alembic_config(self.target_url), "d7e8f9a0b1c2")

    def seed_legacy_task(self):
        from app.models.task_record import TaskRecord
        from app.models.user import User

        factory = sessionmaker(bind=self.engine, expire_on_commit=False)
        with factory() as db:
            db.add(User(username="task12-legacy", email="task12-legacy@example.test", password_hash="test-only"))
            db.flush()
            db.add(TaskRecord(task_type="legacy", status="pending", user_id=None))
            db.commit()

    def legacy_task_exists(self) -> bool:
        from app.models.task_record import TaskRecord

        factory = sessionmaker(bind=self.engine, expire_on_commit=False)
        with factory() as db:
            return db.query(TaskRecord).filter(TaskRecord.task_type == "legacy").count() == 1


class _IsolatedRedis:
    def __init__(self, binary: str, port: int, data_dir: str):
        self.binary = binary
        self.port = port
        self.data_dir = data_dir
        self.process: subprocess.Popen | None = None

    @property
    def url(self) -> str:
        return f"redis://127.0.0.1:{self.port}/15"

    def start(self) -> None:
        self.process = subprocess.Popen(
            [
                self.binary,
                "--bind",
                "127.0.0.1",
                "--port",
                str(self.port),
                "--save",
                "",
                "--appendonly",
                "no",
                "--dir",
                self.data_dir,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        client = redis.Redis.from_url(self.url, decode_responses=True)
        try:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                try:
                    if client.ping():
                        return
                except redis.exceptions.ConnectionError:
                    time.sleep(0.05)
            raise RuntimeError("isolated redis-server did not become ready")
        finally:
            client.close()

    def stop(self) -> None:
        if self.process is None or self.process.poll() is not None:
            return
        self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)


@pytest.fixture(scope="function")
def isolated_redis_server(tmp_path):
    binary = shutil.which("redis-server")
    if not binary:
        pytest.skip("integration Redis restart requires redis-server binary")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = int(probe.getsockname()[1])
    server = _IsolatedRedis(binary, port, str(tmp_path))
    server.start()
    try:
        yield server
    finally:
        server.stop()


@pytest.fixture(scope="function")
def migration_cycle():
    base_url = make_url(_database_url())
    admin_engine = create_engine(base_url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    database_name = f"aistock_task12_migration_{uuid4().hex[:12]}"
    if not _MIGRATION_DATABASE_NAME.fullmatch(database_name):
        raise AssertionError("temporary migration database name is not protected")
    target_url = base_url.set(database=database_name).render_as_string(hide_password=False)
    engine = None
    created = False
    try:
        with admin_engine.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{database_name}"'))
        created = True
        engine = create_engine(target_url, pool_pre_ping=True)
        yield _MigrationCycle(target_url, engine)
    finally:
        if engine is not None:
            engine.dispose()
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


__all__ = ["migration_cycle", "postgres_engine", "postgres_session_factory", "redis_client"]
