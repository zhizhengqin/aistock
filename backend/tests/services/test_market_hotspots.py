from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.datahub.contracts import BoardConstituent, BoardQuote, DataResult, Capability, DataQuality
from app.datahub.errors import DataHubError, DataHubErrorCode
from app.services.market_hotspots import (
    HotspotService,
    _trade_date,
    calculate_hotspots,
    classify_trend,
    percentile_rank,
)


NOW = datetime(2026, 8, 22, 7, 30, tzinfo=timezone.utc)
SNAPSHOT_FETCHED = datetime(2026, 8, 22, 9, 15, tzinfo=timezone.utc)


def quote(code, change, turnover, *, cap=100, rise=5, fall=5, flat=0, day=NOW):
    return BoardQuote(
        board_code=code,
        board_name=code,
        kind="industry",
        change_pct=change,
        turnover=turnover,
        market_cap=cap,
        rise_count=rise,
        fall_count=fall,
        flat_count=flat,
        data_at=day,
    )


def test_percentile_rank_has_deterministic_endpoints_and_same_value_midpoint():
    assert percentile_rank(1, [1, 2, 3]) == 0
    assert percentile_rank(3, [1, 2, 3]) == 100
    assert percentile_rank(2, [1, 2, 3]) == 50
    assert percentile_rank(8, [8, 8, 8]) == 50


def test_trade_date_converts_aware_datetime_to_shanghai_calendar_date():
    assert _trade_date(datetime(2026, 8, 22, 23, 30, tzinfo=timezone.utc)) == "2026-08-23"
    assert _trade_date(datetime(2026, 8, 22, 1, 30, tzinfo=timezone.utc)) == "2026-08-22"


def test_hotspot_scoring_is_order_stable_and_reweights_missing_factors():
    rows = [quote("B", 1, 100), quote("A", 1, 100, cap=50, rise=10, fall=0)]
    shuffled = list(reversed(rows))
    first, warnings = calculate_hotspots(rows, kind="industry")
    second, _ = calculate_hotspots(shuffled, kind="industry")
    assert [item.board_code for item in first] == [item.board_code for item in second] == ["A", "B"]
    assert all(item.hot_score == round(item.hot_score, 1) for item in first)
    assert warnings == []

    missing, warnings = calculate_hotspots([quote("A", 1, None)], kind="industry")
    assert missing[0].hot_score == 50.0
    assert "turnover" in warnings


def test_missing_change_is_omitted_from_hotspot_ranking():
    rows = [quote("A", None, 100), quote("B", 1, 100)]
    result, _ = calculate_hotspots(rows, kind="industry")
    assert [item.board_code for item in result] == ["B"]


def test_trend_classification_covers_new_heating_cooling_and_cold_start():
    assert classify_trend("A", 80, 1, []) == ("insufficient_history", 0, None)
    assert classify_trend("A", 80, 1, [{"trade_date": "20260821", "items": []}])[0] == "new"
    history = [
        {"trade_date": "20260821", "items": [{"board_code": "A", "hot_score": 60, "rank": 2}]},
        {"trade_date": "20260820", "items": [{"board_code": "A", "hot_score": 40, "rank": 4}]},
    ]
    assert classify_trend("A", 80, 1, history) == ("heating", 3, 1)
    cooling = [
        {"trade_date": "20260821", "items": [{"board_code": "A", "hot_score": 60, "rank": 2}]},
        {"trade_date": "20260820", "items": [{"board_code": "A", "hot_score": 80, "rank": 1}]},
    ]
    assert classify_trend("A", 40, 3, cooling)[0] == "cooling"


@pytest.mark.asyncio
async def test_service_live_result_queries_history_once_and_keeps_metadata(monkeypatch):
    class Store:
        def __init__(self):
            self.calls = []

        def history(self, *args, **kwargs):
            self.calls.append((args, kwargs))
            return []

    store = Store()
    result = DataResult(
        data=[quote("A", 2, 100)], capability=Capability.MARKET_BOARD_QUOTES, provider="eastmoney", data_at=NOW,
        quality=DataQuality(valid=True, rows=1),
    )
    monkeypatch.setattr("app.services.market_hotspots.get_market_board_quotes", lambda kind: _resolved(result))
    service = HotspotService(db=SimpleNamespace(), snapshot_store=store)
    payload = await service.get_hotspots("industry", limit=12)
    assert payload.items[0].board_code == "A"
    assert payload.meta.provider == "eastmoney"
    assert len(store.calls) == 1


async def _resolved(value):
    return value


@pytest.mark.asyncio
async def test_service_raises_typed_503_when_live_and_snapshot_are_unavailable(monkeypatch):
    async def fail(kind):
        raise DataHubError(DataHubErrorCode.INTERNAL, "数据源失败")

    class Store:
        def latest(self, *args, **kwargs):
            return None

    monkeypatch.setattr("app.services.market_hotspots.get_market_board_quotes", fail)
    service = HotspotService(db=SimpleNamespace(), snapshot_store=Store())
    with pytest.raises(DataHubError) as exc:
        await service.get_hotspots("industry")
    assert exc.value.status_code == 503
    assert "热点" in exc.value.message


