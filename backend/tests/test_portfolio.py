import pytest
from datetime import datetime, timezone
from unittest.mock import patch

from app.datahub.contracts import Capability, DataQuality, DataResult, KlineBar
from app.services.portfolio_orchestrator import run_portfolio_diagnosis
from app.services.risk_orchestrator import run_stock_risk_analysis, run_portfolio_risk_scan
from tests.services.test_remaining_llm_contracts import _Context, _TypedLlm, _snapshot_result


_DATA_AT = datetime(2026, 8, 21, 7, 0, tzinfo=timezone.utc)


def _kline_result(closes):
    rows = [
        KlineBar(
            date=f"2026-07-{(i % 28) + 1:02d}",
            open=float(close),
            high=float(close) * 1.02,
            low=float(close) * 0.98,
            close=float(close),
            volume=10000,
            data_at=_DATA_AT,
        )
        for i, close in enumerate(closes)
    ]
    return DataResult(
        data=rows,
        capability=Capability.STOCK_KLINE_DAILY,
        provider="fixture",
        data_at=_DATA_AT,
        quality=DataQuality(valid=True, rows=len(rows)),
    )


@pytest.mark.asyncio
async def test_portfolio_diagnosis_structure():
    holdings = [
        {"stock_code": "600519", "stock_name": "贵州茅台", "shares": 100, "cost_price": 1680, "industry": "白酒"},
    ]
    with patch("app.services.portfolio_orchestrator.get_stock_info", return_value=_snapshot_result(price=1700)), \
         patch("app.services.portfolio_orchestrator.get_stock_kline", return_value=_kline_result([1650 + i for i in range(60)] * 2)):
        report = await run_portfolio_diagnosis(holdings, 1, _Context(_TypedLlm()), None)

    assert "health_score" in report
    assert "risk_assessment" in report
    assert "suggestions" in report
    assert "portfolio_stats" in report
    assert report["portfolio_stats"]["total_stocks"] == 1
    assert len(report["holdings"]) == 1
    assert report["holdings"][0]["current_price"] == 1700


@pytest.mark.asyncio
async def test_stock_risk_analysis_structure():
    import numpy as np
    prices = list(np.linspace(100, 130, 45))
    prices += [132, 130, 80, 85, 82]
    closes = prices[:50]
    with patch("app.services.risk_orchestrator.get_stock_info", return_value=_snapshot_result(price=1700)), \
         patch("app.services.risk_orchestrator.get_stock_kline", return_value=_kline_result(closes)):
        report = await run_stock_risk_analysis("600519", 30, 1, _Context(_TypedLlm()), None)

    assert "stock_code" in report
    assert "warnings" in report
    assert "ai_analysis" in report
    assert "days" in report
    assert report["stock_code"] == "600519"


@pytest.mark.asyncio
async def test_portfolio_risk_scan_structure():
    holdings = [
        {"stock_code": "600519", "stock_name": "茅台"},
        {"stock_code": "000858", "stock_name": "五粮液"},
    ]
    import numpy as np
    closes = list(np.linspace(100, 130, 60))
    with patch("app.services.risk_orchestrator.get_stock_kline", return_value=_kline_result(closes)):
        report = await run_portfolio_risk_scan(holdings, 1, _Context(_TypedLlm()), None)

    assert "holdings" in report
    assert "portfolio" in report
    assert len(report["holdings"]) == 2
    assert "total_warnings" in report["portfolio"]
    assert "max_level" in report["portfolio"]
    assert "composite_score" in report["portfolio"]
