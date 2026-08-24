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
from app.datahub.contracts import Capability, DataQuality, DataResult, FundFlow, FundFlowRankItem, KlineBar, ShareholderSummary, StockSnapshot
from datetime import datetime, timezone


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


def test_strategy_filter_excludes_missing_60d_flow_with_explicit_reason():
    candidates = [{
        "code": "000001",
        "name": "X",
        "market_cap": 200,
        "change_pct_20d": 5,
        "net_main_flow_60d": None,
        "shareholder": {"change_pct": -3},
    }]

    passed, excluded = _strategy_filter(candidates)

    assert passed == []
    assert len(excluded) == 1
    assert "60日主力净流入数据缺失" in excluded[0]["reason"]


def test_strategy_filter_excludes_missing_market_cap_with_explicit_reason():
    candidates = [{
        "code": "000001",
        "name": "X",
        "market_cap": None,
        "change_pct_20d": 5,
        "net_main_flow_60d": 1e9,
        "shareholder": {"change_pct": -3},
    }]

    passed, excluded = _strategy_filter(candidates)

    assert passed == []
    assert len(excluded) == 1
    assert "流通市值数据缺失" in excluded[0]["reason"]


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

    now = datetime(2026, 8, 21, tzinfo=timezone.utc)
    rank_result = DataResult(data=[FundFlowRankItem(code=row["code"], name=row["name"], net_main_flow=row["net_main_flow"], change_pct=row["change_pct"], net_main_pct=row["net_main_pct"], data_at=now) for row in mock_candidates], capability=Capability.MARKET_FUND_FLOW_RANK, provider="fixture", data_at=now, quality=DataQuality(valid=True, rows=2))
    snapshot_result = DataResult(data=StockSnapshot(code="600519.SS", name="测试", price=100, change_pct=1, market_cap=200, industry="测试", data_at=now), capability=Capability.STOCK_SNAPSHOT, provider="fixture", data_at=now)
    kline_result = DataResult(data=[KlineBar(date=f"2026-08-{(i % 20) + 1:02d}", open=100, high=101, low=99, close=100 + i * 0.05, volume=10000, data_at=now) for i in range(120)], capability=Capability.STOCK_KLINE_DAILY, provider="fixture", data_at=now, quality=DataQuality(valid=True, rows=120))
    shareholder_result = DataResult(data=ShareholderSummary(code="600519.SS", latest=10000, previous=11000, change_pct=-9, history=[10000, 11000], data_at=now), capability=Capability.STOCK_SHAREHOLDERS, provider="fixture", data_at=now)
    flow_result = DataResult(data=FundFlow(code="600519.SS", net_main_flow=2.3, data_at=now), capability=Capability.STOCK_FUND_FLOW, provider="fixture", data_at=now)

    with patch("app.services.main_force_orchestrator.get_market_capital_flow_rank", return_value=rank_result), \
         patch("app.services.main_force_orchestrator.get_stock_info", return_value=snapshot_result), \
         patch("app.services.main_force_orchestrator.get_stock_kline", return_value=kline_result), \
         patch("app.services.main_force_orchestrator.get_stock_shareholder_count", return_value=shareholder_result), \
         patch("app.services.main_force_orchestrator.get_stock_capital_flow", return_value=flow_result):
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
