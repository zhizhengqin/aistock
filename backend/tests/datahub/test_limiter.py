import asyncio

import pytest

from app.datahub.limiter import CircuitBreaker, ProviderLimiter


@pytest.mark.asyncio
async def test_provider_limiter_enforces_provider_and_global_concurrency():
    limiter = ProviderLimiter(provider_limit=2, global_limit=3)
    active = 0
    maximum = 0

    async def operation():
        nonlocal active, maximum
        async with limiter.slot("eastmoney"):
            active += 1
            maximum = max(maximum, active)
            await asyncio.sleep(0.01)
            active -= 1

    await asyncio.gather(*(operation() for _ in range(8)))
    assert maximum <= 2


@pytest.mark.asyncio
async def test_limiter_can_run_sync_sdk_in_controlled_thread():
    limiter = ProviderLimiter(provider_limit=1, global_limit=2)
    result = await limiter.run_sync("tdx", lambda: "ok", timeout=1)
    assert result == "ok"


def test_circuit_breaker_opens_after_failures_and_recovers_after_cooldown():
    breaker = CircuitBreaker(failure_threshold=2, recovery_seconds=0.01)
    assert breaker.allow() is True
    breaker.record_failure()
    assert breaker.allow() is True
    breaker.record_failure()
    assert breaker.allow() is False
    import time

    time.sleep(0.02)
    assert breaker.allow() is True
    breaker.record_success()
    assert breaker.allow() is True
