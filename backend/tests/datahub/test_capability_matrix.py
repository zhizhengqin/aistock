"""Behaviour contracts for the default, independently implemented sources."""

from datetime import datetime, timezone

import pytest

from app.datahub.contracts import Capability
from app.datahub.contracts import DataQuality, DataResult, SectorOverview
from app.datahub.providers.eastmoney import EastmoneyProvider
from app.datahub.providers.sina import SinaProvider
from app.datahub.providers.tencent import TencentProvider


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
async def test_tencent_qfq_kline_uses_real_fqkline_endpoint_and_trusted_date():
    class Client:
        def __init__(self):
            self.calls = []

        def get(self, url, params=None, timeout=None):
            self.calls.append((url, params))
            return _Response(payload={"data": {"qfqday": [["2026-08-21", "10", "11", "12", "9", "1000", "10000"]]}})

    client = Client()
    result = await TencentProvider(http_client=client).fetch(Capability.STOCK_KLINE_DAILY, {"code": "600000.SS"})
    assert client.calls[0][0] == "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
    assert result.data[0].date == "2026-08-21"
    assert result.data_at == datetime(2026, 8, 21, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_rss_provider_parses_optional_global_news_without_legacy_akshare():
    from app.datahub.providers.rss import RssProvider

    xml = """<rss version='2.0'><channel><item><title>海外市场快讯</title><description>摘要</description><link>https://example.test/1</link><pubDate>Fri, 21 Aug 2026 15:00:00 +0800</pubDate></item></channel></rss>"""

    class Client:
        def get(self, url, timeout=None):
            assert url.startswith("https://dedicated.wallstreetcn.com/")
            return _Response(text=xml)

    result = await RssProvider(http_client=Client()).fetch(Capability.STOCK_NEWS, {"source": "华尔街见闻", "limit": 1})
    assert result.provider == "rss"
    assert result.data[0].title == "海外市场快讯"
    assert result.data_at == datetime(2026, 8, 21, 7, 0, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_sina_financials_uses_finance_report_endpoint_and_report_date():
    class Client:
        def get(self, url, params=None, timeout=None):
            assert url == "https://quotes.sina.cn/cn/api/openapi.php/CompanyFinanceService.getFinanceReport2022"
            assert params["paperCode"] == "sh600000"
            return _Response(payload={"result": {"data": {"report_list": {"20260331": {"data": [{"item_title": "净利润", "item_value": "123"}, {"item_title": "营业收入", "item_value": "456"}]}}}}})

    result = await SinaProvider(http_client=Client()).fetch(Capability.STOCK_FINANCIALS, {"code": "600000.SS"})
    assert result.data.report_date == "2026-03-31"
    assert result.data_at == datetime(2026, 3, 31, tzinfo=timezone.utc)


@pytest.mark.asyncio
async def test_sina_quote_sends_origin_headers_required_by_public_endpoint():
    raw = ",".join(["浦发银行", "0", "10", "10.5", "10.2", "10.3"] + [""] * 18 + ["2026-08-22", "15:00:00", "00", "D|36500|330325.00"])

    class Client:
        def __init__(self):
            self.headers = None

        def get(self, url, timeout=None, headers=None):
            self.headers = headers
            return _Response(content=f'hq_str_sh600000="{raw}";'.encode("gbk"))

    client = Client()
    result = await SinaProvider(http_client=client).fetch(Capability.STOCK_SNAPSHOT, {"code": "600000.SS"})
    assert client.headers["Referer"] == "https://stock.finance.sina.com.cn/"
    assert client.headers["User-Agent"]
    assert result.data.price == 10.5
    assert result.data_at == datetime(2026, 8, 22, 7, 0, tzinfo=timezone.utc)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("capability", "path", "payload"),
    [
        (Capability.MARKET_SECTOR_OVERVIEW, "/api/qt/clist/get", {"data": {"diff": [{"f12": "BK001", "f14": "银行", "f2": 100, "f3": 1.2, "f124": 1787295600} ]}}),
        (Capability.STOCK_FUND_FLOW, "/api/qt/stock/fflow/daykline/get", {"data": {"klines": ["2026-08-21,100,20,30,40,10", "2026-08-22,200,30,40,50,20"]}}),
        (Capability.STOCK_SHAREHOLDERS, "https://datacenter-web.eastmoney.com/api/data/v1/get", {"result": {"data": [{"SECURITY_CODE": "600000", "SECURITY_NAME_ABBR": "浦发银行", "END_DATE": "2026-06-30", "HOLDER_NUM": 1000, "HOLDER_NUM_RATIO": -9.09}, {"SECURITY_CODE": "600000", "SECURITY_NAME_ABBR": "浦发银行", "END_DATE": "2026-03-31", "HOLDER_NUM": 1100, "HOLDER_NUM_RATIO": 0}]}}),
        (Capability.STOCK_NEWS, "https://search-api-web.eastmoney.com/search/jsonp", None),
    ],
)
async def test_eastmoney_capabilities_use_documented_endpoint_fixtures(capability, path, payload):
    class Client:
        def get(self, url, params=None, timeout=None):
            assert path in url
            if capability is Capability.STOCK_NEWS:
                return _Response(text='jQuery_news({"result":{"cmsArticleWebOld":[{"title":"市场快讯","content":"内容","date":"2026-08-21 15:00:00","mediaName":"财联社","url":"https://example.test/news"}]}})')
            return _Response(payload=payload)

    params = {"code": "600000.SS", "trade_date": "2026-08-21"}
    result = await EastmoneyProvider(http_client=Client()).fetch(capability, params)
    assert result.data
    assert result.data_at is not None
    if capability is Capability.MARKET_SECTOR_OVERVIEW:
        assert result.data[0].change_pct == 1.2
        assert result.data_at == datetime(2026, 8, 21, 7, 0, tzinfo=timezone.utc)
    elif capability is Capability.STOCK_FUND_FLOW:
        assert result.data.net_main_flow == 300
        assert result.data.net_super_large == 30
        assert len(result.data.daily_flows) == 2
    elif capability is Capability.STOCK_SHAREHOLDERS:
        assert result.data.latest == 1000
        assert result.data.previous == 1100
        assert result.data.change_pct == -9.09
    elif capability is Capability.STOCK_NEWS:
        assert result.data[0].title == "市场快讯"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("capability", "expected_path", "payload"),
    [
        (Capability.MARKET_FUND_FLOW_RANK, "/api/qt/clist/get", {"data": {"diff": [{"f12": "600000", "f14": "浦发银行", "f3": 1.1, "f62": 123, "f184": 2, "f124": 1787295600}]}}),
        (Capability.SECTOR_REALTIME, "/api/qt/clist/get", {"data": {"diff": [{"f12": "BK001", "f14": "银行", "f2": 100, "f3": 1.1, "f6": 200, "f124": 1787295600}]}}),
        (Capability.SECTOR_FUND_FLOW, "/api/qt/clist/get", {"data": {"diff": [{"f12": "BK001", "f14": "银行", "f3": 1.1, "f62": 123, "f184": 2, "f124": 1787295600}]}}),
        (Capability.SECTOR_KLINE, "/api/qt/stock/kline/get", {"data": {"klines": ["2026-08-21,10,11,12,9,100"]}}),
        (Capability.DRAGON_TIGER_LIST, "/api/data/v1/get", {"result": {"data": [{"SECURITY_CODE": "600000", "SECURITY_NAME_ABBR": "浦发银行", "TRADE_DATE": "2026-08-21", "CLOSE_PRICE": 10, "EXPLANATION": "涨幅偏离"}]}}),
        (Capability.DRAGON_TIGER_SEATS, "/api/data/v1/get", {"result": {"data": [{"OPERATEDEPT_NAME": "机构专用", "TRADE_DATE": "2026-08-21", "BUY": 100, "SELL": 20}]}}),
    ],
)
async def test_eastmoney_default_capabilities_have_independent_url_and_shape(capability, expected_path, payload):
    class Client:
        def get(self, url, params=None, timeout=None, **kwargs):
            assert expected_path in url
            if capability is Capability.SECTOR_KLINE:
                assert params["secid"].startswith("90.")
            return _Response(payload=payload)

    result = await EastmoneyProvider(http_client=Client()).fetch(capability, {"code": "600000.SS", "trade_date": "2026-08-21"})
    assert result.data_at is not None
    assert result.quality.rows == 1


