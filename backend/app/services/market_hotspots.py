"""Transparent market hotspot scoring, trend annotation and snapshot fallback."""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from app.datahub.contracts import BoardConstituent, BoardQuote, DataResult, Freshness
from app.datahub.consumer import get_market_board_constituents, get_market_board_quotes
from app.datahub.errors import DataHubError, DataHubErrorCode
from app.datahub.ingestion import SnapshotStore
from app.schemas.market_hotspots import (
    ConstituentsDataset,
    HotspotDataset,
    MarketCloudDataset,
    MarketCloudNode,
    MarketDatasetMeta,
    MarketHotspot,
    RepresentativeStock,
)


WEIGHTS = {"change_pct": 0.5, "turnover": 0.3, "breadth": 0.2}
DATASET_HOTSPOTS = "market.hotspots.v1"
DATASET_CONSTITUENTS = "market.board_constituents.v1"
SCHEMA_VERSION = "1.0"


def percentile_rank(value: float, values: Iterable[float]) -> float:
    """Return a stable 0..100 percentile, averaging equal-value positions."""

    ordered = sorted(float(item) for item in values if item is not None and math.isfinite(float(item)))
    if not ordered:
        return 50.0
    if len(ordered) == 1 or ordered[0] == ordered[-1]:
        return 50.0
    positions = [index for index, item in enumerate(ordered) if item == float(value)]
    if positions:
        position = sum(positions) / len(positions)
    else:
        position = sum(1 for item in ordered if item < float(value))
    return round(position / (len(ordered) - 1) * 100, 6)


def _row_value(row: Any, field: str) -> Any:
    if isinstance(row, Mapping):
        return row.get(field)
    return getattr(row, field, None)


def _change(row: Any) -> float | None:
    value = _row_value(row, "change_pct")
    return float(value) if value is not None else None


def _turnover_factor(row: Any) -> float | None:
    value = _row_value(row, "turnover")
    if value is None:
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return math.log1p(max(0.0, value))


def _breadth_factor(row: Any) -> float | None:
    rise, fall, flat = (_row_value(row, key) for key in ("rise_count", "fall_count", "flat_count"))
    if rise is None or fall is None or flat is None:
        return None
    total = float(rise) + float(fall) + float(flat)
    return float(rise) / total if total > 0 else None


def calculate_hotspots(rows: Iterable[BoardQuote], *, kind: str) -> tuple[list[MarketHotspot], list[str]]:
    """Score one full board cross-section without depending on input order."""

    values = list(rows)
    warnings: list[str] = []
    factors = {
        "change_pct": [_change(row) for row in values],
        "turnover": [_turnover_factor(row) for row in values],
        "breadth": [_breadth_factor(row) for row in values],
    }
    for name, factor_values in factors.items():
        if not any(value is not None for value in factor_values):
            warnings.append(name)
    available_values = {
        name: [float(value) for value in factor_values if value is not None]
        for name, factor_values in factors.items()
    }
    scored: list[MarketHotspot] = []
    for index, row in enumerate(values):
        change = factors["change_pct"][index]
        if change is None:
            # A board without a change percentage cannot be ranked as today's hotspot.
            continue
        row_factors = {
            name: factor_values[index]
            for name, factor_values in factors.items()
            if factor_values[index] is not None
        }
        if not row_factors:
            continue
        weight_total = sum(WEIGHTS[name] for name in row_factors)
        score = sum(WEIGHTS[name] * percentile_rank(row_factors[name], available_values[name]) for name in row_factors)
        score = round(score / weight_total, 1)
        data_at = _row_value(row, "data_at")
        scored.append(
            MarketHotspot(
                board_code=str(_row_value(row, "board_code") or ""),
                board_name=str(_row_value(row, "board_name") or _row_value(row, "board_code") or ""),
                kind=kind,
                change_pct=change,
                turnover=_optional_float(_row_value(row, "turnover")),
                market_cap=_optional_float(_row_value(row, "market_cap")),
                rise_count=_optional_int(_row_value(row, "rise_count")),
                fall_count=_optional_int(_row_value(row, "fall_count")),
                flat_count=_optional_int(_row_value(row, "flat_count")),
                leader_code=_row_value(row, "leader_code"),
                leader_name=_row_value(row, "leader_name"),
                leader_change_pct=_optional_float(_row_value(row, "leader_change_pct")),
                hot_score=score,
                rank=1,
                data_at=data_at,
                trade_date=_trade_date(data_at),
            )
        )
    scored.sort(
        key=lambda item: (
            -item.hot_score,
            -(item.change_pct if item.change_pct is not None else -math.inf),
            -(item.turnover if item.turnover is not None else -math.inf),
            item.board_code,
        )
    )
    return [item.model_copy(update={"rank": index}) for index, item in enumerate(scored, start=1)], warnings


