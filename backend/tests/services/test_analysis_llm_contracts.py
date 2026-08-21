"""Orchestrator contracts for task-scoped, typed model calls."""

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd
import pytest

from app.schemas.llm_outputs import (
    CapitalAnalysisOutput,
    ChiefDecisionOutput,
    FundamentalAnalysisOutput,
    MainForceCapitalOutput,
    MainForceFundamentalOutput,
    MainForceIndustryOutput,
    MainForceQuantOutput,
    MainForceResearcherOutput,
    MainForceTechnicalOutput,
    NewsAnalysisOutput,
    SectorCapitalOutput,
    SectorChiefOutput,
    SectorDiagnosisOutput,
    SectorMacroOutput,
    SectorSentimentOutput,
    SentimentAnalysisOutput,
    TechnicalAnalysisOutput,
)
from app.services.analysis_orchestrator import run_full_analysis
from app.services.main_force_orchestrator import run_main_force_selection
from app.services.sector_orchestrator import run_sector_analysis


class FakeContext:
    task_id = 7
    execution_token = "token-7"

    def __init__(self, llm):
        self.llm = llm
        self.progress: list[int] = []
        self.fence_checks = 0

    async def ensure_current(self):
        self.fence_checks += 1

    async def set_progress(self, value: int):
        self.progress.append(value)


class StructuredLLM:
    def __init__(self, payloads=None, *, failure: Exception | None = None):
        self.calls: list[dict] = []
        self.payloads = payloads or {}
        self.failure = failure

    async def execute_json(self, **kwargs):
        self.calls.append(kwargs)
        if self.failure is not None:
            raise self.failure
        output_type = kwargs["output_type"]
        payload = self.payloads.get(output_type)
        if payload is None:
            payload = _payload_for(output_type)
        return output_type.model_validate(payload)


def _payload_for(output_type):
    if output_type is TechnicalAnalysisOutput:
        return {
            "trend": "震荡向上",
            "score": 72,
            "short_trend": "偏强",
            "mid_trend": "向上",
            "long_trend": "稳健",
            "support_resistance": [{"type": "支撑", "price": 100, "strength": "强"}],
            "breakout_prob": 65,
            "indicator_readings": "均线多头",
            "pattern": "上升通道",
        }
    if output_type is FundamentalAnalysisOutput:
        return {"financial_health": "稳健", "profitability": "良好", "valuation": "合理", "score": 8, "detail": "基本面稳定"}
    if output_type is CapitalAnalysisOutput:
        return {"main_flow": "净流入", "flow_trend": "改善", "score": 7, "detail": "资金持续流入"}
    if output_type is NewsAnalysisOutput:
        return {"sentiment_rating": "利好", "key_news": ["业绩稳定"], "impact": "情绪正面"}
    if output_type is SentimentAnalysisOutput:
        return {"sentiment_score": 67, "indicators": "RSI偏强", "assessment": "情绪回暖"}
    if output_type is ChiefDecisionOutput:
        return {
            "rating": "买入",
            "target_price": 110,
            "stop_loss": 90,
            "confidence": 75,
            "entry_range": "98-102",
            "take_profit": "108-112",
            "holding_period": "1-3个月",
            "position_size": "20%",
            "risk_warning": "关注波动",
            "key_watchpoints": ["业绩"],
            "meeting_summary": "综合分析建议关注",
        }
    if output_type in {
        MainForceCapitalOutput,
        MainForceIndustryOutput,
        MainForceFundamentalOutput,
        MainForceTechnicalOutput,
        MainForceQuantOutput,
    }:
        extra = {
            MainForceCapitalOutput: {"flow_concentration": "大单集中"},
            MainForceIndustryOutput: {"sector_trend": "景气向上"},
            MainForceFundamentalOutput: {"health_rating": "健康"},
            MainForceTechnicalOutput: {"pattern": "突破"},
            MainForceQuantOutput: {"quant_signals": ["量价齐升"]},
        }[output_type]
        return {"focus_stocks": ["600519"], "analysis": "信号一致", "score": 8, **extra}
    if output_type is MainForceResearcherOutput:
        return {
            "companies": [{"code": "600519", "name": "贵州茅台", "buy_range": "98-102", "sell_range": "108-112", "confidence": 80, "position": "20%", "logic": "基本面和资金共振"}],
            "excluded": [],
            "meeting_summary": "研究员综合意见",
        }
    if output_type is SectorMacroOutput:
        return {"report": "宏观环境温和", "score": 7}
    if output_type is SectorDiagnosisOutput:
        return {"sectors": [{"name": "半导体", "health": "良好", "trend": "向上"}], "score": 7}
    if output_type is SectorCapitalOutput:
        return {"inflow_sectors": ["半导体"], "outflow_sectors": ["地产"], "report": "资金轮动", "score": 7}
    if output_type is SectorSentimentOutput:
        return {"sentiment_score": 65, "width": "偏暖", "assessment": "情绪回暖"}
    if output_type is SectorChiefOutput:
        item = {"name": "半导体", "confidence": 8, "logic": "景气改善", "risk": "波动"}
        return {
            "bull_sectors": [item],
            "bear_sectors": [{**item, "name": "地产"}],
            "neutral_sectors": [{**item, "name": "银行"}],
            "operation_advice": "逢低配置",
            "risk_triggers": "跌破均线",
            "key_indicators": ["成交额"],
        }
    raise AssertionError(f"unhandled output type: {output_type}")