@pytest.mark.asyncio
async def test_eastmoney_flow_days_select_daily_endpoint_and_all_a_share_rank_filter():
    calls = []

    class Client:
        def get(self, url, params=None, timeout=None, **kwargs):
            calls.append((url, params))
            if "fflow/daykline" in url:
                payload = {"data": {"klines": ["2026-08-20,10,2,3,4,1", "2026-08-21,20,3,4,5,2"]}}
            else:
                payload = {"data": {"diff": [{"f12": "920001", "f14": "北交所样本", "f3": 1, "f62": 9, "f184": 2, "f124": 1787295600}]}}
            return _Response(payload=payload)

    provider = EastmoneyProvider(http_client=Client())
    result = await provider.fetch(Capability.STOCK_FUND_FLOW, {"code": "600000.SS", "days": 20})
    assert result.data.net_main_flow == 30
    assert calls[0][0].endswith("/api/qt/stock/fflow/daykline/get")
    assert calls[0][1]["klt"] == "101"
    assert calls[0][1]["lmt"] == "20"

    await provider.fetch(Capability.MARKET_FUND_FLOW_RANK, {"limit": 1})
    rank_query = calls[1][1]
    for filter_value in ("m:1+t:2", "m:1+t:23", "m:0+t:6", "m:0+t:80", "m:0+t:81+s:2048"):
        assert filter_value in rank_query["fs"]


