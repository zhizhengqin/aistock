from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.datahub.contracts import (
    AuctionOpen,
    Capability,
    DataQuality,
    DataResult,
    KplLimitItem,
    KplStrongSector,
)
from app.datahub.router import DataHubRouter, RouteDefinition
from app.datahub.consumer import get_optional_kpl
from app.datahub.platform import set_datahub_router
from app.services.main_force_orchestrator import run_main_force_selection
from app.services.sector_orchestrator import run_sector_analysis
from app.services.dragon_tiger_orchestrator import run_dragon_tiger_analysis
from app.services.monitor_engine import _fetch_optional_auction
from tests.services.test_analysis_llm_contracts import FakeContext, StructuredLLM, _result
from tests.services.test_remaining_llm_contracts import _Context as RemainingContext, _TypedLlm


_DATA_AT = datetime(2026, 8, 21, 7, 0, tzinfo=timezone.utc)


def _kpl_result(capability, data):
    return DataResult(
        data=data,
        capability=capability,
        provider="tushare",
        data_at=_DATA_AT,
        quality=DataQuality(valid=True, rows=len(data)),
    )


@pytest.mark.asyncio
async def test_optional_kpl_does_not_call_disabled_provider():
    calls = []

    class Provider:
        async def fetch(self, capability, params):
            calls.append((capability, params))
            raise AssertionError("disabled KPL provider must not be called")

    router = DataHubRouter(
        {"tushare": Provider()},
        {Capability.KPL_LIMIT_LIST: RouteDefinition(providers=["tushare"])},
        provider_states={"tushare": False},
    )
    previous = None
    try:
        from app.datahub import platform

        previous = platform.get_datahub_router()
        set_datahub_router(router)
        assert await get_optional_kpl(Capability.KPL_LIMIT_LIST, {"trade_date": "20260821"}) is None
        assert calls == []
    finally:
        set_datahub_router(previous)


@pytest.mark.asyncio
async def test_main_force_consumes_enabled_kpl_context_without_making_it_critical():
    context = FakeContext(StructuredLLM())
    candidate = {"code": "600519.SS", "name": "贵州茅台", "net_main_flow": 1e8, "change_pct": 1, "net_main_pct": 1}
    kpl = _kpl_result(
        Capability.KPL_STRONG_SECTORS,
        [KplStrongSector(ts_code="BK0475", name="半导体", trade_date="20260821")],
    )
    with patch("app.services.main_force_orchestrator.get_market_capital_flow_rank", return_value=_result(Capability.MARKET_FUND_FLOW_RANK, [])), \
        patch("app.services.main_force_orchestrator._enrich_candidate", return_value={**candidate, "market_cap": 100, "change_pct_20d": 1, "net_main_flow_60d": 1, "shareholder": {"change_pct": -1}}), \
        patch("app.services.main_force_orchestrator.get_optional_kpl", new=AsyncMock(return_value=kpl)) as optional:
        report = await run_main_force_selection(1, context, None)
    optional.assert_awaited_once_with(Capability.KPL_STRONG_SECTORS, {})
    assert report["kpl"]["strong_sectors"][0]["name"] == "半导体"
    assert "半导体" in context.llm.calls[0]["messages"][0]["content"]


@pytest.mark.asyncio
async def test_sector_consumes_enabled_kpl_context_without_making_it_critical():
    context = FakeContext(StructuredLLM())
    kpl = _kpl_result(
        Capability.KPL_STRONG_SECTORS,
        [KplStrongSector(ts_code="BK0475", name="半导体", trade_date="20260821")],
    )
    with patch("app.services.sector_orchestrator.get_market_indices", return_value=_result(Capability.MARKET_INDICES, [])), \
        patch("app.services.sector_orchestrator.get_sw_sector_list", return_value=_result(Capability.SECTOR_REALTIME, [])), \
        patch("app.services.sector_orchestrator.get_sector_capital_flow", return_value=_result(Capability.SECTOR_FUND_FLOW, [])), \
        patch("app.services.sector_orchestrator.get_optional_kpl", new=AsyncMock(return_value=kpl)) as optional:
        report = await run_sector_analysis(1, context, None)
    optional.assert_awaited_once_with(Capability.KPL_STRONG_SECTORS, {})
    assert report["kpl"]["strong_sectors"][0]["name"] == "半导体"
    assert "半导体" in context.llm.calls[0]["messages"][0]["content"]


@pytest.mark.asyncio
async def test_dragon_tiger_consumes_enabled_kpl_limit_context_without_making_it_critical():
    context = RemainingContext(_TypedLlm())
    kpl = _kpl_result(
        Capability.KPL_LIMIT_LIST,
        [KplLimitItem(ts_code="600519.SS", name="贵州茅台", trade_date="20260821")],
    )
    with patch("app.services.dragon_tiger_orchestrator.get_dragon_tiger_list", return_value=_result(Capability.DRAGON_TIGER_LIST, [])), \
        patch("app.services.dragon_tiger_orchestrator.get_dragon_tiger_institution", return_value=_result(Capability.DRAGON_TIGER_SEATS, [])), \
        patch("app.services.dragon_tiger_orchestrator.get_optional_kpl", new=AsyncMock(return_value=kpl)) as optional:
        report = await run_dragon_tiger_analysis(5, 1, context, None)
    optional.assert_awaited_once_with(Capability.KPL_LIMIT_LIST, {})
    assert report["kpl"]["limit_list"][0]["name"] == "贵州茅台"
    assert "贵州茅台" in context.llm.calls[-1]["messages"][0]["content"]


@pytest.mark.asyncio
async def test_monitor_consumes_enabled_kpl_auction_context():
    auction = _kpl_result(
        Capability.MARKET_AUCTION_OPEN,
        [AuctionOpen(ts_code="600519.SS", trade_date="20260821", open=1701.0)],
    )
    with patch("app.services.monitor_engine.get_optional_kpl", new=AsyncMock(return_value=auction)) as optional:
        result = await _fetch_optional_auction("600519.SS")
    optional.assert_awaited_once_with(Capability.MARKET_AUCTION_OPEN, {"ts_code": "600519.SS"})
    assert result.data[0].open == 1701.0
