"""Regression tests for complete, isolated stock-analysis data collection."""

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.datahub.contracts import (
    Capability,
    FinancialSummary,
    FundFlow,
    KlineBar,
    NewsItem,
    StockProfile,
    StockSnapshot,
    DataResult,
    DataQuality,
)
from app.datahub.errors import DataHubError
from app.services import analysis_orchestrator
from tests.test_orchestrator import _Context, _kline_result, _result


_DATA_AT = datetime(2026, 8, 21, 7, tzinfo=timezone.utc)


def _profile_result(industry: str | None):
    return _result(
        Capability.STOCK_PROFILE,
        StockProfile(code="600000.SS", name="浦发银行", industry=industry, data_at=_DATA_AT),
    )


@pytest.mark.asyncio
async def test_analysis_merges_snapshot_valuation_and_independent_profile_before_financial_fallback():
    snapshot = _result(
        Capability.STOCK_SNAPSHOT,
        StockSnapshot(
            code="600000.SS",
            name="行情名称",
            price=12.34,
            change_pct=1.2,
            pe_ttm=18.5,
            pb=1.4,
            market_cap=1234.56,
            industry="",
            data_at=_DATA_AT,
        ),
    )
    financial = _result(
        Capability.STOCK_FINANCIALS,
        FinancialSummary(code="600000.SS", pe_ttm=99.0, pb=9.0, market_cap=9999.0, data_at=_DATA_AT),
    )
    flow = _result(Capability.STOCK_FUND_FLOW, FundFlow(code="600000.SS", data_at=_DATA_AT))
    news = _result(Capability.STOCK_NEWS, [NewsItem(title="测试", date=_DATA_AT)])

    with patch.object(analysis_orchestrator, "get_stock_info", new=AsyncMock(return_value=snapshot)), \
        patch.object(analysis_orchestrator, "get_stock_profile", new=AsyncMock(return_value=_profile_result("银行")), create=True) as profile, \
        patch.object(analysis_orchestrator, "get_stock_kline", new=AsyncMock(return_value=_kline_result())), \
        patch.object(analysis_orchestrator, "get_stock_financial_summary", new=AsyncMock(return_value=financial)), \
        patch.object(analysis_orchestrator, "get_stock_capital_flow", new=AsyncMock(return_value=flow)), \
        patch.object(analysis_orchestrator, "get_stock_news_titles", new=AsyncMock(return_value=news)):
        report = await analysis_orchestrator.run_full_analysis("600000", 1, _Context(), None)

    profile.assert_awaited_once_with("600000")
    assert report["stock_name"] == "浦发银行"
    assert report["stock_info"]["industry"] == "银行"
    assert report["stock_info"]["pe_ttm"] == 18.5
    assert report["stock_info"]["pb"] == 1.4
    assert report["stock_info"]["market_cap"] == 1234.56


@pytest.mark.asyncio
async def test_analysis_collects_independent_inputs_concurrently():
    active = 0
    maximum = 0

    async def delayed(value):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        await asyncio.sleep(0.02)
        active -= 1
        return value

    snapshot = _result(Capability.STOCK_SNAPSHOT, StockSnapshot(code="600000.SS", name="浦发银行", price=12, data_at=_DATA_AT))
    profile = _profile_result("银行")
    financial = _result(Capability.STOCK_FINANCIALS, FinancialSummary(code="600000.SS", data_at=_DATA_AT))
    flow = _result(Capability.STOCK_FUND_FLOW, FundFlow(code="600000.SS", data_at=_DATA_AT))
    news = _result(Capability.STOCK_NEWS, [NewsItem(title="测试", date=_DATA_AT)])

    async def snapshot_call(*_args): return await delayed(snapshot)
    async def profile_call(*_args): return await delayed(profile)
    async def kline_call(*_args): return await delayed(_kline_result())
    async def financial_call(*_args): return await delayed(financial)
    async def flow_call(*_args): return await delayed(flow)
    async def news_call(*_args): return await delayed(news)

    with patch.object(analysis_orchestrator, "get_stock_info", new=snapshot_call), \
        patch.object(analysis_orchestrator, "get_stock_profile", new=profile_call, create=True), \
        patch.object(analysis_orchestrator, "get_stock_kline", new=kline_call), \
        patch.object(analysis_orchestrator, "get_stock_financial_summary", new=financial_call), \
        patch.object(analysis_orchestrator, "get_stock_capital_flow", new=flow_call), \
        patch.object(analysis_orchestrator, "get_stock_news_titles", new=news_call):
        await analysis_orchestrator.run_full_analysis("600000", 1, _Context(), None)

    assert maximum >= 5


