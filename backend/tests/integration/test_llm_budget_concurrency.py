"""PostgreSQL-only budget/attempt concurrency evidence on disposable DBs."""

import asyncio
import importlib
import os
import pkgutil
import re
import uuid
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from datetime import date

import httpx
import pytest
from sqlalchemy import create_engine, delete, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from app.models.base import Base
from app.models.llm_config import LlmRuntimeSetting
from app.models.llm_execution import LlmCallAttempt, LlmDailyBudget, LlmTokenReservation
from app.models.task_record import TaskRecord
from app.services.llm.budget import TokenBudgetService
from app.services.llm.call_executor import LlmCallExecutor
from app.services.llm.provider_client import ProviderClient
from app.services.llm.types import LlmRuntimeConfig, Provider


_DB_NAME_PATTERN = re.compile(r"^aistock_llm_budget_test_[0-9a-f]{12}$")


def _import_all_models():
    import app.models

    for module in pkgutil.iter_modules(app.models.__path__):
        importlib.import_module(f"app.models.{module.name}")


@contextmanager
def _temporary_database(database_url: str):
    """Create/drop one random PostgreSQL DB; never mutate the supplied DB."""

    base_url = make_url(database_url)
    admin_url = base_url.set(database="postgres")
    database_name = f"aistock_llm_budget_test_{uuid.uuid4().hex[:12]}"
    assert _DB_NAME_PATTERN.fullmatch(database_name)
    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    test_engine = None
    created = False
    try:
        with admin_engine.connect() as admin:
            admin.execute(text(f'CREATE DATABASE "{database_name}"'))
        created = True
        test_engine = create_engine(base_url.set(database=database_name), pool_size=10, max_overflow=50)
        _import_all_models()
        Base.metadata.create_all(test_engine)
        yield test_engine
    finally:
        if test_engine is not None:
            test_engine.dispose()
        if created:
            with admin_engine.connect() as admin:
                admin.execute(
                    text(
                        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                        "WHERE datname = :db_name AND pid <> pg_backend_pid()"
                    ),
                    {"db_name": database_name},
                )
                admin.execute(text(f'DROP DATABASE IF EXISTS "{database_name}"'))
        admin_engine.dispose()


def _runtime_config():
    return LlmRuntimeConfig(
        config_id=None,
        provider=Provider.DEEPSEEK,
        display_name="concurrency-test",
        model_name="model-x",
        base_url="https://api.deepseek.com",
        api_key="sk-test",
        credential_version="v1",
        max_output_tokens=100,
        input_price_micro_yuan_per_million=1,
        output_price_micro_yuan_per_million=2,
        runtime_fingerprint="fp-concurrency",
    )


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要显式 TEST_DATABASE_URL")
def test_postgres_budget_concurrency_caps_fifty_reservations():
    database_url = os.environ["TEST_DATABASE_URL"]
    if not database_url.startswith("postgresql"):
        pytest.fail("TEST_DATABASE_URL 必须指向 PostgreSQL")
    with _temporary_database(database_url) as engine:
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        test_date = date(2099, 1, 1)
        with Session(engine) as db:
            db.add(LlmRuntimeSetting(daily_token_limit=1_000))
            db.commit()

        def worker(index: int):
            db = factory()
            try:
                service = TokenBudgetService(db, daily_token_limit=1_000)
                reservation = service.reserve(30, step_key=f"concurrency-{index}", budget_date=test_date)
                return reservation.id
            except Exception as exc:  # expected only after the 33-token cap
                return exc
            finally:
                db.close()

        with ThreadPoolExecutor(max_workers=50) as pool:
            results = list(pool.map(worker, range(50)))
        reservations = [item for item in results if isinstance(item, str)]
        failures = [item for item in results if not isinstance(item, str)]
        assert len(reservations) == 33
        assert all(getattr(exc, "code", None) == "llm_daily_limit_reached" for exc in failures)

        with Session(engine) as db:
            rows = db.execute(
                select(LlmTokenReservation).where(LlmTokenReservation.budget_date == test_date)
            ).scalars().all()
            assert len(rows) == 33
            for reservation in rows:
                TokenBudgetService(db).release(reservation.id)
            ledger = db.get(LlmDailyBudget, test_date)
            assert ledger.reserved_tokens == 0
            db.execute(delete(LlmTokenReservation).where(LlmTokenReservation.budget_date == test_date))
            db.execute(delete(LlmDailyBudget).where(LlmDailyBudget.budget_date == test_date))
            db.commit()


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要显式 TEST_DATABASE_URL")
def test_postgres_task_step_attempt_ordinals_are_unique_and_linked():
    database_url = os.environ["TEST_DATABASE_URL"]
    if not database_url.startswith("postgresql"):
        pytest.fail("TEST_DATABASE_URL 必须指向 PostgreSQL")
    with _temporary_database(database_url) as engine:
        factory = sessionmaker(bind=engine, expire_on_commit=False)
        with Session(engine) as db:
            db.add(LlmRuntimeSetting(daily_token_limit=1_000_000))
            task = TaskRecord(task_type="concurrency_test", status="running", user_id=None)
            db.add(task)
            db.commit()
            task_id = task.id

        async def one_call(index: int):
            async def handler(request):
                return httpx.Response(
                    200,
                    json={
                        "model": "model-x",
                        "choices": [{"message": {"content": '{"ok":true}'}}],
                        "usage": {"prompt_tokens": 5, "completion_tokens": 2},
                    },
                )

            client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
            try:
                executor = LlmCallExecutor(
                    factory,
                    provider_client=ProviderClient(
                        client=client,
                        resolver=lambda host, port: ["8.8.8.8"],
                    ),
                    budget=TokenBudgetService(factory, daily_token_limit=1_000_000),
                )
                result = await executor.call(
                    runtime_config=_runtime_config(),
                    operation_type="task",
                    task_id=task_id,
                    step_key="same-step",
                    messages=[{"role": "user", "content": f"hello-{index}"}],
                )
                return result
            finally:
                await client.aclose()

        def worker(index: int):
            return asyncio.run(one_call(index))

        with ThreadPoolExecutor(max_workers=20) as pool:
            list(pool.map(worker, range(20)))

        with Session(engine) as db:
            attempts = db.execute(
                select(LlmCallAttempt).where(
                    LlmCallAttempt.task_id == task_id,
                    LlmCallAttempt.step_key == "same-step",
                )
            ).scalars().all()
            reservations = db.execute(
                select(LlmTokenReservation).where(
                    LlmTokenReservation.task_id == task_id,
                    LlmTokenReservation.step_key == "same-step",
                )
            ).scalars().all()
            assert len(attempts) == len(reservations) == 20
            assert sorted(item.attempt_no for item in attempts) == list(range(1, 21))
            assert len({item.attempt_no for item in attempts}) == 20
            assert {item.reservation_id for item in attempts} == {item.id for item in reservations}
            assert all(item.status == "settled" for item in reservations)
