import asyncio
from datetime import datetime, timezone

import pytest

from app.datahub.contracts import (
    Capability,
    DataQuality,
    DataResult,
    FinancialSummary,
    Freshness,
    FundFlow,
    MarketIndex,
    StockSnapshot,
)
from app.datahub.errors import DataHubError, DataHubErrorCode
from app.datahub.runtime import InMemoryDataCache, RedisDataCache
from app.datahub.router import DataHubRouter, RouteDefinition
from app.datahub.validators import validate_cross_source, validate_payload


def _result(provider: str, price: float = 3000) -> DataResult:
    return DataResult(
        data=[MarketIndex(code="000001.SS", name="上证指数", price=price, change_pct=0.2)],
        capability=Capability.MARKET_INDICES,
        provider=provider,
        data_at=datetime.now(timezone.utc),
        quality=DataQuality(valid=True, rows=1),
    )


class FakeProvider:
    def __init__(self, name: str, values, delay: float = 0):
        self.name = name
        self.values = list(values)
        self.calls = 0
        self.delay = delay

    async def fetch(self, capability, params):
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        value = self.values[min(self.calls - 1, len(self.values) - 1)]
        if isinstance(value, Exception):
            raise value
        return value


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("capability", "payload"),
    [
        (
            Capability.STOCK_SNAPSHOT,
            StockSnapshot(code="600519.SS", name="贵州茅台", price=1500, data_at=datetime.now(timezone.utc)),
        ),
        (
            Capability.STOCK_FINANCIALS,
            FinancialSummary(code="600519.SS", revenue=1, data_at=datetime.now(timezone.utc)),
        ),
        (
            Capability.STOCK_FUND_FLOW,
            FundFlow(code="600519.SS", net_main_flow=1, data_at=datetime.now(timezone.utc)),
        ),
    ],
)
async def test_router_accepts_singleton_pydantic_data_result(capability, payload):
    provider = FakeProvider(
        "fixture",
        [DataResult(data=payload, capability=capability, provider="fixture", data_at=payload.data_at)],
    )
    router = DataHubRouter(
        {"fixture": provider},
        {capability: RouteDefinition(mode="fixed", providers=["fixture"])},
    )

    result = await router.fetch(capability, {"code": "600519.SS"})

    assert result.data == payload


@pytest.mark.asyncio
async def test_auto_route_falls_back_and_records_attempts():
    primary = FakeProvider("tencent", [DataHubError(DataHubErrorCode.TIMEOUT, "请求超时")])
    backup = FakeProvider("akshare", [_result("akshare")])
    router = DataHubRouter(
        {"tencent": primary, "akshare": backup},
        {Capability.MARKET_INDICES: RouteDefinition(mode="auto", providers=["tencent", "akshare"])},
    )

    result = await router.fetch(Capability.MARKET_INDICES, {})

    assert result.provider == "akshare"
    assert result.fallback_used is True
    assert result.attempts == ["tencent", "akshare"]


@pytest.mark.asyncio
async def test_fixed_route_does_not_silently_switch_provider():
    primary = FakeProvider("tencent", [DataHubError(DataHubErrorCode.TIMEOUT, "请求超时")])
    backup = FakeProvider("akshare", [_result("akshare")])
    router = DataHubRouter(
        {"tencent": primary, "akshare": backup},
        {Capability.MARKET_INDICES: RouteDefinition(mode="fixed", providers=["tencent", "akshare"])},
    )

    with pytest.raises(DataHubError) as exc_info:
        await router.fetch(Capability.MARKET_INDICES, {})
    assert exc_info.value.code is DataHubErrorCode.TIMEOUT
    assert backup.calls == 0


@pytest.mark.asyncio
async def test_router_returns_last_good_as_stale_before_failing_503():
    provider = FakeProvider(
        "tencent",
        [_result("tencent"), DataHubError(DataHubErrorCode.TIMEOUT, "请求超时")],
    )
    cache = InMemoryDataCache()
    router = DataHubRouter(
        {"tencent": provider},
        {Capability.MARKET_INDICES: RouteDefinition(mode="fixed", providers=["tencent"], stale_ttl_seconds=60)},
        cache=cache,
    )
    fresh = await router.fetch(Capability.MARKET_INDICES, {"codes": ["000001.SS"]})
    stale = await router.fetch(Capability.MARKET_INDICES, {"codes": ["000001.SS"], "force_refresh": True})

    assert fresh.freshness is Freshness.FRESH
    assert stale.freshness is Freshness.STALE
    assert stale.provider == "tencent"