@pytest.mark.asyncio
async def test_analysis_isolates_optional_profile_financial_flow_and_news_failures():
    unavailable = DataHubError("internal", "provider failed")
    snapshot = _result(Capability.STOCK_SNAPSHOT, StockSnapshot(code="600000.SS", name="浦发银行", price=12, industry="快照旧行业", data_at=_DATA_AT))
    with patch.object(analysis_orchestrator, "get_stock_info", new=AsyncMock(return_value=snapshot)), \
        patch.object(analysis_orchestrator, "get_stock_profile", new=AsyncMock(side_effect=unavailable), create=True), \
        patch.object(analysis_orchestrator, "get_stock_kline", new=AsyncMock(return_value=_kline_result())), \
        patch.object(analysis_orchestrator, "get_stock_financial_summary", new=AsyncMock(side_effect=unavailable)), \
        patch.object(analysis_orchestrator, "get_stock_capital_flow", new=AsyncMock(side_effect=unavailable)), \
        patch.object(analysis_orchestrator, "get_stock_news_titles", new=AsyncMock(side_effect=unavailable)):
        report = await analysis_orchestrator.run_full_analysis("600000", 1, _Context(), None)

    assert any("行业资料" in warning for warning in report["data_warnings"])
    assert any("财务数据" in warning for warning in report["data_warnings"])
    assert any("资金流数据" in warning for warning in report["data_warnings"])
    assert any("新闻数据" in warning for warning in report["data_warnings"])
    assert report["stock_info"]["industry"] is None


@pytest.mark.asyncio
async def test_analysis_reports_snapshot_and_profile_failures_independently():
    unavailable = DataHubError("internal", "provider failed")
    with patch.object(analysis_orchestrator, "get_stock_info", new=AsyncMock(side_effect=unavailable)), \
        patch.object(analysis_orchestrator, "get_stock_profile", new=AsyncMock(side_effect=unavailable), create=True), \
        patch.object(analysis_orchestrator, "get_stock_kline", new=AsyncMock(return_value=_kline_result())), \
        patch.object(analysis_orchestrator, "get_stock_financial_summary", new=AsyncMock(return_value=_result(Capability.STOCK_FINANCIALS, FinancialSummary(code="600000.SS", data_at=_DATA_AT)))), \
        patch.object(analysis_orchestrator, "get_stock_capital_flow", new=AsyncMock(return_value=_result(Capability.STOCK_FUND_FLOW, FundFlow(code="600000.SS", data_at=_DATA_AT)))), \
        patch.object(analysis_orchestrator, "get_stock_news_titles", new=AsyncMock(return_value=_result(Capability.STOCK_NEWS, [NewsItem(title="测试", date=_DATA_AT)]))):
        report = await analysis_orchestrator.run_full_analysis("600000", 1, _Context(), None)

    warnings = report["data_warnings"]
    assert any("实时行情数据暂不可用" in warning for warning in warnings)
    assert any("行业资料暂不可用" in warning for warning in warnings)