def classify_trend(
    board_code: str,
    hot_score: float,
    rank: int,
    history: list[Mapping[str, Any]],
) -> tuple[str, int, int | None]:
    """Classify a board against newest-first daily snapshots."""

    if not history:
        return "insufficient_history", 0, None
    previous = _snapshot_item(history[0], board_code)
    if previous is None:
        return "new", 1, None
    previous_score = _optional_float(previous.get("hot_score"))
    previous_rank = _optional_int(previous.get("rank"))
    if previous_score is None:
        return "insufficient_history", 0, None
    score_delta = hot_score - previous_score
    streak = 2 if score_delta != 0 else 1
    for older in history[1:]:
        item = _snapshot_item(older, board_code)
        if item is None:
            break
        older_score = _optional_float(item.get("hot_score"))
        if older_score is None:
            break
        if score_delta > 0 and previous_score > older_score:
            streak += 1
            previous_score = older_score
        elif score_delta < 0 and previous_score < older_score:
            streak += 1
            previous_score = older_score
        else:
            break
    status = "heating" if score_delta > 0 and streak >= 2 else "cooling" if score_delta < 0 and streak >= 2 else "steady"
    rank_change = previous_rank - rank if previous_rank is not None else None
    return status, streak, rank_change


def _snapshot_item(snapshot: Mapping[str, Any], board_code: str) -> dict[str, Any] | None:
    values = snapshot.get("items", snapshot.get("payload", []))
    if isinstance(values, Mapping):
        values = values.get("items", [])
    if not isinstance(values, list):
        return None
    for item in values:
        if isinstance(item, Mapping) and str(item.get("board_code")) == str(board_code):
            return dict(item)
    return None


