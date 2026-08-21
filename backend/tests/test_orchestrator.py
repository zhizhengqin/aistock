import pandas as pd
import pytest
from unittest.mock import patch

from app.schemas.llm_outputs import (
    CapitalAnalysisOutput,
    ChiefDecisionOutput,
    FundamentalAnalysisOutput,
    NewsAnalysisOutput,
    SentimentAnalysisOutput,
    TechnicalAnalysisOutput,
)
from app.services.analysis_orchestrator import run_full_analysis


class _Context:
    task_id = 101

    def __init__(self):
        self.llm = _StructuredLlm()
        self.progress = []

    async def ensure_current(self):
        return None

    async def set_progress(self, value):
        self.progress.append(value)


class _StructuredLlm:
    async def execute_json(self, **kwargs):
        output_type = kwargs["output_type"]
        payloads = {
            TechnicalAnalysisOutput: {
                "trend": "震荡向上", "score": 70, "short_trend": "偏强", "mid_trend": "向上",
                "long_trend": "稳健", "support_resistance": [{"type": "支撑", "price": 95, "strength": "强"}],
                "breakout_prob": 65, "indicator_readings": "均线多头", "pattern": "上升通道",
            },
            FundamentalAnalysisOutput: {
                "financial_health": "稳健", "profitability": "良好", "valuation": "合理", "score": 8, "detail": "基本面稳定",
            },
            CapitalAnalysisOutput: {
                "main_flow": "净流入", "flow_trend": "改善", "score": 7, "detail": "资金流入",
            },
            NewsAnalysisOutput: {
                "sentiment_rating": "利好", "key_news": ["业绩稳定"], "impact": "正面",
            },
            SentimentAnalysisOutput: {
                "sentiment_score": 65, "indicators": "RSI偏强", "assessment": "情绪回暖",
            },
            ChiefDecisionOutput: {
                "rating": "持有", "target_price": 110, "stop_loss": 90, "confidence": 72,
                "entry_range": "98-102", "take_profit": "108-112", "holding_period": "1-3个月",
                "position_size": "20%", "risk_warning": "关注波动", "key_watchpoints": ["业绩"],
                "meeting_summary": "综合分析建议持有",
            },
        }
        return output_type.model_validate(payloads[output_type])


def _context():
    return _Context()


@pytest.mark.asyncio
async def test_orchestrator_full_report_structure():
    mock_kline = pd.DataFrame({
        "open": [100] * 120, "high": [101] * 120, "low": [99] * 120,
        "close": [100.0 + i * 0.1 for i in range(120)], "volume": [10000 + i for i in range(120)],
    })
    context = _context()
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
        report = await run_full_analysis("600519", 1, context, None)

    assert report["stock_code"] == "600519"
    assert report["stock_name"] == "贵州茅台"
    assert "indicators" in report
    assert "ma" in report["indicators"]
    assert set(report["analysts"]) == {"technical", "fundamental", "capital", "news", "sentiment"}
    assert report["decision"]["rating"] == "持有"
    assert "target_price" in report["decision"]
    assert "disclaimer" in report
    assert "analyzed_at" in report
    assert context.progress == [20, 40, 50, 80, 90]


@pytest.mark.asyncio
async def test_orchestrator_missing_data_graceful():
    """Empty market data still produces a typed report from the model service."""
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
        report = await run_full_analysis("999999", 1, _context(), None)

    assert report["stock_code"] == "999999"
    assert "decision" in report
    assert "analysts" in report
    assert report["indicators"]["ma"] == {} or report["indicators"]["ma"].get("MA5") is None
