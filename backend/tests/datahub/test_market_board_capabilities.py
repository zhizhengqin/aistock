from datetime import datetime, timezone

import pytest

from app.datahub.contracts import BoardConstituent, BoardQuote, Capability
from app.datahub.consumer import get_market_board_constituents, get_market_board_quotes
from app.datahub.errors import DataHubErrorCode
from app.datahub.platform import build_router
from app.datahub.providers.eastmoney import EastmoneyProvider


class RecordingClient:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def get(self, url, params=None, **kwargs):
        self.calls.append((url, params or {}, kwargs))
        return Response(self.payload)


class Response:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


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
async def test_consumer_rejects_invalid_kind_and_board_code(monkeypatch):
    with pytest.raises(Exception) as kind_error:
        await get_market_board_quotes("invalid")
    assert getattr(kind_error.value, "code", None) == DataHubErrorCode.VALIDATION

    with pytest.raises(Exception) as board_error:
        await get_market_board_constituents("industry", "not-a-board")
    assert getattr(board_error.value, "code", None) == DataHubErrorCode.VALIDATION
