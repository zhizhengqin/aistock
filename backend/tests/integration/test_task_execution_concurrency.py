"""PostgreSQL concurrency evidence for TaskExecutionRunner fencing."""

from __future__ import annotations

import asyncio
import importlib
import os
import pkgutil
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from app.models.base import Base
from app.models.task_record import TaskRecord
from app.services.task_execution import TaskExecutionFenced, TaskExecutionRunner


_DB_NAME_PATTERN = re.compile(r"^aistock_task_execution_test_[0-9a-f]{12}$")


def _import_all_models():
    import app.models

    for module in pkgutil.iter_modules(app.models.__path__):
        importlib.import_module(f"app.models.{module.name}")


@contextmanager
def _temporary_database(database_url: str):
    base_url = make_url(database_url)
    if base_url.get_backend_name() != "postgresql":
        pytest.fail("TEST_DATABASE_URL 必须使用 PostgreSQL")
    admin_engine = create_engine(base_url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    database_name = f"aistock_task_execution_test_{uuid.uuid4().hex[:12]}"
    assert _DB_NAME_PATTERN.fullmatch(database_name)
    engine = None
    created = False
    try:
        with admin_engine.connect() as admin:
            admin.execute(text(f'CREATE DATABASE "{database_name}"'))
        created = True
        engine = create_engine(base_url.set(database=database_name), pool_size=30, max_overflow=20)
        _import_all_models()
        Base.metadata.create_all(engine)
        yield engine
    finally:
        if engine is not None:
            engine.dispose()
        if created:
            with admin_engine.connect() as admin:
                admin.execute(
                    text(
                        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                        "WHERE datname = :name AND pid <> pg_backend_pid()"
                    ),
                    {"name": database_name},
                )
                admin.execute(text(f'DROP DATABASE IF EXISTS "{database_name}"'))
        admin_engine.dispose()


pytestmark = pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"),
    reason="需要显式 TEST_DATABASE_URL 指向 disposable PostgreSQL",
)


def _seed(factory) -> int:
    with factory() as db:
        task = TaskRecord(
            task_type="stock_analysis",
            status="pending",
            progress=0,
            input_snapshot={"_args": {"stock_code": "600519"}},
            prompt_version="pg-runner-v1",
        )
        db.add(task)
        db.commit()
        return int(task.id)


def test_postgres_twenty_deliveries_have_one_execution():
    with _temporary_database(os.environ["TEST_DATABASE_URL"]) as engine:
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        task_id = _seed(factory)
        start = threading.Barrier(20)
        release = threading.Event()
        calls = 0
        calls_lock = threading.Lock()

        def worker(_index: int):
            nonlocal calls
            runner = TaskExecutionRunner(factory, lease_seconds=30, heartbeat_interval_seconds=60)

            async def execute(ctx):
                nonlocal calls
                with calls_lock:
                    calls += 1
                await asyncio.to_thread(release.wait, 3)
                return {"winner": ctx.execution_token}

            start.wait(timeout=10)
            return asyncio.run(runner.run(task_id, execute, lambda db, task, result: None))

        with ThreadPoolExecutor(max_workers=20) as pool:
            futures = [pool.submit(worker, i) for i in range(20)]
            # Give all claimers time to reach PostgreSQL while the winner is
            # held in business work; then let the single owner finish.
            time.sleep(0.25)
            release.set()
            results = [future.result(timeout=10) for future in futures]

        assert calls == 1
        assert sum(result is not None for result in results) == 1
        with Session(engine) as db:
            task = db.get(TaskRecord, task_id)
            assert task.status == "success"


@pytest.mark.asyncio
async def test_postgres_live_old_owner_is_fenced_after_reclaim():
    with _temporary_database(os.environ["TEST_DATABASE_URL"]) as engine:
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        task_id = _seed(factory)
        runner = TaskExecutionRunner(factory, lease_seconds=2, heartbeat_interval_seconds=60)
        started = asyncio.Event()
        release_old = asyncio.Event()

        async def old_execute(ctx):
            started.set()
            await release_old.wait()
            await ctx.ensure_current()
            return {"old": True}

        old_run = asyncio.create_task(runner.run(task_id, old_execute, lambda db, task, result: None))
        await started.wait()
        with Session(engine) as db:
            db.execute(
                update(TaskRecord)
                .where(TaskRecord.id == task_id)
                .values(lease_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
            )
            db.commit()

        new_claim = runner._claim(task_id).context
        assert new_claim is not None
        release_old.set()
        with pytest.raises(TaskExecutionFenced):
            await old_run

        await runner._persist_success(new_claim, {"new": True}, lambda db, task, result: None)
        with Session(engine) as db:
            task = db.get(TaskRecord, task_id)
            assert task.status == "success"
            assert task.result_json == {"new": True}