@pytest.mark.asyncio
async def test_identical_cache_misses_are_coalesced_to_one_upstream_call():
    provider = FakeProvider("tencent", [_result("tencent")], delay=0.01)
    router = DataHubRouter(
        {"tencent": provider},
        {Capability.MARKET_INDICES: RouteDefinition(mode="fixed", providers=["tencent"])},
        cache=InMemoryDataCache(),
    )
    results = await asyncio.gather(
        router.fetch(Capability.MARKET_INDICES, {"codes": ["000001.SS"]}),
        router.fetch(Capability.MARKET_INDICES, {"codes": ["000001.SS"]}),
    )
    assert provider.calls == 1
    assert results[0].data == results[1].data


def test_quality_validator_rejects_empty_all_zero_and_nan_payloads():
    with pytest.raises(DataHubError) as empty:
        validate_payload(Capability.MARKET_INDICES, [])
    assert empty.value.code is DataHubErrorCode.EMPTY_INVALID

    with pytest.raises(DataHubError) as zero:
        validate_payload(Capability.MARKET_INDICES, [{"code": "000001", "name": "上证指数", "price": 0, "change_pct": 0}])
    assert zero.value.code is DataHubErrorCode.EMPTY_INVALID

    with pytest.raises(DataHubError) as nan:
        validate_payload(Capability.MARKET_INDICES, [{"code": "000001", "name": "上证指数", "price": float("nan"), "change_pct": 0}])
    assert nan.value.code is DataHubErrorCode.EMPTY_INVALID


@pytest.mark.parametrize(
    ("capability", "payload"),
    [
        (
            Capability.STOCK_SNAPSHOT,
            StockSnapshot(code="600519.SS", name="贵州茅台", price=1500, data_at=datetime.now(timezone.utc)),
        ),
        (
            Capability.STOCK_FINANCIALS,
            FinancialSummary(code="600519.SS", revenue=1, data_at=datetime.now(timezone.utc)),
        ),
        (
            Capability.STOCK_FUND_FLOW,
            FundFlow(code="600519.SS", net_main_flow=1, data_at=datetime.now(timezone.utc)),
        ),
    ],
)
def test_quality_validator_accepts_singleton_pydantic_models(capability, payload):
    """A provider may return one typed model instead of a list of rows."""
    from app.datahub.validators import validate_payload

    assert validate_payload(capability, payload) == 1


def test_cross_source_validator_accepts_singleton_pydantic_models():
    """Cross-source checks must normalize typed singleton rows before reading fields."""
    first = StockSnapshot(
        code="600519.SS",
        name="贵州茅台",
        price=1500,
        data_at=datetime.now(timezone.utc),
    )
    second = StockSnapshot(
        code="600519.SS",
        name="贵州茅台",
        price=1501,
        data_at=datetime.now(timezone.utc),
    )

    validate_cross_source(Capability.STOCK_SNAPSHOT, [first, second])


@pytest.mark.asyncio
async def test_redis_cache_serializes_data_result_for_another_process():
    class SharedRedis:
        def __init__(self):
            self.values = {}

        async def set(self, key, value, ex=None):
            self.values[key] = value

        async def get(self, key):
            return self.values.get(key)

    shared = SharedRedis()
    first = RedisDataCache(shared)
    second = RedisDataCache(shared)
    await first.set("indices", _result("tencent"), 60)
    cached = await second.get("indices")
    assert isinstance(cached, DataResult)
    assert cached.provider == "tencent"
    assert isinstance(cached.data[0], MarketIndex)
    stale = await second.get_last_good("indices")
    assert isinstance(stale, DataResult)
    assert stale.provider == "tencent"
    assert isinstance(stale.data[0], MarketIndex)
