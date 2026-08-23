from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.datahub.errors import DataHubError, DataHubErrorCode
from app.datahub.contracts import Capability, DataQuality, DataResult, StockSnapshot
from app.services.analysis_orchestrator import run_full_analysis
from app.services.dragon_tiger_orchestrator import run_dragon_tiger_analysis
from app.services.main_force_orchestrator import run_main_force_selection
from app.services.portfolio_orchestrator import run_portfolio_diagnosis
from app.services.risk_orchestrator import run_stock_risk_analysis
from app.services.sector_orchestrator import run_sector_analysis
from tests.services.test_analysis_llm_contracts import FakeContext, StructuredLLM
from tests.services.test_remaining_llm_contracts import _Context as RemainingContext, _TypedLlm


def _critical_error():
    return DataHubError(DataHubErrorCode.INTERNAL, "关键行情暂不可用")


async def _raise_critical(*_args, **_kwargs):
    raise _critical_error()


@pytest.mark.asyncio
async def test_stock_kline_critical_data_failure_makes_zero_llm_calls():
    context = FakeContext(StructuredLLM())
    data_at = datetime.now(timezone.utc)
    info = DataResult(
        data=StockSnapshot(
            code="600519.SS",
            name="贵州茅台",
            price=1685.5,
            change_pct=1.32,
            industry="白酒",
            data_at=data_at,
        ),
        capability=Capability.STOCK_SNAPSHOT,
        provider="fixture",
        data_at=data_at,
        quality=DataQuality(valid=True, rows=1),
    )
    with patch("app.services.analysis_orchestrator.get_stock_info", new=AsyncMock(return_value=info)), \
        patch("app.services.analysis_orchestrator.get_stock_kline", new=AsyncMock(side_effect=_raise_critical)):
        with pytest.raises(DataHubError):
            await run_full_analysis("600519", 1, context, None)
    assert context.llm.calls == []


@pytest.mark.asyncio
async def test_main_force_critical_data_failure_makes_zero_llm_calls():
    context = FakeContext(StructuredLLM())
    with patch("app.services.main_force_orchestrator.get_market_capital_flow_rank", new=AsyncMock(side_effect=_raise_critical)):
        with pytest.raises(DataHubError):
            await run_main_force_selection(1, context, None)
    assert context.llm.calls == []


@pytest.mark.asyncio
async def test_sector_critical_data_failure_makes_zero_llm_calls():
    context = FakeContext(StructuredLLM())
    with patch("app.services.sector_orchestrator.get_market_indices", new=AsyncMock(side_effect=_raise_critical)):
        with pytest.raises(DataHubError):
            await run_sector_analysis(1, context, None)
    assert context.llm.calls == []


@pytest.mark.asyncio
async def test_dragon_tiger_critical_data_failure_makes_zero_llm_calls():
    context = RemainingContext(_TypedLlm())
    with patch("app.services.dragon_tiger_orchestrator.get_dragon_tiger_list", new=AsyncMock(side_effect=_raise_critical)):
        with pytest.raises(DataHubError):
            await run_dragon_tiger_analysis(5, 1, context, None)
    assert context.llm.calls == []


@pytest.mark.asyncio
async def test_portfolio_critical_data_failure_makes_zero_llm_calls():
    context = RemainingContext(_TypedLlm())
    with patch("app.services.portfolio_orchestrator.get_stock_info", new=AsyncMock(side_effect=_raise_critical)):
        with pytest.raises(DataHubError):
            await run_portfolio_diagnosis(
                [{"stock_code": "600519", "stock_name": "贵州茅台", "shares": 100, "cost_price": 100}],
                1,
                context,
                None,
            )
    assert context.llm.calls == []


@pytest.mark.asyncio
async def test_risk_critical_data_failure_makes_zero_llm_calls():
    context = RemainingContext(_TypedLlm())
    with patch("app.services.risk_orchestrator.get_stock_info", new=AsyncMock(side_effect=_raise_critical)):
        with pytest.raises(DataHubError):
            await run_stock_risk_analysis("600519", 30, 1, context, None)
    assert context.llm.calls == []
