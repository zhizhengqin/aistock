"""PostgreSQL process-race evidence for the one-shot bootstrap lock."""

from __future__ import annotations

import asyncio
import base64
import multiprocessing
import os
import re
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker


_DATABASE_PATTERN = re.compile(r"^aistock_llm_bootstrap_test_[0-9a-f]{12}$")


def _admin_url() -> str:
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


@contextmanager
def _disposable_database():
    admin_url = make_url(_admin_url())
    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    database_name = f"aistock_llm_bootstrap_test_{uuid4().hex[:12]}"
    assert _DATABASE_PATTERN.fullmatch(database_name)
    target_engine = None
    created = False
    previous_test_url = os.environ.get("TEST_DATABASE_URL")
    try:
        with admin_engine.connect() as connection:
            connection.execute(text(f'CREATE DATABASE "{database_name}"'))
        created = True
        target_url = admin_url.set(database=database_name)
        target_url_text = target_url.render_as_string(hide_password=False)
        os.environ["TEST_DATABASE_URL"] = target_url_text
        target_engine = create_engine(target_url)
        backend_dir = Path(__file__).resolve().parents[2]
        config = Config(str(backend_dir / "alembic.ini"))
        config.set_main_option("sqlalchemy.url", target_url_text)
        command.upgrade(config, "head")
        yield target_url_text, database_name
    finally:
        if previous_test_url is None:
            os.environ.pop("TEST_DATABASE_URL", None)
        else:
            os.environ["TEST_DATABASE_URL"] = previous_test_url
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


def _bootstrap_process(
    target_url: str,
    api_key: str,
    start_event,
    result_queue,
    response_status: int,
) -> None:
    """Run one independent process with its own SQLAlchemy connection pool."""

    from app.core.config import settings

    settings.DATABASE_URL = target_url
    settings.ENV = "test"
    settings.LLM_CONFIG_ENCRYPTION_KEY_ID = "race-current"
    settings.LLM_CONFIG_ENCRYPTION_KEYS = {
        "race-current": base64.b64encode(b"r" * 32).decode("ascii")
    }
    settings.DEEPSEEK_API_KEY = api_key
    settings.LLM_MODEL = "deepseek-chat"
    settings.LLM_BASE_URL = "https://api.deepseek.com/v1"
    settings.DAILY_TOKEN_LIMIT = 100_000

    # Import FK targets before SQLAlchemy first configures the model-center
    # mappers; the CLI itself intentionally does not mutate global model
    # registration just to start a one-shot command.
    from app.models import llm_config as _llm_config_models  # noqa: F401
    from app.models import llm_execution as _llm_execution_models  # noqa: F401
    from app.models import llm_usage as _llm_usage_models  # noqa: F401
    from app.models import task_record as _task_record_models  # noqa: F401
    from app.models import user as _user_models  # noqa: F401
    from app.cli import llm_config
    from app.services.llm.budget import TokenBudgetService
    from app.services.llm.call_executor import LlmCallExecutor
    from app.services.llm.provider_client import ProviderClient

    engine = create_engine(target_url, pool_size=2, max_overflow=0)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        # Keep the first process in the network phase while the second process
        # attempts to acquire the advisory lock and performs its second empty
        # check.  Candidate insertion already committed before this call.
        await asyncio.sleep(0.15)
        if response_status == 200:
            return httpx.Response(
                200,
                json={
                    "model": "deepseek-chat",
                    "choices": [
                        {
                            "message": {
                                "content": (
                                    '{"decision":"hold","confidence":0.5,'
                                    '"rationale":"race probe"}'
                                )
                            }
                        }
                    ],
                    "usage": {"prompt_tokens": 8, "completion_tokens": 4},
                },
                request=request,
            )
        return httpx.Response(
            response_status,
            json={"error": {"type": "invalid_request_error", "message": "invalid key"}},
            request=request,
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)
    provider_client = ProviderClient(client=client)
    with factory() as db:
        executor = LlmCallExecutor(
            db=db,
            provider_client=provider_client,
            budget=TokenBudgetService(db, daily_token_limit=100_000),
        )
        start_event.wait(10)
        result = asyncio.run(
            llm_config.bootstrap_async(session_factory=factory, executor=executor)
        )
    asyncio.run(client.aclose())
    engine.dispose()
    result_queue.put(
        {
            "exit_code": result.exit_code,
            "status": result.status,
            "calls": calls,
            "rendered": result.rendered,
        }
    )