@pytest.mark.asyncio
async def test_hotspot_snapshot_fallback_uses_snapshot_fetched_at_and_payload_data_at(monkeypatch):
    async def fail(kind):
        raise DataHubError(DataHubErrorCode.INTERNAL, "数据源失败")

    snapshot = SimpleNamespace(
        trade_date="2026-08-22",
        fetched_at=SNAPSHOT_FETCHED,
        payload_json=[quote("A", 2, 100).model_dump(mode="json")],
    )

    class Store:
        def latest(self, *args, **kwargs):
            return snapshot

        def history(self, *args, **kwargs):
            return []

    monkeypatch.setattr("app.services.market_hotspots.get_market_board_quotes", fail)
    payload = await HotspotService(SimpleNamespace(), snapshot_store=Store()).get_hotspots("industry")
    assert payload.meta.provider == "历史快照"
    assert payload.meta.freshness == "stale"
    assert payload.meta.trade_date == "2026-08-22"
    assert payload.meta.fetched_at == SNAPSHOT_FETCHED
    assert payload.meta.data_at == NOW


@pytest.mark.asyncio
async def test_constituent_snapshot_fallback_uses_snapshot_fetched_at_and_payload_data_at(monkeypatch):
    async def fail(kind, board_code, limit):
        raise DataHubError(DataHubErrorCode.INTERNAL, "数据源失败")

    snapshot = SimpleNamespace(
        trade_date="2026-08-21",
        fetched_at=SNAPSHOT_FETCHED,
        payload_json=[{"code": "600000.SS", "name": "浦发银行", "price": 10, "change_pct": 1, "data_at": NOW.isoformat()}],
    )

    class Store:
        def latest(self, *args, **kwargs):
            return snapshot

    monkeypatch.setattr("app.services.market_hotspots.get_market_board_constituents", fail)
    payload = await HotspotService(SimpleNamespace(), snapshot_store=Store()).get_constituents("industry", "BK0001")
    assert payload.meta.provider == "历史快照"
    assert payload.meta.freshness == "stale"
    assert payload.meta.trade_date == "2026-08-21"
    assert payload.meta.fetched_at == SNAPSHOT_FETCHED
    assert payload.meta.data_at == NOW


@pytest.mark.asyncio
async def test_incompatible_future_history_hides_trend_and_warns(monkeypatch):
    class Store:
        def history(self, *args, **kwargs):
            return [SimpleNamespace(trade_date="2026-08-23", payload_json=[{"board_code": "A", "hot_score": 60, "rank": 2}])]

    result = DataResult(
        data=[quote("A", 2, 100)], capability=Capability.MARKET_BOARD_QUOTES, provider="eastmoney", data_at=NOW,
        quality=DataQuality(valid=True, rows=1),
    )
    monkeypatch.setattr("app.services.market_hotspots.get_market_board_quotes", lambda kind: _resolved(result))
    payload = await HotspotService(SimpleNamespace(), snapshot_store=Store()).get_hotspots("industry")
    assert payload.items[0].trend_status == "insufficient_history"
    assert "历史基准暂不可比" in payload.meta.warnings


@pytest.mark.asyncio
async def test_theme_cloud_places_missing_market_cap_after_non_null_caps(monkeypatch):
    result = DataResult(
        data=[quote("A", 1, 100, cap=None), quote("B", 2, 100, cap=100)],
        capability=Capability.MARKET_BOARD_QUOTES,
        provider="eastmoney",
        data_at=NOW,
        quality=DataQuality(valid=True, rows=2),
    )
    monkeypatch.setattr("app.services.market_hotspots.get_market_board_quotes", lambda kind: _resolved(result))
    payload = await HotspotService(SimpleNamespace(), snapshot_store=SimpleNamespace(history=lambda *a, **k: [])).get_market_cloud("theme", limit=1)
    assert [node.code for node in payload.nodes] == ["B"]


@pytest.mark.asyncio
async def test_daily_snapshot_persists_scored_hotspot_rows(monkeypatch):
    class Store:
        def __init__(self):
            self.upserts = []
            self.runs = []

        def upsert(self, *args):
            self.upserts.append(args)

        def cleanup(self, **kwargs):
            return 0

        def record_run(self, *args, **kwargs):
            self.runs.append((args, kwargs))

    store = Store()

    async def board_quotes(kind):
        return DataResult(
            data=[quote("A", 2, 100)], capability=Capability.MARKET_BOARD_QUOTES, provider="eastmoney", data_at=NOW,
            quality=DataQuality(valid=True, rows=1),
        )

    async def constituents(kind, board_code, limit):
        return DataResult(
            data=[BoardConstituent(code="600000.SS", name="浦发银行", price=10, change_pct=1, data_at=NOW)],
            capability=Capability.MARKET_BOARD_CONSTITUENTS, provider="eastmoney", data_at=NOW,
            quality=DataQuality(valid=True, rows=1),
        )

    monkeypatch.setattr("app.services.market_hotspots.get_market_board_quotes", board_quotes)
    monkeypatch.setattr("app.services.market_hotspots.get_market_board_constituents", constituents)
    summary = await HotspotService(SimpleNamespace(), snapshot_store=store).capture_daily_snapshot()
    assert summary["constituents_saved"] == 2
    hotspot_payloads = [args[-1] for args in store.upserts if args[0] == "market.hotspots.v1"]
    assert hotspot_payloads and hotspot_payloads[0][0]["hot_score"] == 50.0
    assert store.runs and store.runs[0][1]["status"] == "success"