@pytest.mark.asyncio
async def test_eastmoney_sector_overview_maps_real_leader_fields_to_representative_stocks():
    class Client:
        def get(self, url, params=None, timeout=None, **kwargs):
            assert all(field in params.get("fields", "") for field in ("f128", "f136", "f138", "f139", "f140", "f141"))
            return _Response(payload={"data": {"diff": [{
                "f12": "BK001", "f14": "银行", "f2": 100, "f3": 1.2,
                "f140": "浦发银行", "f141": "600000", "f136": 2.5,
                "f124": 1787295600,
            }]}})

    result = await EastmoneyProvider(http_client=Client()).fetch(Capability.MARKET_SECTOR_OVERVIEW, {"limit": 1})
    assert result.data[0].representative_stocks == [{"code": "600000.SS", "name": "浦发银行", "price": None, "change_pct": 2.5}]


@pytest.mark.asyncio
async def test_sector_overview_api_preserves_home_contract_and_datahub_meta(monkeypatch):
    from app.api import market

    result = DataResult(
        data=[SectorOverview(name="银行", change_pct=1.2, price=100)],
        capability=Capability.MARKET_SECTOR_OVERVIEW,
        provider="eastmoney",
        data_at=datetime(2026, 8, 21, tzinfo=timezone.utc),
        quality=DataQuality(valid=True, rows=1),
    )

    async def fake_fetch(category, period):
        return result

    monkeypatch.setattr(market, "get_sector_kline", fake_fetch)
    response = await market.sectors_overview(category="银行金融", period="1月")
    assert response["data"]["category"] == "银行金融"
    assert response["data"]["period"] == "1月"
    assert response["data"]["sectors"][0]["name"] == "银行"
    assert response["data"]["stocks"] == []
    assert response["meta"]["provider"] == "eastmoney"


def test_default_routes_only_reference_declared_available_capabilities():
    from app.datahub.platform import default_routes
    from app.datahub.registry import PROVIDER_REGISTRY

    for capability, route in default_routes().items():
        for provider in route.providers:
            definition = PROVIDER_REGISTRY[provider]
            assert capability in definition.capabilities
            assert definition.available or capability.value.startswith("kpl.")
