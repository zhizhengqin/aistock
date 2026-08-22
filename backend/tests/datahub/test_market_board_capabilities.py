from datetime import datetime, timezone
import json
import zlib

import pytest

from app.datahub.contracts import BoardConstituent, BoardQuote, Capability, DataQuality, DataResult
from app.datahub.consumer import get_market_board_constituents, get_market_board_quotes
from app.datahub.errors import DataHubError, DataHubErrorCode
from app.datahub.platform import build_router
from app.datahub.providers.eastmoney import EastmoneyProvider
from app.datahub.providers.sina import SinaProvider


class RecordingClient:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def get(self, url, params=None, **kwargs):
        self.calls.append((url, params or {}, kwargs))
        return Response(self.payload)


class Response:
    def __init__(self, payload=None, *, content=b""):
        self.payload = payload
        self.content = content

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class SinaRecordingClient:
    def __init__(self, board_payload, constituent_payload=None):
        self.board_payload = board_payload
        self.constituent_payload = constituent_payload
        self.calls = []

    def get(self, url, params=None, timeout=None, headers=None):
        self.calls.append((url, params or {}, timeout, headers or {}))
        hq = ','.join(['上证指数', '0', '3000', '3010', '0'] + [''] * 20 + ['2026-08-22', '15:00:00'])
        if 'hq.sinajs.cn' in url:
            return Response(content=f'hq_str_sh000001="{hq}";'.encode('gbk'))
        if 'getHQNodeData' in url:
            return Response(content=f'var data = {self.constituent_payload};'.encode('gbk'))
        return Response(content=f'var data = {self.board_payload};'.encode('gbk'))


def _canonical(kind, node):
    return f"BK{zlib.crc32(f'{kind}:{node}'.encode()) & 0xffffffff:010d}"


def _sina_board_payload(*nodes):
    return json.dumps({
        node: ",".join([node, name, '10', '100', '2.0', change, '1000', turnover, 'sh600000', '2.5', '10.5', '0.2', '浦发银行'])
        for node, name, change, turnover in nodes
    }, ensure_ascii=False)


def _board_payload():
    return {
        "data": {
            "diff": [
                {
                    "f12": "BK0475",
                    "f14": "银行",
                    "f3": 1.25,
                    "f6": 123456789,
                    "f20": 987654321,
                    "f104": 12,
                    "f105": 3,
                    "f106": 1,
                    "f128": "浦发银行",
                    "f140": "600000",
                    "f136": 2.5,
                    "f124": 1787295600,
                }
            ]
        }
    }


@pytest.mark.asyncio
async def test_eastmoney_board_quotes_use_kind_selector_and_real_leader_fields():
    client = RecordingClient(_board_payload())
    result = await EastmoneyProvider(http_client=client).fetch(Capability.MARKET_BOARD_QUOTES, {"kind": "industry"})

    row = result.data[0]
    assert isinstance(row, BoardQuote)
    assert row.board_code == "BK0475"
    assert row.leader_name == "浦发银行"
    assert row.leader_code == "600000"
    assert row.leader_change_pct == 2.5
    assert client.calls[0][1]["fs"] == "m:90+t:2"
    assert "f12" in client.calls[0][1]["fields"]
    assert "limit" not in client.calls[0][1]


@pytest.mark.asyncio
async def test_eastmoney_theme_quotes_use_theme_selector():
    client = RecordingClient(_board_payload())
    await EastmoneyProvider(http_client=client).fetch(Capability.MARKET_BOARD_QUOTES, {"kind": "theme"})
    assert client.calls[0][1]["fs"] == "m:90+t:3"


@pytest.mark.asyncio
async def test_eastmoney_constituents_validate_board_and_map_optional_numbers():
    client = RecordingClient(
        {
            "data": {
                "diff": [
                    {"f2": None, "f3": None, "f6": None, "f12": "600000", "f14": "浦发银行", "f20": None, "f124": 1787295600}
                ]
            }
        }
    )
    result = await EastmoneyProvider(http_client=client).fetch(
        Capability.MARKET_BOARD_CONSTITUENTS,
        {"kind": "industry", "board_code": "BK0475", "limit": 20},
    )
    row = result.data[0]
    assert isinstance(row, BoardConstituent)
    assert row.code == "600000.SS"
    assert row.price is None
    assert row.change_pct is None
    assert row.market_cap is None
    assert client.calls[0][1]["fs"] == "b:BK0475"
    assert client.calls[0][1]["pz"] == "20"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "expected_url"),
    [
        ("industry", "https://vip.stock.finance.sina.com.cn/q/view/newSinaHy.php"),
        ("theme", "https://money.finance.sina.com.cn/q/view/newFLJK.php?param=class"),
    ],
)
async def test_sina_board_quotes_parse_prefixed_gbk_json_and_use_index_timestamp(kind, expected_url):
    client = SinaRecordingClient(_sina_board_payload(("node-bank", "银行", "1.25", "123456.7")))

    result = await SinaProvider(http_client=client).fetch(Capability.MARKET_BOARD_QUOTES, {"kind": kind})

    row = result.data[0]
    assert isinstance(row, BoardQuote)
    assert row.board_code == _canonical(kind, "node-bank")
    assert row.board_name == "银行"
    assert row.change_pct == 1.25
    assert row.turnover == 123456.7
    assert row.leader_code == "600000.SS"
    assert row.leader_name == "浦发银行"
    assert row.data_at == datetime(2026, 8, 22, 7, 0, tzinfo=timezone.utc)
    assert result.data_at == row.data_at
    assert client.calls[0][0] == expected_url
    assert client.calls[0][2] == 10
    assert client.calls[0][3]["Referer"] == "https://stock.finance.sina.com.cn/"