def _optional_float(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _optional_int(value: Any) -> int | None:
    number = _optional_float(value)
    return int(number) if number is not None else None


def _cloud_weight(row: Any) -> float:
    market_cap = _optional_float(_row_value(row, "market_cap"))
    if market_cap is not None and market_cap > 0:
        return market_cap
    turnover = _optional_float(_row_value(row, "turnover"))
    if turnover is not None and turnover > 0:
        return turnover
    return 1.0


def _trade_date(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(ZoneInfo("Asia/Shanghai"))
        return value.date().isoformat()
    return str(value)[:10]


def _payload_data_at(payload: Any) -> datetime | None:
    values = payload if isinstance(payload, list) else [payload]
    for item in values:
        candidate = item.get("data_at") if isinstance(item, Mapping) else getattr(item, "data_at", None)
        if candidate is None:
            continue
        if isinstance(candidate, datetime):
            return candidate
        try:
            return datetime.fromisoformat(str(candidate).replace("Z", "+00:00"))
        except ValueError:
            continue
    return None


def _normalise_trade_date(value: Any) -> str | None:
    """Normalise snapshot dates so legacy YYYYMMDD rows compare safely."""

    text = _trade_date(value)
    if text is None:
        return None
    compact = text.replace("-", "")
    if len(compact) == 8 and compact.isdigit():
        return f"{compact[:4]}-{compact[4:6]}-{compact[6:]}"
    return text


def _compatible_history(history: list[dict[str, Any]], current_trade_date: str | None) -> tuple[list[dict[str, Any]], bool]:
    """Drop same-day snapshots and reject a future baseline.

    A live request can run after today's snapshot.  Comparing it with that
    same-day row would report a false trend, while a future-dated snapshot is
    an incompatible baseline and must be surfaced instead of guessed through.
    """

    if not current_trade_date:
        return history, False
    current = _normalise_trade_date(current_trade_date)
    normalised = [(_normalise_trade_date(item.get("trade_date")), item) for item in history]
    if any(date and current and date > current for date, _ in normalised):
        return [], True
    filtered = [item for date, item in normalised if date != current]
    return filtered, False


def _records(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item.model_dump(mode="json") if hasattr(item, "model_dump") else dict(item) for item in value]
    if hasattr(value, "model_dump"):
        return [value.model_dump(mode="json")]
    return []


def _meta(
    result: DataResult | None,
    *,
    capability: str,
    provider: str,
    trade_date: str | None,
    warnings: list[str] | None = None,
    data_at: datetime | None = None,
    fetched_at: datetime | None = None,
) -> MarketDatasetMeta:
    if result is not None:
        freshness = result.freshness.value if isinstance(result.freshness, Freshness) else str(result.freshness)
        return MarketDatasetMeta(
            capability=result.capability.value,
            provider=result.provider,
            data_at=result.data_at,
            fetched_at=result.fetched_at,
            freshness=freshness if freshness in {"fresh", "stale"} else "fresh",
            fallback_used=result.fallback_used,
            warnings=list(result.warnings) + list(warnings or []),
            trade_date=trade_date or _trade_date(result.data_at),
            source="datahub",
        )
    return MarketDatasetMeta(
        capability=capability,
        provider=provider,
        data_at=data_at,
        fetched_at=fetched_at or datetime.now(timezone.utc),
        freshness="stale",
        fallback_used=True,
        warnings=list(warnings or []),
        trade_date=trade_date,
        source="datahub",
    )


class HotspotService:
    def __init__(self, db: Any, *, snapshot_store: SnapshotStore | None = None) -> None:
        self.db = db
        self.snapshots = snapshot_store or SnapshotStore(db)

    def _history(self, kind: str) -> list[dict[str, Any]]:
        rows = self.snapshots.history(DATASET_HOTSPOTS, kind, limit=6, schema_version=SCHEMA_VERSION, source="datahub")
        return [{"trade_date": row.trade_date, "items": row.payload_json} for row in rows]

    async def _quotes(self, kind: str) -> tuple[list[BoardQuote], DataResult | None, str | None, list[dict[str, Any]], Any | None]:
        try:
            result = await get_market_board_quotes(kind)
            return list(result.data or []), result, _trade_date(result.data_at), self._history(kind), None
        except DataHubError as error:
            snapshot = self.snapshots.latest(DATASET_HOTSPOTS, kind, schema_version=SCHEMA_VERSION, source="datahub")
            if snapshot is None:
                raise DataHubError(DataHubErrorCode.INTERNAL, "市场热点数据暂不可用，请稍后重试") from error
            payload = snapshot.payload_json if isinstance(snapshot.payload_json, list) else []
            rows = [BoardQuote.model_validate(item) for item in payload]
            return rows, None, snapshot.trade_date, self._history(kind), snapshot

    async def get_hotspots(self, kind: str, limit: int = 12) -> HotspotDataset:
        rows, result, trade_date, history, snapshot = await self._quotes(kind)
        items, warnings = calculate_hotspots(rows, kind=kind)
        history, incompatible_history = _compatible_history(history, trade_date)
        if incompatible_history:
            warnings.append("历史基准暂不可比")
        if result is None:
            # A snapshot payload may already contain product fields; score again to
            # ensure old schema rows cannot alter the current ordering.
            meta = _meta(
                None,
                capability="market.board_quotes",
                provider="历史快照",
                trade_date=trade_date,
                warnings=warnings,
                data_at=_payload_data_at(snapshot.payload_json if snapshot is not None else None),
                fetched_at=getattr(snapshot, "fetched_at", None),
            )
        else:
            meta = _meta(result, capability="market.board_quotes", provider=result.provider, trade_date=trade_date, warnings=warnings)
        for item in items:
            status, streak, rank_change = classify_trend(item.board_code, item.hot_score, item.rank, history)
            items[item.rank - 1] = item.model_copy(update={"trend_status": status, "streak_days": streak, "rank_change": rank_change})
        return HotspotDataset(kind=kind, items=items[: max(1, min(int(limit), 12))], meta=meta)

    async def get_market_cloud(self, kind: str, limit: int = 80) -> MarketCloudDataset:
        rows, result, trade_date, _, snapshot = await self._quotes(kind)
        filtered = list(rows)
        if kind == "theme":
            filtered.sort(
                key=lambda row: (
                    -_cloud_weight(row),
                    str(_row_value(row, "board_code") or ""),
                )
            )
            filtered = filtered[: max(1, min(int(limit), 80))]
        nodes = []
        ordered = filtered if kind == "theme" else sorted(filtered, key=lambda item: str(_row_value(item, "board_code") or ""))
        for row in ordered:
            cap = _optional_float(_row_value(row, "market_cap"))
            nodes.append(
                MarketCloudNode(
                    code=str(_row_value(row, "board_code") or ""),
                    name=str(_row_value(row, "board_name") or ""),
                    kind=kind,
                    value=_cloud_weight(row),
                    change_pct=_optional_float(_row_value(row, "change_pct")),
                    market_cap=cap,
                    data_at=_row_value(row, "data_at"),
                    trade_date=trade_date,
                )
            )
        return MarketCloudDataset(
            kind=kind,
            nodes=nodes,
            meta=_meta(
                result,
                capability="market.board_quotes",
                provider="历史快照" if result is None else result.provider,
                trade_date=trade_date,
                data_at=_payload_data_at(snapshot.payload_json if snapshot is not None else None),
                fetched_at=getattr(snapshot, "fetched_at", None),
            ),
        )

    async def get_constituents(self, kind: str, board_code: str, limit: int = 20) -> ConstituentsDataset:
        try:
            result = await get_market_board_constituents(kind, board_code, limit)
            rows = list(result.data or [])
            trade_date = _trade_date(result.data_at)
            items = [self._stock(row, index, trade_date) for index, row in enumerate(rows, start=1)]
            return ConstituentsDataset(kind=kind, board_code=board_code, board_name=None, items=items, meta=_meta(result, capability="market.board_constituents", provider=result.provider, trade_date=trade_date))
        except DataHubError as error:
            scope = f"{kind}:{board_code}"
            snapshot = self.snapshots.latest(DATASET_CONSTITUENTS, scope, schema_version=SCHEMA_VERSION, source="datahub")
            if snapshot is None:
                raise DataHubError(DataHubErrorCode.INTERNAL, "代表个股数据暂不可用，请稍后重试") from error
            payload = snapshot.payload_json if isinstance(snapshot.payload_json, list) else []
            items = [self._stock(BoardConstituent.model_validate(item), index, snapshot.trade_date) for index, item in enumerate(payload, start=1)]
            meta = _meta(
                None,
                capability="market.board_constituents",
                provider="历史快照",
                trade_date=snapshot.trade_date,
                data_at=_payload_data_at(payload),
                fetched_at=getattr(snapshot, "fetched_at", None),
            )
            return ConstituentsDataset(kind=kind, board_code=board_code, items=items, meta=meta)

    @staticmethod
    def _stock(row: BoardConstituent, index: int, trade_date: str | None) -> RepresentativeStock:
        return RepresentativeStock(
            code=row.code,
            name=row.name,
            price=row.price,
            change_pct=row.change_pct,
            turnover=row.turnover,
            market_cap=row.market_cap,
            rank=index,
            data_at=row.data_at,
            trade_date=trade_date or _trade_date(row.data_at),
        )

    async def capture_daily_snapshot(self) -> dict[str, Any]:
        """Capture both board categories and their representative stocks."""

        summary: dict[str, Any] = {"categories": {}, "constituents_saved": 0, "constituents_failed": []}
        for kind in ("industry", "theme"):
            try:
                result = await get_market_board_quotes(kind)
                scored, warnings = calculate_hotspots(result.data or [], kind=kind)
                trade_date = _trade_date(result.data_at)
                if trade_date is None:
                    raise DataHubError(DataHubErrorCode.STALE_INVALID, "板块数据缺少交易日")
                # Store product rows (including hot_score/rank) so the next
                # day's trend comparison can run without re-fetching history.
                self.snapshots.upsert(DATASET_HOTSPOTS, trade_date, kind, SCHEMA_VERSION, "datahub", _records(scored))
                saved = 0
                failed: list[str] = []
                for hotspot in scored[:12]:
                    try:
                        constituents = await get_market_board_constituents(kind, hotspot.board_code, 20)
                        self.snapshots.upsert(DATASET_CONSTITUENTS, trade_date, f"{kind}:{hotspot.board_code}", SCHEMA_VERSION, "datahub", _records(constituents.data))
                        saved += 1
                    except DataHubError:
                        failed.append(hotspot.board_code)
                summary["categories"][kind] = {"status": "success", "rows": len(scored), "constituents_saved": saved, "constituents_failed": failed, "warnings": warnings, "trade_date": trade_date}
                summary["constituents_saved"] += saved
                summary["constituents_failed"].extend(f"{kind}:{code}" for code in failed)
            except DataHubError as error:
                summary["categories"][kind] = {"status": "failed", "error": error.message}
        successful_dates = [
            value.get("trade_date")
            for value in summary["categories"].values()
            if value.get("status") == "success" and value.get("trade_date")
        ]
        all_failed = all(value.get("status") == "failed" for value in summary["categories"].values())
        run_date = max(successful_dates) if successful_dates else datetime.now(timezone.utc).date().isoformat()
        if hasattr(self.snapshots, "record_run"):
            self.snapshots.record_run(
                DATASET_HOTSPOTS,
                run_date,
                status="failed" if all_failed else "success",
                counts=summary,
                error="热点快照采集失败，请稍后重试" if all_failed else None,
            )
        self.snapshots.cleanup(retention_days=730)
        if all_failed:
            raise DataHubError(DataHubErrorCode.INTERNAL, "热点快照采集失败，请稍后重试")
        return summary


__all__ = ["HotspotService", "calculate_hotspots", "classify_trend", "percentile_rank"]
