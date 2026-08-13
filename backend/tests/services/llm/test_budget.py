from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.models.base import Base
from app.models.llm_config import LlmRuntimeSetting
from app.models.llm_execution import LlmDailyBudget, LlmTokenReservation
from app.services.llm.budget import TokenBudgetService


@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        yield db
    Base.metadata.drop_all(engine)


def test_reserve_settle_and_release_are_atomic(session):
    service = TokenBudgetService(session, daily_token_limit=100)
    first = service.reserve(60, step_key="step-1")
    assert first.reserved_tokens == 60
    service.release(first.id)
    second = service.reserve(60, step_key="step-2")
    service.settle(second.id, 17)
    ledger = session.execute(select(LlmDailyBudget)).scalar_one()
    assert ledger.reserved_tokens == 0
    assert ledger.settled_tokens == 17


def test_actual_over_reserve_locks_global_budget(session):
    service = TokenBudgetService(session, daily_token_limit=100)
    reservation = service.reserve(10, step_key="step")
    service.settle(reservation.id, 11)
    setting = session.get(LlmRuntimeSetting, 1)
    assert setting.budget_locked is True
    with pytest.raises(Exception) as exc:
        service.reserve(1, step_key="next")
    assert exc.value.code == "llm_budget_locked"


def test_expired_lease_is_reaped(session):
    now = datetime(2026, 8, 13, tzinfo=timezone.utc)
    service = TokenBudgetService(session, daily_token_limit=100, clock=lambda: now)
    reservation = service.reserve(20, step_key="step", lease_expires_at=now - timedelta(seconds=1))
    assert service.reap_expired(now=now) == 1
    row = session.get(LlmTokenReservation, reservation.id)
    assert row.status == "expired"


def test_beijing_midnight_creates_separate_ledgers(session):
    before = datetime(2026, 8, 13, 15, 59, 59, tzinfo=timezone.utc)
    after = datetime(2026, 8, 13, 16, 0, 0, tzinfo=timezone.utc)
    service = TokenBudgetService(session, daily_token_limit=100, clock=lambda: before)
    service.reserve(80, step_key="before")
    service.clock = lambda: after
    service.reserve(80, step_key="after")
    assert len(session.execute(select(LlmDailyBudget)).scalars().all()) == 2
