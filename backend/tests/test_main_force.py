import pytest
from unittest.mock import patch
from app.schemas.llm_outputs import (
    MainForceCapitalOutput,
    MainForceFundamentalOutput,
    MainForceIndustryOutput,
    MainForceQuantOutput,
    MainForceResearcherOutput,
    MainForceTechnicalOutput,
)
from app.services.main_force_orchestrator import run_main_force_selection, _strategy_filter


class _Context:
    task_id = 202

    def __init__(self):
        self.llm = _Llm()

    async def ensure_current(self):
        return None

    async def set_progress(self, _value):
        return None


class _Llm:
    async def execute_json(self, **kwargs):
        output_type = kwargs["output_type"]
        if output_type is MainForceCapitalOutput:
            extra = {"flow_concentration": "大单集中"}
        elif output_type is MainForceIndustryOutput:
            extra = {"sector_trend": "景气向上"}
        elif output_type is MainForceFundamentalOutput:
            extra = {"health_rating": "健康"}
        elif output_type is MainForceTechnicalOutput:
            extra = {"pattern": "突破形态"}
        elif output_type is MainForceQuantOutput:
            extra = {"quant_signals": ["量价齐升"]}
        else:
            return output_type.model_validate({
                "companies": [{"code": "600519", "name": "贵州茅台", "buy_range": "98-102", "sell_range": "108-112", "confidence": 80, "position": "20%", "logic": "资金与基本面共振"}],
                "excluded": [],
                "meeting_summary": "研究员综合意见",
            })
        return output_type.model_validate({"focus_stocks": ["600519"], "analysis": "候选信号一致", "score": 8, **extra})


def test_strategy_filter_excludes_low_market_cap():
    candidates = [
        {"code": "600519", "name": "茅台", "market_cap": 2000, "change_pct_20d": 5,
         "net_main_flow_60d": 1e9, "shareholder": {"change_pct": -5}},
        {"code": "601398", "name": "工行", "market_cap": 30, "change_pct_20d": 3,
         "net_main_flow_60d": 1e9, "shareholder": {"change_pct": -2}},
    ]
    passed, excluded = _strategy_filter(candidates)
    assert len(passed) == 1
    assert passed[0]["code"] == "600519"
    assert len(excluded) == 1
    assert "流通市值" in excluded[0]["reason"]


def test_strategy_filter_excludes_high_20d_gain():
    candidates = [
        {"code": "000001", "name": "X", "market_cap": 200, "change_pct_20d": 15,
         "net_main_flow_60d": 1e9, "shareholder": {"change_pct": -3}},
    ]
    passed, excluded = _strategy_filter(candidates)
    assert len(passed) == 0
    assert "涨幅" in excluded[0]["reason"]


def test_strategy_filter_excludes_shareholder_increase():
    candidates = [
        {"code": "000001", "name": "X", "market_cap": 200, "change_pct_20d": 5,
         "net_main_flow_60d": 1e9, "shareholder": {"change_pct": 5}},
    ]
    passed, excluded = _strategy_filter(candidates)
    assert len(passed) == 0
    assert "股东户数" in excluded[0]["reason"]


@pytest.mark.asyncio
async def test_main_force_full_report_structure():
    mock_candidates = [
        {"code": "600519", "name": "贵州茅台", "net_main_flow": 5e8, "change_pct": 1.5,
         "net_main_pct": 2.3},
        {"code": "000858", "name": "五粮液", "net_main_flow": 3e8, "change_pct": 0.8,
         "net_main_pct": 1.8},
    ]
    mock_info = {"name": "测试", "price": 100, "change_pct": 1.0, "market_cap": 200, "industry": "测试"}
    mock_gdhs = {"latest": 10000, "previous": 11000, "change_pct": -9.0, "history": [10000, 11000]}

    import pandas as pd
    mock_kline = pd.DataFrame({
        "open": [100]*120, "high": [101]*120, "low": [99]*120,
        "close": [100.0 + i * 0.05 for i in range(120)], "volume": [10000]*120,
    })

    with patch("app.services.main_force_orchestrator.get_market_capital_flow_rank", return_value=mock_candidates), \
         patch("app.services.main_force_orchestrator.get_stock_info", return_value=mock_info), \
         patch("app.services.main_force_orchestrator.get_stock_kline", return_value=mock_kline), \
         patch("app.services.main_force_orchestrator.get_stock_shareholder_count", return_value=mock_gdhs), \
         patch("app.services.main_force_orchestrator.get_stock_capital_flow", return_value={
             "net_main_flow": 2.3,
         }):
        report = await run_main_force_selection(1, _Context(), None)

    assert "skim_count" in report
    assert "filtered_count" in report
    assert "recommended" in report
    assert "analysts" in report
    assert "excluded" in report
    assert "strategy" in report
    assert "capital" in report["analysts"]
    assert "industry" in report["analysts"]
    assert "fundamental" in report["analysts"]
    assert "technical" in report["analysts"]
    assert "quant" in report["analysts"]
    # Mock researcher returns companies list
    rec = report["recommended"]
    if "companies" in rec:
        assert len(rec["companies"]) >= 1
