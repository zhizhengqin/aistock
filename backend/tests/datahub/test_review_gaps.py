from datetime import datetime, timezone
import inspect

import pytest

from app.datahub.contracts import Capability, DataResult, DataQuality
from app.datahub.errors import DataHubError, DataHubErrorCode
from app.datahub.providers.akshare import AkshareProvider
from app.datahub.providers.base import ProviderAdapter
from app.datahub.providers.eastmoney import EastmoneyProvider
from app.datahub.providers.official import OfficialProvider
from app.datahub.providers.sina import SinaProvider
from app.datahub.providers.tdx import TdxProvider
from app.datahub.providers.tencent import TencentProvider
from app.datahub.providers.tushare import TushareProvider
from app.datahub.router import DataHubRouter, RouteDefinition
from app.datahub.runtime import InMemoryDataCache
from app.datahub.limiter import RedisRateLimiter, RedisCircuitBreaker


def test_consumers_are_async_datahub_only_without_legacy_unwrap_boundary():
    import app.datahub.consumer as consumer

    source = inspect.getsource(consumer)
    assert "app.datasource.akshare_client" not in source
    assert "unwrap_data" not in source
    assert inspect.iscoroutinefunction(consumer.get_stock_info)
    assert inspect.iscoroutinefunction(consumer.get_stock_kline)


def test_independent_adapters_do_not_inherit_or_import_akshare():
    provider_types = (TencentProvider, EastmoneyProvider, TdxProvider, SinaProvider, OfficialProvider)
    for provider_type in provider_types:
        assert not issubclass(provider_type, AkshareProvider)
        assert "AkshareProvider" not in inspect.getsource(provider_type)
        assert issubclass(provider_type, ProviderAdapter)


@pytest.mark.asyncio
async def test_tencent_fixture_parser_decodes_gbk_quote_and_timestamp():
    payload = "v_sh000001=\"1~上证指数~000001~3001.23~2990.12~2995.00~123~45~67~0.37~2026/08/22 15:00:00\";"
    class Response:
        content = payload.encode("gbk")
        status_code = 200

        def raise_for_status(self):
            return None

    class Client:
        def get(self, url, timeout):
            return Response()

    result = await TencentProvider(http_client=Client()).fetch(
        Capability.MARKET_INDICES,
        {"codes": ["000001.SS"]},
    )
    assert result.data[0].code == "000001.SS"
    assert result.data[0].price == 3001.23
    assert result.data[0].data_at is not None
    assert result.data_at is not None


