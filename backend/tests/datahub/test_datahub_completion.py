"""Regression tests for the approved DataHub completion contract."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.datahub.contracts import Capability, FundFlow
from app.datahub.errors import DataHubError, DataHubErrorCode
from app.datahub.platform import default_routes
from app.datahub.providers.eastmoney import EastmoneyProvider
from app.datahub.providers.kpl_native import KplNativeProvider
from app.datahub.providers.sina import SinaProvider
from app.datahub.providers.tencent import TencentProvider
from app.datahub.registry import get_provider


class _Response:
    status_code = 200

    def __init__(self, *, content=b"", payload=None, text=""):
        self.content = content
        self._payload = payload
        self.text = text

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


@pytest.mark.asyncio
async def test_tencent_snapshot_maps_estimation_fields_using_protocol_indexes_and_units():
    fields = [""] * 53
    fields[1] = "示例股份"
    fields[2] = "600000"
    fields[3] = "12.34"
    fields[4] = "12.00"
    fields[39] = "18.5"      # PE(TTM)
    fields[44] = "1234.56"    # float market cap, 亿
    fields[45] = "987.65"     # total market cap, 亿
    fields[46] = "2.34"       # PB
    fields[52] = "20.1"       # static PE
    fields[30] = "20260821161402"
    payload = f'v_sh600000="{"~".join(fields)}";'

    result = await TencentProvider(http_client=type("Client", (), {
        "get": lambda self, url, timeout=None: _Response(content=payload.encode("gbk")),
    })()).fetch(Capability.STOCK_SNAPSHOT, {"code": "600000.SS"})

    assert result.data.pe_ttm == 18.5
    assert result.data.pb == 2.34
    assert result.data.market_cap == 987.65
    assert result.data.float_market_cap == 1234.56
    assert result.data.pe_static == 20.1
    assert result.data.market_cap != 123456.0


@pytest.mark.asyncio
async def test_eastmoney_profile_is_independent_from_snapshot_and_parses_industry():
    class Client:
        def __init__(self):
            self.calls = []

        def get(self, url, params=None, **kwargs):
            self.calls.append((url, params))
            return _Response(payload={"data": {"f57": "600000", "f58": "浦发银行", "f127": "银行", "f124": 1787295600}})

    client = Client()
    result = await EastmoneyProvider(http_client=client).fetch(Capability.STOCK_PROFILE, {"code": "600000.SS"})

    assert result.data.__class__.__name__ == "StockProfile"
    assert result.data.code == "600000.SS"
    assert result.data.name == "浦发银行"
    assert result.data.industry == "银行"
    assert client.calls[0][0].endswith("/api/qt/stock/get")


def test_fund_flow_contract_preserves_missing_values_and_quality_names():
    flow = FundFlow(code="600000.SS", net_main_flow=None, net_super_large=None, net_large=12.0, net_medium=None, net_small=None)
    assert flow.net_main_flow is None

    missing = [name for name in ("net_main_flow", "net_super_large", "net_medium", "net_small") if getattr(flow, name) is None]
    assert missing == ["net_main_flow", "net_super_large", "net_medium", "net_small"]


@pytest.mark.asyncio
async def test_sina_daily_fund_flow_parses_only_real_net_amount_without_zero_fill():
    class Client:
        def get(self, url, params=None, **kwargs):
            assert "MoneyFlow.ssl_qsfx_zjlrqs" in url
            return _Response(text=' [{"opendate":"2026-08-21","trade":"12.3","netamount":"4.5"}] ')

    result = await SinaProvider(http_client=Client()).fetch(
        Capability.STOCK_FUND_FLOW,
        {"code": "600000.SS", "days": 5},
    )

    assert result.data.net_main_flow == 4.5
    assert result.data.net_super_large is None
    assert result.data.net_large is None
    assert result.data.net_medium is None
    assert result.data.net_small is None
    assert result.quality.missing_fields == ["net_super_large", "net_large", "net_medium", "net_small"]


@pytest.mark.asyncio
async def test_sina_daily_fund_flow_preserves_a_real_zero_net_amount():
    class Client:
        def get(self, url, params=None, **kwargs):
            return _Response(text='[{"opendate":"2026-08-21","netamount":0}]')

    result = await SinaProvider(http_client=Client()).fetch(
        Capability.STOCK_FUND_FLOW,
        {"code": "600000.SS", "days": 1},
    )

    assert result.data.net_main_flow == 0


@pytest.mark.asyncio
async def test_native_kpl_requires_user_id_and_token_and_maps_documented_capabilities():
    provider = KplNativeProvider(token="runtime-token", user_id="operator", http_client=None)
    assert get_provider("kpl_native").credential_fields[0].key == "user_id"
    assert get_provider("kpl_native").credential_fields[1].key == "token"
    assert Capability("kpl_native.stock_tags") in get_provider("kpl_native").capabilities
    assert Capability("kpl_native.plate_ranking") in get_provider("kpl_native").capabilities
    assert Capability("kpl_native.plate_constituents") in get_provider("kpl_native").capabilities
    assert Capability("kpl_native.stock_ranking") in get_provider("kpl_native").capabilities

    with pytest.raises(DataHubError) as missing:
        await KplNativeProvider(token="", user_id="operator", http_client=None).fetch(
            Capability("kpl_native.stock_tags"),
            {"stock_code": "600000", "trade_date": "20260821"},
        )
    assert missing.value.code is DataHubErrorCode.NOT_CONFIGURED


@pytest.mark.asyncio
async def test_native_kpl_http_200_business_error_is_invalid_credentials():
    class Client:
        def post(self, url, data):
            return _Response(payload={"errcode": 1016, "errmsg": "not login"})

    provider = KplNativeProvider(token="runtime-token", user_id="operator", http_client=Client())
    with pytest.raises(DataHubError) as error:
        await provider.get_hq_plate("600000", "20260821")
    assert error.value.code is DataHubErrorCode.AUTHENTICATION_FAILED
    assert "未登录" in error.value.message or "凭证" in error.value.message
    assert "runtime-token" not in str(error.value.to_response())


def test_tushare_and_native_kpl_routes_are_independent_and_honest():
    routes = default_routes()
    assert Capability.KPL_LIMIT_LIST in get_provider("tushare").capabilities
    assert Capability.KPL_LIMIT_LIST not in get_provider("kpl_native").capabilities
    assert routes[Capability.KPL_LIMIT_LIST].providers == ["tushare"]
    assert routes[Capability("kpl_native.stock_tags")].providers == ["kpl_native"]
    assert routes[Capability("kpl_native.plate_ranking")].providers == ["kpl_native"]
    assert routes[Capability("kpl_native.plate_constituents")].providers == ["kpl_native"]
    assert routes[Capability("kpl_native.stock_ranking")].providers == ["kpl_native"]
