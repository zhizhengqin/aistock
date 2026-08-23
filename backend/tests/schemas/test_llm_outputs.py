"""Contract tests for versioned structured business-model outputs."""

import math

import pytest
from pydantic import ValidationError

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


def _technical() -> dict:
    return {
        "trend": "震荡向上",
        "score": 72,
        "short_trend": "短线偏强",
        "mid_trend": "中期向上",
        "long_trend": "长期稳健",
        "support_resistance": [{"type": "支撑", "price": 1600.0, "strength": "较强"}],
        "breakout_prob": 63.5,
        "indicator_readings": "均线多头排列，MACD维持正值",
        "pattern": "上升通道",
    }


def _fundamental() -> dict:
    return {
        "financial_health": "稳健",
        "profitability": "盈利能力较强",
        "valuation": "估值合理",
        "score": 8.2,
        "detail": "盈利和现金流保持稳定。",
    }


def _capital() -> dict:
    return {
        "main_flow": "主力净流入",
        "flow_trend": "持续改善",
        "score": 7.5,
        "detail": "大单资金连续净流入。",
    }


def _news() -> dict:
    return {
        "sentiment_rating": "利好",
        "key_news": ["公司发布稳健业绩公告"],
        "impact": "短期情绪偏正面。",
    }


def _sentiment() -> dict:
    return {
        "sentiment_score": 68.0,
        "indicators": "RSI处于中性偏强区间",
        "assessment": "市场情绪温和回暖。",
    }


def _chief() -> dict:
    return {
        "rating": "买入",
        "target_price": 1800.0,
        "stop_loss": 1550.0,
        "confidence": 77.0,
        "entry_range": "1600-1650",
        "take_profit": "1750-1800",
        "holding_period": "1-3个月",
        "position_size": "建议仓位20%-30%",
        "risk_warning": "关注估值和市场波动风险。",
        "key_watchpoints": ["业绩兑现", "主力资金变化"],
        "meeting_summary": "综合五位分析师意见，维持谨慎看多。",
    }


def _main_force_common(extra: dict) -> dict:
    return {
        "focus_stocks": ["600519"],
        "analysis": "候选标的的信号较为一致。",
        "score": 8.0,
        **extra,
    }


def _researcher() -> dict:
    return {
        "companies": [
            {
                "code": "600519",
                "name": "贵州茅台",
                "buy_range": "1600-1650",
                "sell_range": "1750-1800",
                "confidence": 82.0,
                "position": "20%",
                "logic": "资金与基本面共振。",
            }
        ],
        "excluded": [{"code": "000001", "name": "平安银行", "reason": "趋势较弱"}],
        "meeting_summary": "研究员会议形成一项精选建议。",
    }


def _sector_chief_item() -> dict:
    return {"name": "半导体", "confidence": 8.5, "logic": "景气度改善", "risk": "波动较大"}


def _sector_chief() -> dict:
    return {
        "bull_sectors": [_sector_chief_item()],
        "bear_sectors": [{**_sector_chief_item(), "name": "地产"}],
        "neutral_sectors": [{**_sector_chief_item(), "name": "银行"}],
        "operation_advice": "逢低配置景气改善板块。",
        "risk_triggers": "指数跌破关键均线。",
        "key_indicators": ["北向资金", "成交额"],
    }


