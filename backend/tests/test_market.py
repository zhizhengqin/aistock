import pandas as pd
from unittest.mock import patch
from tests.conftest import client
from datetime import datetime, timezone
from app.datahub.contracts import Capability, DataQuality, DataResult, MarketIndex


def _indices_result():
    now = datetime(2026, 8, 21, tzinfo=timezone.utc)
    rows = [MarketIndex(code=code, name=name, price=price, change_pct=change, data_at=now) for code, name, price, change in [
        ("000001.SS", "上证指数", 3200.5, 0.32),
        ("399001.SZ", "深证成指", 10000.0, -0.12),
        ("399006.SZ", "创业板指", 2100.0, 1.5),
        ("000300.SS", "沪深300", 3800.1, -0.2),
        ("000688.SS", "科创50", 900.0, 0.1),
    ]]
    return DataResult(data=rows, capability=Capability.MARKET_INDICES, provider="tencent", data_at=now, quality=DataQuality(valid=True, rows=len(rows)))


def test_market_indices_route(client, fake_redis):
    with patch("app.api.market.get_market_indices", return_value=_indices_result()):
        resp = client.get("/api/stocks/market-indices")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert isinstance(data, list)
    assert len(data) == 5
    codes = [d["code"] for d in data]
    assert "000001.SS" in codes


def test_market_indices_cache_hit(client, fake_redis):
    with patch("app.api.market.get_market_indices", return_value=_indices_result()):
        resp = client.get("/api/stocks/market-indices")
    assert resp.status_code == 200
    assert len(resp.json()["data"]) == 5


def test_legacy_sectors_overview_route_removed(client):
    assert client.get("/api/stocks/sectors/overview").status_code == 404
