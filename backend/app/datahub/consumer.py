"""Typed asynchronous business-consumer boundary for the DataHub.

Every consumer calls the capability router and returns the complete
``DataResult``.  Provider SDKs are hidden behind the router; no business
module may import the legacy AkShare compatibility client.
"""

from __future__ import annotations

from typing import Any
import re

import pandas as pd

from app.datahub.contracts import Capability, DataResult
from app.datahub.errors import DataHubError, DataHubErrorCode
from app.datahub.platform import get_datahub_router
from app.datahub.providers.base import capability_probe_params


DEFAULT_INDEX_CODES = ["000001.SS", "399001.SZ", "399006.SZ", "000300.SS", "000688.SS"]


async def _fetch(capability: Capability, params: dict[str, Any]) -> DataResult:
    return await get_datahub_router().fetch(capability, params)


async def get_market_indices(*, codes: list[str] | None = None) -> DataResult:
    return await _fetch(Capability.MARKET_INDICES, {"codes": list(codes or DEFAULT_INDEX_CODES)})


def _validate_board_kind(kind: str) -> str:
    if kind not in {"industry", "theme"}:
        raise DataHubError(DataHubErrorCode.VALIDATION, "板块类型必须是 industry 或 theme")
    return kind


def _validate_board_code(board_code: str) -> str:
    value = str(board_code or "").upper()
    if not re.fullmatch(r"BK\d{3,6}", value):
        raise DataHubError(DataHubErrorCode.VALIDATION, "板块代码格式无效")
    return value


async def get_market_board_quotes(kind: str) -> DataResult:
    """Fetch the complete raw board cross-section for one category."""

    return await _fetch(Capability.MARKET_BOARD_QUOTES, {"kind": _validate_board_kind(kind)})


async def get_market_board_constituents(kind: str, board_code: str, limit: int = 20) -> DataResult:
    """Fetch up to ``limit`` constituents for one validated board."""

    if limit < 1 or limit > 20:
        raise DataHubError(DataHubErrorCode.VALIDATION, "代表个股数量必须在 1 到 20 之间")
    return await _fetch(
        Capability.MARKET_BOARD_CONSTITUENTS,
        {"kind": _validate_board_kind(kind), "board_code": _validate_board_code(board_code), "limit": limit},
    )


async def get_stock_info(code: str) -> DataResult:
    return await _fetch(Capability.STOCK_SNAPSHOT, {"code": code})


async def get_stock_kline(code: str, days: int = 120) -> DataResult:
    return await _fetch(Capability.STOCK_KLINE_DAILY, {"code": code, "days": days})


async def get_stock_financial_summary(code: str) -> DataResult:
    return await _fetch(Capability.STOCK_FINANCIALS, {"code": code})


async def get_stock_capital_flow(code: str, days: int = 20) -> DataResult:
    return await _fetch(Capability.STOCK_FUND_FLOW, {"code": code, "days": days})


async def get_stock_news_titles(code: str, limit: int = 10) -> DataResult:
    return await _fetch(Capability.STOCK_NEWS, {"code": code, "limit": limit})


async def get_global_news(source: str, limit: int = 30) -> DataResult:
    return await _fetch(Capability.STOCK_NEWS, {"source": source, "limit": limit})


async def get_market_capital_flow_rank(limit: int = 50) -> DataResult:
    return await _fetch(Capability.MARKET_FUND_FLOW_RANK, {"limit": limit})


async def get_stock_shareholder_count(code: str) -> DataResult:
    return await _fetch(Capability.STOCK_SHAREHOLDERS, {"code": code})


async def get_sw_sector_list() -> DataResult:
    return await _fetch(Capability.SECTOR_REALTIME, {})


async def get_sw_sector_detail(code: str, days: int = 20) -> DataResult:
    return await _fetch(Capability.SECTOR_KLINE, {"code": code, "days": days})


async def get_sector_capital_flow() -> DataResult:
    return await _fetch(Capability.SECTOR_FUND_FLOW, {})


async def get_dragon_tiger_list(days: int = 5) -> DataResult:
    return await _fetch(Capability.DRAGON_TIGER_LIST, {"days": days})


async def get_dragon_tiger_institution(code: str | None = None) -> DataResult:
    return await _fetch(Capability.DRAGON_TIGER_SEATS, {"code": code} if code else {})


async def get_optional_kpl(capability: Capability | str, params: dict[str, Any] | None = None) -> DataResult | None:
    """Fetch an opt-in KPL capability when its configured route is enabled.

    KPL is an optional enrichment for business workflows.  The default
    registry keeps Tushare disabled, so the fast path returns without touching
    an upstream client.  Explicitly enabled routes use the same router,
    cache, limits and credential handling as every other capability.  A
    missing credential/permission is a normal optional miss; structural and
    transient failures are left to the caller so it can surface a warning.
    """

    capability = Capability(capability)
    router = get_datahub_router()
    await router.refresh_routes()
    route = router.routes.get(capability)
    if route is None:
        return None
    if router.provider_states and not any(router.provider_states.get(name, False) for name in route.providers):
        return None
    request = dict(capability_probe_params(capability))
    request.update(params or {})
    try:
        return await router.fetch(capability, request)
    except DataHubError as exc:
        if exc.code in {
            DataHubErrorCode.NOT_CONFIGURED,
            DataHubErrorCode.AUTHENTICATION_FAILED,
            DataHubErrorCode.PERMISSION_DENIED,
            DataHubErrorCode.UNSUPPORTED,
        }:
            return None
        raise


def records(result: DataResult) -> list[dict[str, Any]]:
    """Convert typed rows at an explicit domain boundary.

    This helper is intentionally narrow: it does not unwrap arbitrary legacy
    values and therefore cannot hide DataHub metadata or errors.
    """

    value = result.data
    if isinstance(value, list):
        return [row.model_dump(mode="json") if hasattr(row, "model_dump") else dict(row) for row in value]
    if hasattr(value, "model_dump"):
        return [value.model_dump(mode="json")]
    if isinstance(value, dict):
        return [dict(value)]
    return []


def kline_dataframe(result: DataResult) -> pd.DataFrame:
    return pd.DataFrame(records(result))


__all__ = [name for name in globals() if name.startswith("get_")] + ["DEFAULT_INDEX_CODES", "kline_dataframe", "records"]
