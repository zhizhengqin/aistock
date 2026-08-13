import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.models.base import Base
from app.models.llm_execution import LlmCallAttempt, LlmTokenReservation
from app.services.llm.budget import TokenBudgetService
from app.services.llm.call_executor import LlmCallExecutor
from app.services.llm.provider_client import ProviderClient
from app.services.llm.types import LlmRuntimeConfig, Provider


def runtime():
    return LlmRuntimeConfig(
        config_id=None,
        provider=Provider.DEEPSEEK,
        display_name="test",
        model_name="model-x",
        base_url="https://api.deepseek.com",
        api_key="sk-secret",
        credential_version="v1",
        max_output_tokens=100,
        input_price_micro_yuan_per_million=1,
        output_price_micro_yuan_per_million=2,
        runtime_fingerprint="fp-test",
    )


@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        yield db
    Base.metadata.drop_all(engine)


@pytest.mark.asyncio
async def test_executor_audits_success_and_requires_operation_type(session):
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
    executor = LlmCallExecutor(
        session,
        provider_client=ProviderClient(client=client),
        budget=TokenBudgetService(session, daily_token_limit=1000),
    )
    result = await executor.call(
        runtime_config=runtime(),
        operation_type="admin_probe",
        step_key="probe",
        messages=[{"role": "user", "content": "hello"}],
    )
    await client.aclose()
    assert result.result_json == {"ok": True}
    attempt = session.execute(select(LlmCallAttempt)).scalar_one()
    assert attempt.task_id is None
    assert attempt.model_config_id is None
    assert attempt.operation_type == "admin_probe"
    assert attempt.provider_snapshot == "deepseek"
    assert session.execute(select(LlmTokenReservation)).scalar_one().status == "settled"


@pytest.mark.asyncio
async def test_executor_retries_with_distinct_reservation_and_attempt(session):
    calls = 0

    async def handler(request):
        nonlocal calls
        calls += 1
        if calls < 2:
            return httpx.Response(503, json={"error": {"message": "temporary"}})
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": '{"ok":true}'}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 2},
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    executor = LlmCallExecutor(
        session,
        provider_client=ProviderClient(client=client),
        budget=TokenBudgetService(session, daily_token_limit=1000),
    )
    await executor.call(
        runtime_config=runtime(),
        operation_type="task",
        task_id=None,
        step_key="retry",
        messages=[{"role": "user", "content": "hello"}],
    )
    await client.aclose()
    assert calls == 2
    assert len(session.execute(select(LlmCallAttempt)).scalars().all()) == 2
    assert len(session.execute(select(LlmTokenReservation)).scalars().all()) == 2


@pytest.mark.asyncio
async def test_unknown_timeout_settles_upper_bound_and_never_replays(session):
    calls = 0

    async def handler(request):
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("response timed out")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    executor = LlmCallExecutor(
        session,
        provider_client=ProviderClient(client=client),
        budget=TokenBudgetService(session, daily_token_limit=1000),
    )
    with pytest.raises(Exception) as exc:
        await executor.call(
            runtime_config=runtime(),
            operation_type="task",
            step_key="unknown",
            messages=[{"role": "user", "content": "hello"}],
        )
    await client.aclose()
    assert calls == 1
    assert exc.value.code == "llm_failed_unknown"
    attempt = session.execute(select(LlmCallAttempt)).scalar_one()
    reservation = session.execute(select(LlmTokenReservation)).scalar_one()
    assert attempt.status == "failed_unknown"
    assert reservation.status == "settled"
    assert reservation.settled_tokens == reservation.reserved_tokens


@pytest.mark.asyncio
async def test_retry_budget_is_per_call_even_when_history_has_high_attempt_numbers(session):
    session.add(
        LlmCallAttempt(
            task_id=42,
            model_config_id=None,
            operation_type="task",
            step_key="history",
            attempt_no=9,
            provider_snapshot="deepseek",
            model_snapshot="model-x",
            runtime_fingerprint="old",
            status="failed",
        )
    )
    session.commit()
    calls = 0

    async def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(503, text="temporary")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    executor = LlmCallExecutor(
        session,
        provider_client=ProviderClient(client=client),
        budget=TokenBudgetService(session, daily_token_limit=100_000),
        max_retries=2,
    )
    with pytest.raises(Exception) as exc:
        await executor.call(
            runtime_config=runtime(),
            operation_type="task",
            task_id=42,
            step_key="history",
            messages=[{"role": "user", "content": "hello"}],
        )
    await client.aclose()
    assert exc.value.code == "llm_unavailable"
    assert calls == 3


@pytest.mark.asyncio
async def test_retry_attempts_have_no_orphan_reservations_after_all_failures(session):
    async def handler(request):
        return httpx.Response(503, text="temporary")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    executor = LlmCallExecutor(
        session,
        provider_client=ProviderClient(client=client),
        budget=TokenBudgetService(session, daily_token_limit=100_000),
    )
    with pytest.raises(Exception):
        await executor.call(
            runtime_config=runtime(),
            operation_type="task",
            task_id=43,
            step_key="orphan-check",
            messages=[{"role": "user", "content": "hello"}],
        )
    await client.aclose()
    attempts = session.execute(select(LlmCallAttempt)).scalars().all()
    reservations = session.execute(select(LlmTokenReservation)).scalars().all()
    assert len(attempts) == len(reservations) == 3
    assert {attempt.reservation_id for attempt in attempts} == {row.id for row in reservations}


@pytest.mark.asyncio
async def test_confirmed_unsent_connect_failure_releases_before_retry(session):
    calls = 0

    async def handler(request):
        nonlocal calls
        calls += 1
        raise httpx.ConnectError("connection refused")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    executor = LlmCallExecutor(
        session,
        provider_client=ProviderClient(client=client),
        budget=TokenBudgetService(session, daily_token_limit=1000),
        max_retries=1,
    )
    with pytest.raises(Exception) as exc:
        await executor.call(
            runtime_config=runtime(),
            operation_type="task",
            step_key="unsent",
            messages=[{"role": "user", "content": "hello"}],
        )
    await client.aclose()
    assert calls == 2
    assert exc.value.code == "llm_unavailable"
    reservations = session.execute(select(LlmTokenReservation)).scalars().all()
    assert all(row.status == "released" for row in reservations)
