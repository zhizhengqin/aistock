import pytest
import pandas as pd
from unittest.mock import patch
from app.services.analysis_orchestrator import run_full_analysis


@pytest.mark.asyncio
async def test_orchestrator_full_report_structure():
    mock_kline = pd.DataFrame({
        "open": [100]*120, "high": [101]*120, "low": [99]*120,
        "close": [100.0 + i * 0.1 for i in range(120)], "volume": [10000 + i for i in range(120)],
    })
    with patch("app.services.analysis_orchestrator.get_stock_info", return_value={
        "name": "贵州茅台", "price": 1685.5, "change_pct": 1.32, "industry": "白酒",
    }), patch("app.services.analysis_orchestrator.get_stock_kline", return_value=mock_kline), \
         patch("app.services.analysis_orchestrator.get_stock_financial_summary", return_value={
             "revenue": 88e8, "net_profit": 40e8, "roe": 15.2, "pe_ttm": 22.8, "pb": 7.5,
             "market_cap": 20000, "gross_margin": 91.5, "debt_ratio": 22.3,
         }), patch("app.services.analysis_orchestrator.get_stock_capital_flow", return_value={
             "net_main_flow": 2.3e8, "net_super_large": 1.5e8, "net_large": 0.5e8,
             "net_medium": -0.3e8, "net_small": -0.7e8,
         }), patch("app.services.analysis_orchestrator.get_stock_news_titles", return_value=[
             {"title": "业绩预增公告", "content": "营收稳健", "date": "2026-01-01"},
         ]):
        report = await run_full_analysis("600519", 1, None, None)

    assert report["stock_code"] == "600519"
    assert report["stock_name"] == "贵州茅台"
    assert "indicators" in report
    assert "ma" in report["indicators"]
    assert "analysts" in report
    assert "technical" in report["analysts"]
    assert "fundamental" in report["analysts"]
    assert "capital" in report["analysts"]
    assert "news" in report["analysts"]
    assert "sentiment" in report["analysts"]
    assert "decision" in report
    assert "rating" in report["decision"]
    assert "target_price" in report["decision"]
    assert "disclaimer" in report
    assert "analyzed_at" in report


@pytest.mark.asyncio
async def test_orchestrator_missing_data_graceful():
    """Orchestrator should not crash when akshare returns empty data."""
    with patch("app.services.analysis_orchestrator.get_stock_info", return_value={
        "name": "", "price": 0, "change_pct": 0, "industry": "",
    }), patch("app.services.analysis_orchestrator.get_stock_kline", return_value=pd.DataFrame()), \
         patch("app.services.analysis_orchestrator.get_stock_financial_summary", return_value={
             "revenue": 0, "net_profit": 0, "roe": 0, "pe_ttm": 0, "pb": 0,
             "market_cap": 0, "gross_margin": 0, "debt_ratio": 0,
         }), patch("app.services.analysis_orchestrator.get_stock_capital_flow", return_value={
             "net_main_flow": 0, "net_super_large": 0, "net_large": 0,
             "net_medium": 0, "net_small": 0,
         }), patch("app.services.analysis_orchestrator.get_stock_news_titles", return_value=[]):
        report = await run_full_analysis("999999", 1, None, None)

    assert report["stock_code"] == "999999"
    assert "decision" in report
    assert "analysts" in report
    # Indicators should be empty dicts since kline was empty
    assert report["indicators"]["ma"] == {} or report["indicators"]["ma"].get("MA5") is None