def _kline():
    return pd.DataFrame(
        {
            "open": [100] * 120,
            "high": [101] * 120,
            "low": [99] * 120,
            "close": [100 + i * 0.1 for i in range(120)],
            "volume": [10000] * 120,
        }
    )


@pytest.mark.asyncio
async def test_stock_orchestrator_uses_typed_task_llm_and_stable_steps():
    context = FakeContext(StructuredLLM())
    with patch("app.services.analysis_orchestrator.get_stock_info", return_value={"name": "测试", "price": 100, "change_pct": 1, "industry": "科技"}), \
        patch("app.services.analysis_orchestrator.get_stock_kline", return_value=_kline()), \
        patch("app.services.analysis_orchestrator.get_stock_financial_summary", return_value={"revenue": 1e8, "net_profit": 0.5e8, "roe": 12, "pe_ttm": 20, "pb": 2, "market_cap": 100, "gross_margin": 40, "debt_ratio": 30}), \
        patch("app.services.analysis_orchestrator.get_stock_capital_flow", return_value={"net_main_flow": 1e8, "net_super_large": 1e8, "net_large": 0, "net_medium": 0, "net_small": 0}), \
        patch("app.services.analysis_orchestrator.get_stock_news_titles", return_value=[]), \
        patch("app.services.analysis_orchestrator.compute_all", return_value={"ma": {}, "macd": {}, "rsi": {}, "kdj": {}, "boll": {}}):
        report = await run_full_analysis("600519", 1, context, None)

    assert set(report["analysts"]) == {"technical", "fundamental", "capital", "news", "sentiment"}
    assert report["decision"]["rating"] == "买入"
    assert {call["step_key"] for call in context.llm.calls} == {
        "stock.technical.v1",
        "stock.fundamental.v1",
        "stock.capital.v1",
        "stock.news.v1",
        "stock.sentiment.v1",
        "stock.chief.v1",
    }
    assert all(call["prompt_version"].endswith(".v1") for call in context.llm.calls)
    assert context.fence_checks >= 6


@pytest.mark.asyncio
async def test_required_stock_step_failure_propagates_without_neutral_fallback():
    context = FakeContext(StructuredLLM(failure=RuntimeError("provider failed")))
    with patch("app.services.analysis_orchestrator.get_stock_info", return_value={"name": "测试", "price": 100, "change_pct": 1, "industry": "科技"}), \
        patch("app.services.analysis_orchestrator.get_stock_kline", return_value=_kline()), \
        patch("app.services.analysis_orchestrator.get_stock_financial_summary", return_value={"revenue": 1e8, "net_profit": 0.5e8, "roe": 12, "pe_ttm": 20, "pb": 2, "market_cap": 100, "gross_margin": 40, "debt_ratio": 30}), \
        patch("app.services.analysis_orchestrator.get_stock_capital_flow", return_value={"net_main_flow": 1e8, "net_super_large": 1e8, "net_large": 0, "net_medium": 0, "net_small": 0}), \
        patch("app.services.analysis_orchestrator.get_stock_news_titles", return_value=[]), \
        patch("app.services.analysis_orchestrator.compute_all", return_value={"ma": {}, "macd": {}, "rsi": {}, "kdj": {}, "boll": {}}):
        with pytest.raises(RuntimeError, match="provider failed"):
            await run_full_analysis("600519", 1, context, None)
    assert not any(call["step_key"] == "stock.chief.v1" for call in context.llm.calls)


@pytest.mark.asyncio
async def test_main_force_and_sector_wait_for_typed_synthesis_inputs():
    llm = StructuredLLM()
    context = FakeContext(llm)
    candidate = {"code": "600519", "name": "贵州茅台", "net_main_flow": 1e8, "change_pct": 1, "net_main_pct": 1}
    with patch("app.services.main_force_orchestrator.get_market_capital_flow_rank", return_value=[candidate]), \
        patch("app.services.main_force_orchestrator._enrich_candidate", return_value={**candidate, "market_cap": 100, "change_pct_20d": 1, "net_main_flow_60d": 1, "shareholder": {"change_pct": -1}}):
        main_report = await run_main_force_selection(1, context, None)

    assert main_report["recommended"]["companies"]
    main_keys = [call["step_key"] for call in llm.calls]
    assert main_keys[-1] == "main_force.researcher.v1"
    assert set(main_keys[:5]) == {
        "main_force.capital.v1", "main_force.industry.v1", "main_force.fundamental.v1",
        "main_force.technical.v1", "main_force.quant.v1",
    }

    llm.calls.clear()
    with patch("app.services.sector_orchestrator.get_market_indices", return_value=[]), \
        patch("app.services.sector_orchestrator.get_sw_sector_list", return_value=[]), \
        patch("app.services.sector_orchestrator.get_sector_capital_flow", return_value=[]):
        sector_report = await run_sector_analysis(1, context, None)

    assert sector_report["decision"]["operation_advice"]
    sector_keys = [call["step_key"] for call in llm.calls]
    assert sector_keys[-1] == "sector.chief.v1"
    assert set(sector_keys[:4]) == {"sector.macro.v1", "sector.diagnosis.v1", "sector.capital.v1", "sector.sentiment.v1"}