@pytest.mark.asyncio
async def test_sina_constituents_rebuild_canonical_board_mapping_and_scale_market_cap():
    client = SinaRecordingClient(
        _sina_board_payload(("node-bank", "银行", "1.25", "123456.7")),
        '[{"code":"600000","name":"浦发银行","trade":"10.5","changepercent":"1.2","amount":"123456","mktcap":"9876","ticktime":"15:00:00"}]',
    )
    board_code = _canonical("industry", "node-bank")

    result = await SinaProvider(http_client=client).fetch(
        Capability.MARKET_BOARD_CONSTITUENTS,
        {"kind": "industry", "board_code": board_code, "limit": 1},
    )

    row = result.data[0]
    assert isinstance(row, BoardConstituent)
    assert row.code == "600000.SS"
    assert row.price == 10.5
    assert row.change_pct == 1.2
    assert row.turnover == 123456
    assert row.market_cap == 98760000
    assert row.data_at == datetime(2026, 8, 22, 7, 0, tzinfo=timezone.utc)
    constituent_call = next(call for call in client.calls if "getHQNodeData" in call[0])
    assert constituent_call[1] == {"page": 1, "num": 1, "sort": "changepercent", "asc": 0, "node": "node-bank", "symbol": "", "_s_r_a": "page"}


@pytest.mark.asyncio
async def test_sina_board_code_collision_fails_closed(monkeypatch):
    client = SinaRecordingClient(
        _sina_board_payload(
            ("node-bank", "银行", "1.25", "123456.7"),
            ("node-tech", "科技", "2.25", "223456.7"),
        )
    )
    monkeypatch.setattr("app.datahub.providers.sina.zlib.crc32", lambda value: 42)

    with pytest.raises(DataHubError) as error:
        await SinaProvider(http_client=client).fetch(Capability.MARKET_BOARD_QUOTES, {"kind": "industry"})
    assert error.value.code is DataHubErrorCode.SCHEMA_CHANGED


@pytest.mark.asyncio
async def test_market_board_router_retries_sina_after_eastmoney_internal_error():
    from app.datahub.router import DataHubRouter, RouteDefinition

    data_at = datetime.now(timezone.utc)

    class EastmoneyUnavailable:
        async def fetch(self, capability, params):
            raise DataHubError(DataHubErrorCode.INTERNAL, "东方财富连接中断", provider="eastmoney")

    class SinaFallback:
        async def fetch(self, capability, params):
            row = BoardQuote(
                board_code="BK1234567890",
                board_name="银行",
                kind="industry",
                change_pct=1.2,
                turnover=123,
                data_at=data_at,
            )
            return DataResult(
                data=[row],
                capability=capability,
                provider="sina",
                data_at=data_at,
                quality=DataQuality(valid=True, rows=1),
            )

    router = DataHubRouter(
        {"eastmoney": EastmoneyUnavailable(), "sina": SinaFallback()},
        {
            Capability.MARKET_BOARD_QUOTES: RouteDefinition(
                mode="auto",
                providers=["eastmoney", "sina"],
                ttl_seconds=300,
                stale_ttl_seconds=3600,
            )
        },
    )

    result = await router.fetch(Capability.MARKET_BOARD_QUOTES, {"kind": "industry"})

    assert result.provider == "sina"
    assert result.attempts == ["eastmoney", "sina"]
    assert result.fallback_used is True


@pytest.mark.asyncio
async def test_consumer_rejects_invalid_kind_and_board_code(monkeypatch):
    with pytest.raises(Exception) as kind_error:
        await get_market_board_quotes("invalid")
    assert getattr(kind_error.value, "code", None) == DataHubErrorCode.VALIDATION

    with pytest.raises(Exception) as board_error:
        await get_market_board_constituents("industry", "not-a-board")
    assert getattr(board_error.value, "code", None) == DataHubErrorCode.VALIDATION


def test_consumer_accepts_ten_digit_sina_canonical_board_code_and_rejects_longer_values():
    from app.datahub.consumer import _validate_board_code

    assert _validate_board_code("BK1234567890") == "BK1234567890"
    with pytest.raises(DataHubError) as error:
        _validate_board_code("BK12345678901")
    assert error.value.code is DataHubErrorCode.VALIDATION
