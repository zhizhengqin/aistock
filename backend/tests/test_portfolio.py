import pytest
import pandas as pd
from unittest.mock import patch
from app.services.portfolio_orchestrator import run_portfolio_diagnosis
from app.services.risk_orchestrator import run_stock_risk_analysis, run_portfolio_risk_scan
from tests.services.test_remaining_llm_contracts import _Context, _TypedLlm


@pytest.mark.asyncio
async def test_portfolio_diagnosis_structure():
    holdings = [
        {"stock_code": "600519", "stock_name": "贵州茅台", "shares": 100, "cost_price": 1680, "industry": "白酒"},
    ]
    mock_info = {"name": "贵州茅台", "price": 1700, "change_pct": 0.5, "market_cap": 2000, "industry": "白酒"}
    mock_kline = pd.DataFrame({"close": [1650 + i for i in range(60)] * 2, "open": 1650, "high": 1700, "low": 1640, "volume": 10000})
    with patch("app.services.portfolio_orchestrator.get_stock_info", return_value=mock_info), \
         patch("app.services.portfolio_orchestrator.get_stock_kline", return_value=mock_kline):
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
    mock_info = {"name": "贵州茅台", "price": 1700, "change_pct": 0.5, "industry": "白酒"}
    import numpy as np
    prices = list(np.linspace(100, 130, 45))
    prices += [132, 130, 80, 85, 82]
    closes = prices[:50]
    mock_kline = pd.DataFrame({"close": closes, "high": [c * 1.02 for c in closes], "low": [c * 0.98 for c in closes], "open": closes, "volume": [10000] * 50})

    with patch("app.services.risk_orchestrator.get_stock_info", return_value=mock_info), \
         patch("app.services.risk_orchestrator.get_stock_kline", return_value=mock_kline):
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
    mock_kline = pd.DataFrame({"close": closes, "high": [c * 1.02 for c in closes], "low": [c * 0.98 for c in closes], "open": closes, "volume": [10000] * 60})

    with patch("app.services.risk_orchestrator.get_stock_kline", return_value=mock_kline):
        report = await run_portfolio_risk_scan(holdings, 1, _Context(_TypedLlm()), None)

    assert "holdings" in report
    assert "portfolio" in report
    assert len(report["holdings"]) == 2
    assert "total_warnings" in report["portfolio"]
    assert "max_level" in report["portfolio"]
    assert "composite_score" in report["portfolio"]