@pytest.mark.parametrize(
    ("model", "payload"),
    [
        (TechnicalAnalysisOutput, _technical()),
        (FundamentalAnalysisOutput, _fundamental()),
        (CapitalAnalysisOutput, _capital()),
        (NewsAnalysisOutput, _news()),
        (SentimentAnalysisOutput, _sentiment()),
        (ChiefDecisionOutput, _chief()),
        (MainForceCapitalOutput, _main_force_common({"flow_concentration": "大单集中"})),
        (MainForceIndustryOutput, _main_force_common({"sector_trend": "景气上行"})),
        (MainForceFundamentalOutput, _main_force_common({"health_rating": "健康"})),
        (MainForceTechnicalOutput, _main_force_common({"pattern": "突破形态"})),
        (MainForceQuantOutput, _main_force_common({"quant_signals": ["量价齐升"]})),
        (MainForceResearcherOutput, _researcher()),
        (
            SectorMacroOutput,
            {"report": "宏观环境中性偏暖。", "score": 7.0},
        ),
        (
            SectorDiagnosisOutput,
            {
                "sectors": [{"name": "半导体", "health": "良好", "trend": "向上"}],
                "score": 7.5,
            },
        ),
        (
            SectorCapitalOutput,
            {
                "inflow_sectors": ["半导体"],
                "outflow_sectors": ["地产"],
                "report": "资金向成长板块集中。",
                "score": 7.0,
            },
        ),
        (
            SectorSentimentOutput,
            {"sentiment_score": 66.0, "width": "上涨家数占优", "assessment": "情绪回暖。"},
        ),
        (SectorChiefOutput, _sector_chief()),
    ],
)
def test_v1_outputs_are_strict_and_versioned(model, payload):
    result = model.model_validate(payload)
    assert result.model_dump(mode="json") == payload
    assert model.schema_version == "v1"
    assert model.model_config["extra"] == "forbid"


def test_output_rejects_missing_extra_wrong_and_blank_values():
    payload = _technical()
    payload.pop("summary", None)
    payload.pop("pattern")
    with pytest.raises(ValidationError):
        TechnicalAnalysisOutput.model_validate(payload)

    payload = _technical() | {"unexpected": True}
    with pytest.raises(ValidationError):
        TechnicalAnalysisOutput.model_validate(payload)

    payload = _technical() | {"score": "72"}
    with pytest.raises(ValidationError):
        TechnicalAnalysisOutput.model_validate(payload)

    payload = _chief() | {"meeting_summary": "   "}
    with pytest.raises(ValidationError):
        ChiefDecisionOutput.model_validate(payload)


def test_chief_normalizes_numeric_entry_and_take_profit_values():
    payload = _chief() | {
        "entry_range": [50.5, 52.5],
        "take_profit": 55.57,
    }

    result = ChiefDecisionOutput.model_validate(payload)

    assert result.entry_range == "50.5-52.5"
    assert result.take_profit == "55.57"
    assert result.model_dump(mode="json")["entry_range"] == "50.5-52.5"
    assert result.model_dump(mode="json")["take_profit"] == "55.57"


@pytest.mark.parametrize(
    "value",
    [
        [50.5, 52.5, 54.5],
        [52.5, 50.5],
        [0, 52.5],
        [-1, 52.5],
        [50.5, math.nan],
        [True, 52.5],
        {"low": 50.5, "high": 52.5},
    ],
)
def test_chief_rejects_invalid_numeric_entry_range(value):
    with pytest.raises(ValidationError):
        ChiefDecisionOutput.model_validate(_chief() | {"entry_range": value})


@pytest.mark.parametrize("value", [True, 0, -1, math.nan, math.inf, [55.57]])
def test_chief_rejects_invalid_numeric_take_profit(value):
    with pytest.raises(ValidationError):
        ChiefDecisionOutput.model_validate(_chief() | {"take_profit": value})


@pytest.mark.parametrize(
    ("model", "field", "value"),
    [
        (TechnicalAnalysisOutput, "score", -1),
        (TechnicalAnalysisOutput, "score", 101),
        (TechnicalAnalysisOutput, "breakout_prob", math.inf),
        (SentimentAnalysisOutput, "sentiment_score", math.nan),
        (ChiefDecisionOutput, "rating", "观望"),
        (
            MainForceResearcherOutput,
            "companies",
            [{"code": "", "name": "测试", "buy_range": "1", "sell_range": "2", "confidence": 50, "position": "10%", "logic": "原因"}],
        ),
        (SectorChiefOutput, "bull_sectors", [{"name": "", "confidence": 4, "logic": "无"}]),
    ],
)
def test_output_rejects_boundaries_enums_nonfinite_and_invalid_ranked_item(model, field, value):
    payload = {
        TechnicalAnalysisOutput: _technical,
        SentimentAnalysisOutput: _sentiment,
        ChiefDecisionOutput: _chief,
        MainForceResearcherOutput: _researcher,
        SectorChiefOutput: _sector_chief,
    }[model]()
    payload[field] = value
    with pytest.raises(ValidationError):
        model.model_validate(payload)
