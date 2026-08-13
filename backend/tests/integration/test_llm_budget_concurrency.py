"""PostgreSQL-only budget concurrency evidence on the disposable test DB."""

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date

import pytest
from sqlalchemy import create_engine, delete, select
from sqlalchemy.orm import Session, sessionmaker

from app.models.base import Base
from app.models.llm_config import LlmRuntimeSetting
from app.models.llm_execution import LlmDailyBudget, LlmTokenReservation
from app.services.llm.budget import TokenBudgetService


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="需要显式 TEST_DATABASE_URL")
def test_postgres_budget_concurrency_caps_fifty_reservations():
    database_url = os.environ["TEST_DATABASE_URL"]
    if not database_url.startswith("postgresql"):
        pytest.fail("TEST_DATABASE_URL 必须指向 PostgreSQL")
    engine = create_engine(database_url, pool_size=10, max_overflow=50)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    test_date = date(2099, 1, 1)
    # Isolate rows by a date no application task can use.  The singleton
    # runtime row is restored at the end so this disposable DB remains usable.
    with Session(engine) as db:
        db.execute(delete(LlmTokenReservation).where(LlmTokenReservation.budget_date == test_date))
        db.execute(delete(LlmDailyBudget).where(LlmDailyBudget.budget_date == test_date))
        setting = db.get(LlmRuntimeSetting, 1)
        original_setting = None
        if setting is not None:
            original_setting = (setting.daily_token_limit, setting.budget_locked)
        if setting is None:
            setting = LlmRuntimeSetting(daily_token_limit=1_000)
            db.add(setting)
        else:
            setting.daily_token_limit = 1_000
            setting.budget_locked = False
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
    assert all(getattr(exc, "code", None) == "llm_budget_exhausted" for exc in failures)

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
        if original_setting is not None:
            setting = db.get(LlmRuntimeSetting, 1)
            setting.daily_token_limit, setting.budget_locked = original_setting
        else:
            db.delete(db.get(LlmRuntimeSetting, 1))
        db.commit()
    engine.dispose()