@pytest.mark.asyncio
async def test_tencent_fixture_parser_accepts_compact_quote_timestamp():
    payload = "v_sh000001=\"1~上证指数~000001~3001.23~2990.12~2995.00~123~45~67~0.37~20260821161402\";"

    class Response:
        content = payload.encode("gbk")

        def raise_for_status(self):
            return None

    class Client:
        def get(self, url, timeout):
            return Response()

    result = await TencentProvider(http_client=Client()).fetch(
        Capability.MARKET_INDICES,
        {"codes": ["000001.SS"]},
    )

    assert result.data_at == datetime(2026, 8, 21, 8, 14, 2, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_tencent_code_whitelist_preserves_index_etf_and_beijing_symbols_and_timezone():
    payload = (
        'v_sh000300="1~沪深300~000300~3500.00~3490~3495~0~0~0~0~2026/08/22 15:00:00";'
        'v_sz159915="1~创业板ETF~159915~2.10~2.00~2.05~0~0~0~0~2026/08/22 15:00:00";'
        'v_bj430047="1~诺思兰德~430047~10.20~10~10~0~0~0~0~2026/08/22 15:00:00";'
    )

    class Response:
        content = payload.encode("gbk")

        def raise_for_status(self):
            return None

    class Client:
        def __init__(self):
            self.url = ""

        def get(self, url, timeout):
            self.url = url
            return Response()

    client = Client()
    result = await TencentProvider(http_client=client).fetch(
        Capability.MARKET_INDICES,
        {"codes": ["000300", "159915.SZ", "430047.BJ"]},
    )
    codes = {row.code for row in result.data}
    assert codes == {"000300.SS", "159915.SZ", "430047.BJ"}
    assert "sh000300" in client.url and "sz159915" in client.url and "bj430047" in client.url
    assert result.data_at == datetime(2026, 8, 22, 7, 0, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_sina_fixture_preserves_shanghai_code_and_china_timestamp():
    fields = ["浦发银行", "10", "10", "10.2", "10.3", "9.9"] + ["0"] * 25 + ["2026-08-22", "15:00:00", "0"]
    payload = f'hq_str_sh600000="{",".join(fields)}";'

    class Response:
        content = payload.encode("gbk")

        def raise_for_status(self):
            return None

    class Client:
        def get(self, url, timeout):
            return Response()

    result = await SinaProvider(http_client=Client()).fetch(Capability.STOCK_SNAPSHOT, {"code": "600000.SH"})
    assert result.data.code == "600000.SS"
    assert result.data_at == datetime(2026, 8, 22, 7, 0, tzinfo=timezone.utc)


def test_ticker_normalisation_routes_bj_92_before_shanghai_digit_rules():
    from app.datahub.providers.ticker import normalise_ticker, vendor_symbol

    assert normalise_ticker("920001") == "920001.BJ"
    assert normalise_ticker("000300") == "000300.SS"
    assert normalise_ticker("600000") == "600000.SS"
    assert normalise_ticker("159915") == "159915.SZ"
    assert normalise_ticker("sh000300") == "000300.SS"
    assert vendor_symbol("920001") == "bj920001"


def test_ticker_normalisation_rejects_ambiguous_or_malformed_codes():
    from app.datahub.providers.ticker import normalise_ticker

    for value in ("", "foo", "foo600519bar", "6005190", "SH000001.SZ", "000001.BAD"):
        with pytest.raises(DataHubError) as exc:
            normalise_ticker(value)
        assert exc.value.code is DataHubErrorCode.VALIDATION


@pytest.mark.asyncio
async def test_router_rejects_fresh_result_without_trusted_data_time():
    class Provider:
        async def fetch(self, capability, params):
            return DataResult(
                data=[{"code": "000001.SS", "name": "上证指数", "price": 3000, "change_pct": 0}],
                capability=capability,
                provider="fixture",
                data_at=None,
                quality=DataQuality(valid=True, rows=1),
            )

    router = DataHubRouter(
        {"fixture": Provider()},
        {Capability.MARKET_INDICES: RouteDefinition(mode="fixed", providers=["fixture"])},
        cache=InMemoryDataCache(),
    )
    with pytest.raises(DataHubError) as exc:
        await router.fetch(Capability.MARKET_INDICES, {"codes": ["000001.SS"]})
    assert exc.value.code is DataHubErrorCode.STALE_INVALID


@pytest.mark.asyncio
async def test_router_accepts_tushare_trade_date_as_data_time_for_kpl():
    class FakePro:
        def kpl_list(self, **kwargs):
            return [{"ts_code": "000001.SZ", "name": "示例", "trade_date": "20260821", "tag": "涨停"}]

    provider = TushareProvider(token="runtime", pro_client=FakePro())
    router = DataHubRouter(
        {"tushare": provider},
        {Capability.KPL_LIMIT_LIST: RouteDefinition(mode="fixed", providers=["tushare"], ttl_seconds=86400)},
        cache=InMemoryDataCache(),
        clock=lambda: datetime(2026, 8, 21, 8, tzinfo=timezone.utc),
    )
    result = await router.fetch(Capability.KPL_LIMIT_LIST, {"trade_date": "20260821"})
    assert result.data_at == datetime(2026, 8, 21, tzinfo=timezone.utc)
    assert result.freshness.value == "fresh"


@pytest.mark.asyncio
async def test_router_cache_key_changes_when_route_generation_changes():
    class Provider:
        def __init__(self, name):
            self.name = name
            self.calls = 0

        async def fetch(self, capability, params):
            self.calls += 1
            return DataResult(
                data=[{"code": "000001.SS", "name": self.name, "price": 3000 + self.calls, "change_pct": 0}],
                capability=capability,
                provider=self.name,
                data_at=datetime.now(timezone.utc),
                quality=DataQuality(valid=True, rows=1),
            )

    first = Provider("first")
    second = Provider("second")
    router = DataHubRouter(
        {"first": first, "second": second},
        {Capability.MARKET_INDICES: RouteDefinition(mode="fixed", providers=["first"], generation=1)},
        cache=InMemoryDataCache(),
    )
    await router.fetch(Capability.MARKET_INDICES, {})
    router.routes[Capability.MARKET_INDICES] = RouteDefinition(mode="fixed", providers=["second"], generation=2)
    result = await router.fetch(Capability.MARKET_INDICES, {})
    assert result.provider == "second"
    assert first.calls == 1
    assert second.calls == 1


@pytest.mark.asyncio
async def test_router_refreshes_route_from_authoritative_loader_before_business_fetch():
    class Provider:
        def __init__(self, name):
            self.name = name
            self.calls = 0

        async def fetch(self, capability, params):
            self.calls += 1
            return DataResult(
                data=[{"code": "000001.SS", "name": self.name, "price": 3000, "change_pct": 0, "data_at": datetime.now(timezone.utc)}],
                capability=capability,
                provider=self.name,
                data_at=datetime.now(timezone.utc),
                quality=DataQuality(valid=True, rows=1),
            )

    current = {"route": RouteDefinition(mode="fixed", providers=["first"], generation=1)}

    async def load_routes():
        return {Capability.MARKET_INDICES: current["route"]}

    first, second = Provider("first"), Provider("second")
    router = DataHubRouter({"first": first, "second": second}, {}, route_loader=load_routes, cache=InMemoryDataCache())
    assert (await router.fetch(Capability.MARKET_INDICES, {})).provider == "first"
    current["route"] = RouteDefinition(mode="fixed", providers=["second"], generation=2)
    assert (await router.fetch(Capability.MARKET_INDICES, {})).provider == "second"


@pytest.mark.asyncio
async def test_database_route_loader_failure_is_structured_instead_of_defaulting():
    from app.datahub import platform

    loader = platform._DatabaseRouteLoader(ttl_seconds=0)
    original = platform._load_database_routes_sync
    platform._load_database_routes_sync = lambda: (_ for _ in ()).throw(ConnectionError("database offline"))
    try:
        with pytest.raises(DataHubError) as exc:
            await loader()
        assert exc.value.code is DataHubErrorCode.INTERNAL
        assert "database offline" not in str(exc.value)
    finally:
        platform._load_database_routes_sync = original


@pytest.mark.asyncio
async def test_redis_rate_budget_is_shared_and_local_fallback_is_time_windowed():
    class SharedRedis:
        def __init__(self):
            self.counts = {}

        async def incr(self, key):
            self.counts[key] = self.counts.get(key, 0) + 1
            return self.counts[key]

        async def expire(self, key, seconds):
            return True

    redis = SharedRedis()
    first = RedisRateLimiter(redis, limit=1, window_seconds=60)
    second = RedisRateLimiter(redis, limit=1, window_seconds=60)
    assert await first.allow("eastmoney") is True
    assert await second.allow("eastmoney") is False
    class BrokenRedis:
        async def incr(self, key):
            raise ConnectionError("redis down")

        async def expire(self, key, seconds):
            raise ConnectionError("redis down")

    local = RedisRateLimiter(BrokenRedis(), limit=1, window_seconds=60)
    assert await local.allow("eastmoney") is True
    assert await local.allow("eastmoney") is False


@pytest.mark.asyncio
async def test_router_applies_shared_rate_budget_to_provider_calls():
    class SharedRedis:
        def __init__(self):
            self.values = {}

        async def incr(self, key):
            self.values[key] = int(self.values.get(key, 0)) + 1
            return self.values[key]

        async def expire(self, key, seconds):
            return True

        async def get(self, key):
            return self.values.get(key)

        async def set(self, key, value, ex=None):
            self.values[key] = value

        async def delete(self, *keys):
            for key in keys:
                self.values.pop(key, None)

    class Provider:
        async def fetch(self, capability, params):
            return DataResult(data=[{"code": "000001.SS", "name": "上证指数", "price": 3000, "change_pct": 0, "data_at": datetime.now(timezone.utc)}], capability=capability, provider="fixture", data_at=datetime.now(timezone.utc), quality=DataQuality(valid=True, rows=1))

    redis = SharedRedis()
    router = DataHubRouter(
        {"fixture": Provider()},
        {Capability.MARKET_INDICES: RouteDefinition(mode="fixed", providers=["fixture"])},
        cache=InMemoryDataCache(),
        rate_limiter=RedisRateLimiter(redis, limit=1, window_seconds=60),
    )
    await router.fetch(Capability.MARKET_INDICES, {"force_refresh": True})
    with pytest.raises(DataHubError) as exc:
        await router.fetch(Capability.MARKET_INDICES, {"force_refresh": True})
    assert exc.value.code is DataHubErrorCode.RATE_LIMITED


@pytest.mark.asyncio
async def test_shared_circuit_breaker_opens_for_another_router_and_recovers():
    class SharedRedis:
        def __init__(self):
            self.values = {}

        async def incr(self, key):
            self.values[key] = int(self.values.get(key, 0)) + 1
            return self.values[key]

        async def expire(self, key, seconds):
            return True

        async def get(self, key):
            return self.values.get(key)

        async def set(self, key, value, ex=None):
            self.values[key] = value

        async def delete(self, *keys):
            for key in keys:
                self.values.pop(key, None)

    class BrokenProvider:
        async def fetch(self, capability, params):
            raise DataHubError(DataHubErrorCode.TIMEOUT, "fixture timeout")

    redis = SharedRedis()
    breaker_a = RedisCircuitBreaker(redis, failure_threshold=1, recovery_seconds=1)
    breaker_b = RedisCircuitBreaker(redis, failure_threshold=1, recovery_seconds=1)
    assert await breaker_a.allow("fixture") is True
    await breaker_a.record_failure("fixture")
    assert await breaker_b.allow("fixture") is False
    await breaker_a.record_success("fixture")
    assert await breaker_b.allow("fixture") is True


@pytest.mark.asyncio
async def test_router_cleans_completed_request_locks():
    class Provider:
        async def fetch(self, capability, params):
            return DataResult(data=[{"code": "000001.SS", "name": "上证指数", "price": 3000, "change_pct": 0, "data_at": datetime.now(timezone.utc)}], capability=capability, provider="fixture", data_at=datetime.now(timezone.utc), quality=DataQuality(valid=True, rows=1))

    router = DataHubRouter({"fixture": Provider()}, {Capability.MARKET_INDICES: RouteDefinition(mode="fixed", providers=["fixture"], ttl_seconds=1)}, cache=InMemoryDataCache())
    for index in range(200):
        await router.fetch(Capability.MARKET_INDICES, {"code": str(index), "force_refresh": True})
    assert len(router._locks) <= 32


@pytest.mark.asyncio
async def test_router_applies_saved_credentials_and_fingerprint_to_next_business_fetch():
    class CredentialProvider:
        def __init__(self):
            self.token = ""
            self.calls = 0

        def update_credentials(self, credentials):
            self.token = credentials.get("token", "")

        async def fetch(self, capability, params):
            self.calls += 1
            return DataResult(
                data=[{"code": "000001.SS", "name": self.token, "price": 3000, "change_pct": 0, "data_at": datetime.now(timezone.utc)}],
                capability=capability,
                provider="tushare",
                data_at=datetime.now(timezone.utc),
                quality=DataQuality(valid=True, rows=1),
            )

    provider = CredentialProvider()
    state = {"token": "token-a", "fingerprint": "fp-a"}

    async def loader():
        return (
            {Capability.MARKET_INDICES: RouteDefinition(mode="fixed", providers=["tushare"], provider_fingerprint=state["fingerprint"])},
            {"tushare": True},
            {"tushare": {"token": state["token"]}},
        )

    router = DataHubRouter({"tushare": provider}, {}, route_loader=loader, cache=InMemoryDataCache())
    first = await router.fetch(Capability.MARKET_INDICES, {"force_refresh": True})
    assert first.data[0]["name"] == "token-a"
    state.update(token="token-b", fingerprint="fp-b")
    second = await router.fetch(Capability.MARKET_INDICES, {"force_refresh": True})
    assert second.data[0]["name"] == "token-b"
    assert provider.calls == 2


def test_provider_http_statuses_map_to_safe_retry_categories():
    from app.datahub.providers.base import translate_provider_error

    class HttpError(Exception):
        def __init__(self, status_code):
            self.response = type("Response", (), {"status_code": status_code})()

    assert translate_provider_error(HttpError(403), provider="eastmoney").code is DataHubErrorCode.IP_BLOCKED
    assert translate_provider_error(HttpError(429), provider="eastmoney").code is DataHubErrorCode.RATE_LIMITED
    assert translate_provider_error(HttpError(401), provider="tushare").code is DataHubErrorCode.AUTHENTICATION_FAILED
    assert translate_provider_error(HttpError(503), provider="eastmoney").code is DataHubErrorCode.INTERNAL
    assert translate_provider_error(TimeoutError(), provider="eastmoney").code is DataHubErrorCode.TIMEOUT


def test_production_datahub_without_encryption_key_is_rejected():
    from app.api import admin_datahub
    from app.core.config import settings

    original_env = settings.ENV
    original_key = settings.DATAHUB_CONFIG_ENCRYPTION_KEY
    settings.ENV = "production"
    settings.DATAHUB_CONFIG_ENCRYPTION_KEY = ""
    try:
        with pytest.raises(DataHubError) as exc:
            admin_datahub._service(None)
        assert exc.value.code is DataHubErrorCode.NOT_CONFIGURED
        assert "local-datahub-development-key" not in str(exc.value)
    finally:
        settings.ENV = original_env
        settings.DATAHUB_CONFIG_ENCRYPTION_KEY = original_key


def test_quality_validator_rejects_negative_prices_impossible_changes_and_bad_kline_ranges():
    with pytest.raises(DataHubError) as negative:
        from app.datahub.validators import validate_payload

        validate_payload(Capability.MARKET_INDICES, [{"code": "000001.SS", "name": "上证指数", "price": -1, "change_pct": 0, "data_at": datetime.now(timezone.utc)}])
    assert negative.value.code is DataHubErrorCode.EMPTY_INVALID

    with pytest.raises(DataHubError):
        validate_payload(Capability.STOCK_SNAPSHOT, [{"code": "000001.SS", "name": "上证指数", "price": 3000, "change_pct": 9999, "data_at": datetime.now(timezone.utc)}])

    with pytest.raises(DataHubError):
        validate_payload(Capability.STOCK_KLINE_DAILY, [{"date": "2026-08-22", "open": 10, "close": 9, "high": 8, "low": 7, "volume": 1}])


def test_database_route_override_keeps_capability_ttls_and_default_fingerprints():
    from types import SimpleNamespace
    from app.datahub.platform import _merge_database_routes, default_routes

    base = default_routes()
    rows = [SimpleNamespace(
        capability=Capability.STOCK_FINANCIALS.value,
        mode="fixed",
        provider_order_json=["sina"],
        contract_version="2.0",
        version=7,
    )]
    configs = {"sina": SimpleNamespace(version=3, fingerprint="saved-sina-fp", public_config_json={})}
    routes = _merge_database_routes(base, rows, configs)
    assert routes[Capability.STOCK_FINANCIALS].ttl_seconds == base[Capability.STOCK_FINANCIALS].ttl_seconds
    assert routes[Capability.STOCK_FINANCIALS].stale_ttl_seconds == base[Capability.STOCK_FINANCIALS].stale_ttl_seconds
    assert routes[Capability.STOCK_FINANCIALS].generation == 7
    assert routes[Capability.STOCK_FINANCIALS].provider_fingerprint
    assert routes[Capability.MARKET_INDICES].provider_fingerprint


def test_capability_probe_params_are_safe_and_shape_complete():
    from app.datahub.providers.base import capability_probe_params

    for capability in Capability:
        params = capability_probe_params(capability)
        assert isinstance(params, dict)
        assert "token" not in params and "secret" not in params and "key" not in params
        if capability in {Capability.STOCK_SNAPSHOT, Capability.STOCK_KLINE_DAILY, Capability.STOCK_FINANCIALS, Capability.STOCK_FUND_FLOW, Capability.STOCK_SHAREHOLDERS}:
            assert params.get("code") == "600000.SS"
        if capability in {Capability.DRAGON_TIGER_LIST, Capability.DRAGON_TIGER_SEATS}:
            assert "code" not in params and "trade_date" not in params
        if capability is Capability.STOCK_NEWS:
            assert params.get("source") == "华尔街见闻"
