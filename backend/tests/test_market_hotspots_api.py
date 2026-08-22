from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from app.schemas.market_hotspots import (
    ConstituentsDataset,
    HotspotDataset,
    MarketCloudDataset,
    MarketCloudNode,
    MarketDatasetMeta,
    MarketHotspot,
    RepresentativeStock,
)


NOW = datetime(2026, 8, 22, 7, 30, tzinfo=timezone.utc)


def _meta(capability):
    return MarketDatasetMeta(capability=capability, provider="eastmoney", data_at=NOW, fetched_at=NOW, trade_date="2026-08-22")


def test_market_hotspots_route_validates_kind_and_limit(client):
    assert client.get("/api/stocks/market-hotspots?kind=invalid").status_code == 422
    assert client.get("/api/stocks/market-hotspots?kind=industry&limit=13").status_code == 422


def test_market_hotspots_route_returns_product_data_and_meta(client):
    payload = HotspotDataset(kind="industry", items=[MarketHotspot(board_code="BK0475", board_name="银行", kind="industry", change_pct=1, hot_score=80, rank=1, data_at=NOW)], meta=_meta("market.board_quotes"))
    with patch("app.api.market.HotspotService") as service_cls:
        service_cls.return_value.get_hotspots = AsyncMock(return_value=payload)
        response = client.get("/api/stocks/market-hotspots?kind=industry")
    assert response.status_code == 200
    assert response.json()["code"] == 0
    assert response.json()["data"]["items"][0]["board_code"] == "BK0475"
    assert response.json()["meta"]["provider"] == "eastmoney"


def test_market_cloud_and_constituents_routes_have_defaults_and_validate_board(client):
    cloud = MarketCloudDataset(kind="industry", nodes=[MarketCloudNode(code="BK0475", name="银行", kind="industry", value=100, change_pct=1, data_at=NOW)], meta=_meta("market.board_quotes"))
    stocks = ConstituentsDataset(kind="industry", board_code="BK0475", items=[RepresentativeStock(code="600000.SS", name="浦发银行", price=10, change_pct=1, rank=1, data_at=NOW)], meta=_meta("market.board_constituents"))
    with patch("app.api.market.HotspotService") as service_cls:
        service_cls.return_value.get_market_cloud = AsyncMock(return_value=cloud)
        service_cls.return_value.get_constituents = AsyncMock(return_value=stocks)
        assert client.get("/api/stocks/market-cloud?kind=industry").status_code == 200
        assert client.get("/api/stocks/boards/BK0475/constituents?kind=industry").status_code == 200
    assert client.get("/api/stocks/boards/NOPE/constituents?kind=industry").status_code == 422
    assert client.get("/api/stocks/market-cloud?kind=industry&limit=81").status_code == 422


def test_legacy_sector_overview_route_is_removed(client):
    assert client.get("/api/stocks/sectors/overview").status_code == 404
