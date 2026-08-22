from datetime import datetime, timezone

import pandas as pd
import pytest

from app.datahub.contracts import Capability, KplLimitItem
from app.datahub.errors import DataHubError, DataHubErrorCode
from app.datahub.providers.akshare import AkshareProvider
from app.datahub.providers.kpl_native import KplNativeProvider
from app.datahub.providers.tushare import TushareProvider


@pytest.mark.asyncio
async def test_akshare_indices_use_category_parameter_once_and_return_typed_rows():
    class FakeAk:
        def __init__(self):
            self.calls = []

        def stock_zh_index_spot_em(self, **kwargs):
            self.calls.append(kwargs)
            return pd.DataFrame(
                [
                    {"代码": "000001", "名称": "上证指数", "最新价": 3000, "涨跌幅": 0.2},
                    {"代码": "399006", "名称": "创业板指", "最新价": 2100, "涨跌幅": 1.5},
                ]
            )

    ak = FakeAk()
    provider = AkshareProvider(ak_module=ak)
    result = await provider.fetch(Capability.MARKET_INDICES, {})

    assert ak.calls == [{"symbol": "上证系列指数"}]
    assert result.data[0].name == "上证指数"
    assert result.data[0].price == 3000


@pytest.mark.asyncio
async def test_akshare_probe_runs_sync_call_without_patching_uvloop():
    class FakeAk:
        def stock_zh_index_spot_em(self, **kwargs):
            return pd.DataFrame([{"代码": "000001", "名称": "上证指数", "最新价": 3000, "涨跌幅": 0.2}])

    provider = AkshareProvider(ak_module=FakeAk())
    probe = await provider.probe(Capability.MARKET_INDICES)
    assert probe.status == "ok"
    assert probe.rows == 1


@pytest.mark.asyncio
async def test_tushare_distinguishes_missing_token_and_permission_error():
    with pytest.raises(DataHubError) as missing:
        await TushareProvider(token="").fetch(Capability.KPL_LIMIT_LIST, {"trade_date": "20260821"})
    assert missing.value.code is DataHubErrorCode.NOT_CONFIGURED

    class FakePro:
        def kpl_list(self, **kwargs):
            raise RuntimeError("积分不足")

    provider = TushareProvider(token="secret", pro_client=FakePro())
    with pytest.raises(DataHubError) as denied:
        await provider.fetch(Capability.KPL_LIMIT_LIST, {"trade_date": "20260821"})
    assert denied.value.code is DataHubErrorCode.PERMISSION_DENIED
    assert "secret" not in str(denied.value.to_response())


@pytest.mark.asyncio
async def test_tushare_returns_pydantic_capability_rows():
    class FakePro:
        def kpl_list(self, **kwargs):
            return [{"ts_code": "000001.SZ", "name": "示例", "trade_date": "20260821", "tag": "涨停"}]

    result = await TushareProvider(token="secret", pro_client=FakePro()).fetch(
        Capability.KPL_LIMIT_LIST,
        {"trade_date": "20260821"},
    )
    assert isinstance(result.data[0], KplLimitItem)
    assert result.data[0].ts_code == "000001.SZ"


@pytest.mark.asyncio
async def test_tushare_provider_clients_are_token_scoped_without_global_set_token(monkeypatch):
    import sys
    import types

    calls = []

    class FakePro:
        def kpl_list(self, **kwargs):
            return [{"ts_code": "000001.SZ", "name": "示例", "trade_date": "20260821", "tag": "涨停"}]

    fake_ts = types.SimpleNamespace(pro_api=lambda token: (calls.append(token) or FakePro()))
    monkeypatch.setitem(sys.modules, "tushare", fake_ts)
    first = TushareProvider(token="token-a")
    second = TushareProvider(token="token-b")
    await first.fetch(Capability.KPL_LIMIT_LIST, {"trade_date": "20260821"})
    await second.fetch(Capability.KPL_LIMIT_LIST, {"trade_date": "20260821"})
    assert calls == ["token-a", "token-b"]


@pytest.mark.asyncio
async def test_native_kpl_requires_explicit_token_and_never_embeds_sample_token():
    provider = KplNativeProvider(token="", http_client=None)
    with pytest.raises(DataHubError) as missing:
        await provider.fetch(Capability.MARKET_AUCTION_OPEN, {"stock_code": "000001", "trade_date": "20260821"})
    assert missing.value.code is DataHubErrorCode.NOT_CONFIGURED
    assert "Token=" not in repr(provider)


@pytest.mark.asyncio
async def test_native_kpl_uses_runtime_token_for_only_documented_hq_plate_action():
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"trend": {"code": "000001", "day": "20260821"}, "pankou": {"tag": "金融科技"}, "stockplate": [["券商概念", "说明"]]}

    class Client:
        def __init__(self):
            self.payload = None

        def post(self, url, data):
            self.payload = data
            return Response()

    client = Client()
    provider = KplNativeProvider(token="runtime-token", user_id="operator", http_client=client)
    result = await provider.get_hq_plate("000001", "20260821")
    assert result["trend"]["code"] == "000001"
    assert result["pankou"]["tag"] == "金融科技"
    assert client.payload["Token"] == "runtime-token"
    assert "runtime-token" not in str(result)


@pytest.mark.asyncio
async def test_native_kpl_rejects_undocumented_capability_actions():
    provider = KplNativeProvider(token="runtime-token", http_client=None)
    with pytest.raises(DataHubError) as unsupported:
        await provider.fetch(Capability.MARKET_AUCTION_OPEN, {"trade_date": "20260821"})
    assert unsupported.value.code is DataHubErrorCode.UNSUPPORTED
