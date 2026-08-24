"""Capability-aware payload quality checks."""

from __future__ import annotations

import math
from datetime import date, datetime
from collections.abc import Iterable, Mapping
from typing import Any

from pydantic import BaseModel

from app.datahub.contracts import Capability
from app.datahub.errors import DataHubError, DataHubErrorCode


def _as_mapping(value: Any) -> Mapping[str, Any] | None:
    """Normalize one provider row at the consumer boundary.

    Pydantic models are iterable over ``(field, value)`` pairs, which is not
    the row protocol used by the validators.  Convert them before any caller
    is allowed to use mapping methods such as ``get``.
    """
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="python")
    return value if isinstance(value, Mapping) else None


def _iter_rows(payload: Any) -> list[Mapping[str, Any]]:
    if payload is None:
        return []
    # Pydantic models implement ``__iter__`` over field tuples.  Treat a
    # singleton model as one typed row before considering generic iterables;
    # otherwise a valid snapshot/financial/flow model is mistaken for an
    # empty payload.
    if isinstance(payload, BaseModel):
        row = _as_mapping(payload)
        return [row] if row is not None else []
    if isinstance(payload, Mapping):
        # Some single-row providers return a dictionary.  A nested list is
        # treated as rows when the conventional ``data`` key is present.
        nested = payload.get("data")
        if isinstance(nested, list):
            return [row for item in nested if (row := _as_mapping(item)) is not None]
        return [payload]
    if isinstance(payload, Iterable) and not isinstance(payload, (str, bytes)):
        return [row for item in payload if (row := _as_mapping(item)) is not None]
    return []


def validate_payload(capability: Capability | str, payload: Any) -> int:
    """Validate shape, required fields and finite/non-zero market values.

    Returns the number of usable rows.  It intentionally does not reject a
    legitimate zero percentage change; only price/quote fields are checked
    for the all-zero failure mode that used to be cached as success.
    """

    capability = Capability(capability)
    rows = _iter_rows(payload)
    if not rows:
        raise DataHubError(DataHubErrorCode.EMPTY_INVALID, "数据源返回空数据")

    required: dict[Capability, tuple[str, ...]] = {
        Capability.MARKET_INDICES: ("code", "name", "price"),
        Capability.MARKET_BOARD_QUOTES: ("board_code", "board_name", "kind", "data_at"),
        Capability.MARKET_BOARD_CONSTITUENTS: ("code", "name", "data_at"),
        Capability.STOCK_SNAPSHOT: ("code", "name", "price"),
        Capability.STOCK_PROFILE: ("code", "name"),
        Capability.STOCK_KLINE_DAILY: ("date", "open", "close", "high", "low"),
        Capability.KPL_LIMIT_LIST: ("trade_date",),
        Capability.KPL_CONCEPTS: ("ts_code", "name", "trade_date"),
        Capability.KPL_CONCEPT_CONSTITUENTS: ("ts_code", "con_code", "trade_date"),
        Capability.KPL_LIMIT_LADDER: ("ts_code", "trade_date", "nums"),
        Capability.KPL_STRONG_SECTORS: ("ts_code", "name", "trade_date"),
        Capability.MARKET_AUCTION_OPEN: ("ts_code", "trade_date", "open"),
    }
    fields = required.get(capability, ())
    normalised_rows: list[Mapping[str, Any]] = []
    for row in rows:
        normalised_rows.append(row)
        missing = [field for field in fields if row.get(field) in (None, "")]
        if missing:
            raise DataHubError(
                DataHubErrorCode.SCHEMA_CHANGED,
                "数据源字段发生变化",
                provider_detail={"missing": missing},
            )
        for key, value in row.items():
            if isinstance(value, float) and not math.isfinite(value):
                raise DataHubError(DataHubErrorCode.EMPTY_INVALID, "数据源返回了无效数值")
            if key in {"price", "open", "close", "high", "low", "volume", "amount"} and value not in (None, ""):
                try:
                    if float(value) < 0:
                        raise DataHubError(DataHubErrorCode.EMPTY_INVALID, "数据源返回了越界数值")
                except (TypeError, ValueError):
                    raise DataHubError(DataHubErrorCode.SCHEMA_CHANGED, "数据源数值字段无法解析") from None
            if key in {"change_pct", "pct_chg", "net_main_pct"} and value not in (None, ""):
                try:
                    if abs(float(value)) > 1000:
                        raise DataHubError(DataHubErrorCode.EMPTY_INVALID, "数据源涨跌幅超出合理范围")
                except (TypeError, ValueError):
                    raise DataHubError(DataHubErrorCode.SCHEMA_CHANGED, "数据源涨跌幅字段无法解析") from None

        if capability in {Capability.STOCK_KLINE_DAILY, Capability.SECTOR_KLINE}:
            try:
                high = float(row.get("high"))
                low = float(row.get("low"))
                open_price = float(row.get("open"))
                close = float(row.get("close"))
                if high < max(open_price, close) or low > min(open_price, close) or high < low:
                    raise DataHubError(DataHubErrorCode.EMPTY_INVALID, "K 线高低价关系不成立")
            except (TypeError, ValueError):
                raise DataHubError(DataHubErrorCode.SCHEMA_CHANGED, "K 线价格字段无法解析") from None

        if "data_at" in row and row.get("data_at") is not None:
            timestamp = row.get("data_at")
            if not isinstance(timestamp, (datetime, date)):
                try:
                    datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
                except ValueError:
                    raise DataHubError(DataHubErrorCode.SCHEMA_CHANGED, "数据时间字段无法解析") from None

    if capability in {Capability.MARKET_INDICES, Capability.STOCK_SNAPSHOT}:
        prices = [float(row.get("price", 0) or 0) for row in normalised_rows]
        if prices and all(price == 0 for price in prices):
            raise DataHubError(DataHubErrorCode.EMPTY_INVALID, "数据源返回全零行情")
    return len(rows)


def validate_cross_source(capability: Capability | str, results: Iterable[Any], *, tolerance: float = 0.05) -> None:
    """Reject materially divergent overlapping quotes from two sources."""

    values: list[float] = []
    for result in results:
        payload = getattr(result, "data", result)
        for row in _iter_rows(payload):
            value = row.get("price")
            if value not in (None, ""):
                try:
                    values.append(float(value))
                except (TypeError, ValueError):
                    continue
    if len(values) >= 2:
        baseline = max(abs(values[0]), 1e-9)
        if abs(values[0] - values[1]) / baseline > tolerance:
            raise DataHubError(DataHubErrorCode.EMPTY_INVALID, "多数据源行情偏差超过质量阈值")


__all__ = ["validate_cross_source", "validate_payload"]