@pytest.mark.asyncio
async def test_analysis_propagates_unexpected_optional_runtime_error():
    snapshot = _result(Capability.STOCK_SNAPSHOT, StockSnapshot(code="600000.SS", name="浦发银行", price=12, data_at=_DATA_AT))
    financial = _result(Capability.STOCK_FINANCIALS, FinancialSummary(code="600000.SS", data_at=_DATA_AT))
    flow = _result(Capability.STOCK_FUND_FLOW, FundFlow(code="600000.SS", data_at=_DATA_AT))
    news = _result(Capability.STOCK_NEWS, [NewsItem(title="测试", date=_DATA_AT)])

    with patch.object(analysis_orchestrator, "get_stock_info", new=AsyncMock(return_value=snapshot)), \
        patch.object(analysis_orchestrator, "get_stock_profile", new=AsyncMock(side_effect=RuntimeError("程序错误")), create=True), \
        patch.object(analysis_orchestrator, "get_stock_kline", new=AsyncMock(return_value=_kline_result())), \
        patch.object(analysis_orchestrator, "get_stock_financial_summary", new=AsyncMock(return_value=financial)), \
        patch.object(analysis_orchestrator, "get_stock_capital_flow", new=AsyncMock(return_value=flow)), \
        patch.object(analysis_orchestrator, "get_stock_news_titles", new=AsyncMock(return_value=news)):
        with pytest.raises(RuntimeError, match="程序错误"):
            await analysis_orchestrator.run_full_analysis("600000", 1, _Context(), None)


@pytest.mark.asyncio
async def test_analysis_propagates_optional_cancellation():
    snapshot = _result(Capability.STOCK_SNAPSHOT, StockSnapshot(code="600000.SS", name="浦发银行", price=12, data_at=_DATA_AT))
    financial = _result(Capability.STOCK_FINANCIALS, FinancialSummary(code="600000.SS", data_at=_DATA_AT))
    flow = _result(Capability.STOCK_FUND_FLOW, FundFlow(code="600000.SS", data_at=_DATA_AT))
    news = _result(Capability.STOCK_NEWS, [NewsItem(title="测试", date=_DATA_AT)])

    with patch.object(analysis_orchestrator, "get_stock_info", new=AsyncMock(return_value=snapshot)), \
        patch.object(analysis_orchestrator, "get_stock_profile", new=AsyncMock(side_effect=asyncio.CancelledError()), create=True), \
        patch.object(analysis_orchestrator, "get_stock_kline", new=AsyncMock(return_value=_kline_result())), \
        patch.object(analysis_orchestrator, "get_stock_financial_summary", new=AsyncMock(return_value=financial)), \
        patch.object(analysis_orchestrator, "get_stock_capital_flow", new=AsyncMock(return_value=flow)), \
        patch.object(analysis_orchestrator, "get_stock_news_titles", new=AsyncMock(return_value=news)):
        with pytest.raises(asyncio.CancelledError):
            await analysis_orchestrator.run_full_analysis("600000", 1, _Context(), None)


@pytest.mark.asyncio
async def test_analysis_propagates_kline_runtime_error_as_critical_input_failure():
    snapshot = _result(Capability.STOCK_SNAPSHOT, StockSnapshot(code="600000.SS", name="浦发银行", price=12, data_at=_DATA_AT))
    profile = _profile_result("银行")
    financial = _result(Capability.STOCK_FINANCIALS, FinancialSummary(code="600000.SS", data_at=_DATA_AT))
    flow = _result(Capability.STOCK_FUND_FLOW, FundFlow(code="600000.SS", data_at=_DATA_AT))
    news = _result(Capability.STOCK_NEWS, [NewsItem(title="测试", date=_DATA_AT)])

    with patch.object(analysis_orchestrator, "get_stock_info", new=AsyncMock(return_value=snapshot)), \
        patch.object(analysis_orchestrator, "get_stock_profile", new=AsyncMock(return_value=profile), create=True), \
        patch.object(analysis_orchestrator, "get_stock_kline", new=AsyncMock(side_effect=RuntimeError("K线程序错误"))), \
        patch.object(analysis_orchestrator, "get_stock_financial_summary", new=AsyncMock(return_value=financial)), \
        patch.object(analysis_orchestrator, "get_stock_capital_flow", new=AsyncMock(return_value=flow)), \
        patch.object(analysis_orchestrator, "get_stock_news_titles", new=AsyncMock(return_value=news)):
        with pytest.raises(RuntimeError, match="K线程序错误"):
            await analysis_orchestrator.run_full_analysis("600000", 1, _Context(), None)