def _run_race(target_url: str, *, api_key: str, response_status: int):
    context = multiprocessing.get_context("spawn")
    start_event = context.Event()
    result_queue = context.Queue()
    processes = [
        context.Process(
            target=_bootstrap_process,
            args=(target_url, api_key, start_event, result_queue, response_status),
        )
        for _ in range(2)
    ]
    for process in processes:
        process.start()
    start_event.set()
    for process in processes:
        process.join(timeout=30)
        assert process.exitcode == 0
    return [result_queue.get(timeout=5) for _ in processes]


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要显式 TEST_DATABASE_URL")
def test_two_bootstrap_processes_share_advisory_lock_and_probe_once():
    with _disposable_database() as (target_url, _database_name):
        # Spawn avoids inheriting pytest/psycopg2's parent process state while
        # still giving each worker a truly independent Python process and
        # SQLAlchemy connection pool.
        results = _run_race(
            target_url,
            api_key="sk-race-secret",
            response_status=401,
        )

        target_engine = create_engine(target_url)
        try:
            with target_engine.connect() as connection:
                settings_count = connection.execute(
                    text("SELECT count(*) FROM llm_runtime_settings")
                ).scalar_one()
                config_count = connection.execute(
                    text("SELECT count(*) FROM llm_model_configs")
                ).scalar_one()
                run_count = connection.execute(
                    text("SELECT count(*) FROM llm_model_test_runs")
                ).scalar_one()
                default_count = connection.execute(
                    text(
                        "SELECT count(*) FROM llm_runtime_settings "
                        "WHERE default_model_config_id IS NOT NULL"
                    )
                ).scalar_one()
            assert settings_count == 1
            assert config_count == 1
            assert run_count == 1
            assert default_count == 0
            assert sum(item["calls"] for item in results) == 1
            assert sorted(item["status"] for item in results) == [
                "bootstrap_failed",
                "bootstrap_noop",
            ]
            assert all("sk-race-secret" not in item["rendered"] for item in results)
        finally:
            target_engine.dispose()


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要显式 TEST_DATABASE_URL")
def test_successful_probe_race_creates_one_default_and_one_audit_run():
    with _disposable_database() as (target_url, _database_name):
        results = _run_race(
            target_url,
            api_key="sk-success-race-secret",
            response_status=200,
        )
        target_engine = create_engine(target_url)
        try:
            with target_engine.connect() as connection:
                assert connection.execute(text("SELECT count(*) FROM llm_runtime_settings")).scalar_one() == 1
                assert connection.execute(text("SELECT count(*) FROM llm_model_configs")).scalar_one() == 1
                assert connection.execute(text("SELECT count(*) FROM llm_model_test_runs")).scalar_one() == 1
                assert connection.execute(
                    text("SELECT count(*) FROM llm_runtime_settings WHERE default_model_config_id IS NOT NULL")
                ).scalar_one() == 1
            assert sum(item["calls"] for item in results) == 1
            assert sorted(item["status"] for item in results) == [
                "bootstrap_noop",
                "bootstrap_ready",
            ]
            assert all("sk-success-race-secret" not in item["rendered"] for item in results)
        finally:
            target_engine.dispose()


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要显式 TEST_DATABASE_URL")
def test_empty_database_without_legacy_key_race_creates_settings_only():
    with _disposable_database() as (target_url, _database_name):
        results = _run_race(target_url, api_key="", response_status=401)
        target_engine = create_engine(target_url)
        try:
            with target_engine.connect() as connection:
                assert connection.execute(text("SELECT count(*) FROM llm_runtime_settings")).scalar_one() == 1
                assert connection.execute(text("SELECT count(*) FROM llm_model_configs")).scalar_one() == 0
                assert connection.execute(text("SELECT count(*) FROM llm_model_test_runs")).scalar_one() == 0
            assert all(item["calls"] == 0 for item in results)
            assert all(item["status"] == "bootstrap_noop" for item in results)
        finally:
            target_engine.dispose()
