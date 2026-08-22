"""Task 10 contracts for remaining structured analysis and data paths."""

from __future__ import annotations

import math
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest
from pydantic import ValidationError

from app.datahub.contracts import (
    Capability,
    DataQuality,
    DataResult,
    DragonTigerItem,
    DragonTigerSeat,
    KlineBar,
    StockSnapshot,
)

from app.schemas.llm_outputs import (
    DragonTigerAnalysisOutput,
    PortfolioDiagnosisOutput,
    RiskAnalysisOutput,
    UsResearchOutput,
)
from app.models.news_item import NewsItem
from app.services.dragon_tiger_orchestrator import run_dragon_tiger_analysis
from app.services.news_collector import NEWS_SOURCES, persist_news
from app.services.portfolio_orchestrator import run_portfolio_diagnosis
from app.services.risk_orchestrator import run_stock_risk_analysis
from app.services.us_research_orchestrator import CORE_US_STOCKS, build_report


class _Context:
    task_id = 404

    def __init__(self, llm):
        self.llm = llm
        self.progress: list[int] = []

    async def ensure_current(self):
        return None

    async def set_progress(self, value: int):
        self.progress.append(value)


class _TypedLlm:
    def __init__(self, *, failure: Exception | None = None):
        self.calls: list[dict] = []
        self.failure = failure

    async def execute_json(self, **kwargs):
        self.calls.append(kwargs)
        if self.failure is not None:
            raise self.failure
        output_type = kwargs["output_type"]
        return output_type.model_validate(_payload(output_type))


def _payload(output_type):
    if output_type is DragonTigerAnalysisOutput:
        return {
            "summary": "游资活跃度温和回升。",
            "confidence_score": 82.5,
            "active_institutions": [
                {"name": "东方财富拉萨", "success_rate": 48.2, "appearances": 12, "style": "短线"}
            ],
            "strategy_advice": "控制仓位并关注成交量。",
            "risk_level": "高风险",
        }
    if output_type is PortfolioDiagnosisOutput:
        return {
            "health_score": 45,
            "risk_assessment": "组合集中度偏高。",
            "asset_allocation": "建议分散行业配置。",
            "risk_exposure": "单一行业风险暴露较大。",
            "strategy_consistency": "持仓与策略需要重新匹配。",
            "suggestions": ["降低单一持仓比例"],
            "summary": "组合健康度偏低，建议分散投资。",
        }
    if output_type is RiskAnalysisOutput:
        return {
            "risk_level": "警告",
            "risk_score": 56,
            "analysis": "波动率偏高，存在回撤压力。",
            "advice": "关注支撑位并分批操作。",
        }
    if output_type is UsResearchOutput:
        return {
            "cards": {
                "us_sentiment": "震荡偏强",
                "a_share_impact": "中性偏结构性",
                "risk_level": "中等",
                "focus_directions": ["AI算力", "半导体"],
            },
            "sections": {
                "核心结论": "隔夜市场风险偏好回升。",
                "隔夜美股表现": "科技股领涨。",
                "核心个股解读": "核心个股表现分化。",
                "板块与主题": "成长板块占优。",
                "美债与宏观": "收益率小幅变化。",
                "重要新闻摘要": "政策表态偏谨慎。",
                "对A股的启示": "关注科技映射方向。",
                "风险提示": "海外流动性变化可能带来波动。",
            },
        }
    raise AssertionError(f"unhandled output type: {output_type}")


def _kline(rows: int = 60):
    close = [100 + i * 0.2 for i in range(rows)]
    return pd.DataFrame({
        "close": close,
        "open": close,
        "high": [v * 1.02 for v in close],
        "low": [v * 0.98 for v in close],
        "volume": [10000] * rows,
    })


_DATA_AT = datetime(2026, 8, 21, 7, 0, tzinfo=timezone.utc)


def _result(capability, data, *, rows=None):
    if rows is None:
        rows = len(data) if isinstance(data, list) else 1
    return DataResult(
        data=data,
        capability=capability,
        provider="fixture",
        data_at=_DATA_AT,
        quality=DataQuality(valid=True, rows=rows),
    )


def _kline_result(rows: int = 60):
    return _result(
        Capability.STOCK_KLINE_DAILY,
        [
            KlineBar(
                date=f"2026-07-{(i % 28) + 1:02d}",
                open=100 + i * 0.2,
                high=(100 + i * 0.2) * 1.02,
                low=(100 + i * 0.2) * 0.98,
                close=100 + i * 0.2,
                volume=10000,
                data_at=_DATA_AT,
            )
            for i in range(rows)
        ],
        rows=rows,
    )


def _snapshot_result(*, price=110):
    return _result(
        Capability.STOCK_SNAPSHOT,
        StockSnapshot(code="600519.SS", name="贵州茅台", price=price, industry="白酒", data_at=_DATA_AT),
    )


@pytest.mark.parametrize(
    ("model", "field", "value"),
    [
        (DragonTigerAnalysisOutput, "confidence_score", math.inf),
        (PortfolioDiagnosisOutput, "health_score", 101),
        (RiskAnalysisOutput, "risk_level", "未知"),
        (UsResearchOutput, "cards", {}),
    ],
)
def test_remaining_outputs_reject_invalid_payloads(model, field, value):
    payload = _payload(model)
    payload[field] = value
    with pytest.raises(ValidationError):
        model.model_validate(payload)


def test_remaining_outputs_reject_missing_and_extra_fields():
    payload = _payload(PortfolioDiagnosisOutput)
    payload.pop("summary")
    with pytest.raises(ValidationError):
        PortfolioDiagnosisOutput.model_validate(payload)
    with pytest.raises(ValidationError):
        PortfolioDiagnosisOutput.model_validate({**_payload(PortfolioDiagnosisOutput), "unexpected": True})


@pytest.mark.asyncio
async def test_remaining_orchestrators_use_typed_task_service_and_stable_steps():
    context = _Context(_TypedLlm())
    records = [DragonTigerItem(
        code="600519.SS", name="茅台", net_amount=3e8,
        buy_amount=4e8, sell_amount=1e8, change_pct=7,
        date="2026-08-01", reason="涨幅偏离", data_at=_DATA_AT,
    )]
    institutions = [DragonTigerSeat(
        name="东方财富拉萨", appearances=12, net_amount=5e8,
        data_at=_DATA_AT,
    )]
    with patch("app.services.dragon_tiger_orchestrator.get_dragon_tiger_list", return_value=_result(Capability.DRAGON_TIGER_LIST, records)), \
        patch("app.services.dragon_tiger_orchestrator.get_dragon_tiger_institution", return_value=_result(Capability.DRAGON_TIGER_SEATS, institutions)):
        dragon = await run_dragon_tiger_analysis(5, 1, context, None)
    assert dragon["analysis"]["summary"]
    assert context.llm.calls[-1]["step_key"] == "dragon_tiger.analysis.v1"

    context.llm.calls.clear()
    holdings = [{"stock_code": "600519", "stock_name": "贵州茅台", "shares": 100, "cost_price": 100, "industry": "白酒"}]
    with patch("app.services.portfolio_orchestrator.get_stock_info", return_value=_snapshot_result()), \
        patch("app.services.portfolio_orchestrator.get_stock_kline", return_value=_kline_result()):
        portfolio = await run_portfolio_diagnosis(holdings, 1, context, None)
    assert portfolio["summary"]
    assert context.llm.calls[-1]["step_key"] == "portfolio.diagnosis.v1"

    context.llm.calls.clear()
    with patch("app.services.risk_orchestrator.get_stock_info", return_value=_snapshot_result()), \
        patch("app.services.risk_orchestrator.get_stock_kline", return_value=_kline_result()):
        risk = await run_stock_risk_analysis("600519", 30, 1, context, None)
    assert risk["ai_analysis"]["risk_score"] == 56
    assert context.llm.calls[-1]["step_key"] == "risk.analysis.v1"

    context.llm.calls.clear()
    source_data = {
        "indices": [{"name": "道琼斯", "ticker": "^DJI", "close": 1, "change_pct": 1}],
        "core_stocks": [{**stock, "close": 100.0, "change_pct": 1.5} for stock in CORE_US_STOCKS],
        "bond_yields": {"y2": 3.8, "y10": 4.2, "y30": 4.8},
        "sectors": [{"name": "半导体", "ticker": "SMH.US", "close": 100.0, "change_pct": 2.1}],
        "news": [{"title": "Fed holds rates", "source": "CNBC", "url": "https://example.com"}],
        "movers": {"gainers": [{"ticker": "NVDA", "name": "英伟达", "change_pct": 2}], "losers": []},
    }
    with patch("app.services.us_research_orchestrator.fetch_us_indices", return_value=source_data["indices"]), \
        patch("app.services.us_research_orchestrator.fetch_us_core_stocks", return_value=source_data["core_stocks"]), \
        patch("app.services.us_research_orchestrator.fetch_us_bond_yields", return_value=source_data["bond_yields"]), \
        patch("app.services.us_research_orchestrator.fetch_us_sector_samples", return_value=source_data["sectors"]), \
        patch("app.services.us_research_orchestrator.fetch_english_news", return_value=source_data["news"]), \
        patch("app.services.us_research_orchestrator.fetch_us_movers", return_value=source_data["movers"]):
        us_report = await build_report("2026-08-07", user_id=1, execution_ctx=context)
    assert us_report["cards"]["us_sentiment"]
    assert context.llm.calls[-1]["step_key"] == "us_research.narrative.v1"


@pytest.mark.asyncio
async def test_required_model_failure_propagates_without_fallback():
    context = _Context(_TypedLlm(failure=RuntimeError("provider failed")))
    records = [DragonTigerItem(
        code="600519.SS", name="茅台", net_amount=1, buy_amount=2,
        sell_amount=1, appearances=1, change_pct=1,
        date="2026-08-01", reason="涨幅", data_at=_DATA_AT,
    )]
    with patch("app.services.dragon_tiger_orchestrator.get_dragon_tiger_list", return_value=_result(Capability.DRAGON_TIGER_LIST, records)), \
        patch("app.services.dragon_tiger_orchestrator.get_dragon_tiger_institution", return_value=_result(Capability.DRAGON_TIGER_SEATS, [])):
        with pytest.raises(RuntimeError, match="provider failed"):
            await run_dragon_tiger_analysis(5, 1, context, None)


def test_news_source_failure_does_not_seed_samples(test_db):
    _, session_factory = test_db
    db = session_factory()
    stats = persist_news(db, [], [{"source": "全部来源", "error": "网络不可用"}])
    assert stats["new"] == 0
    assert stats["errors"]
    assert db.query(NewsItem).count() == 0
    db.close()


def test_product_contains_no_mock_or_fixed_ai_fallback():
    root = Path(__file__).resolve().parents[2] / "app"
    product_text = "\n".join(path.read_text(encoding="utf-8") for path in root.rglob("*.py"))
    forbidden = ("LLM_MOCK", "MOCK_RESPONSES", "DEFAULT_LLM_NARRATIVE", "_safe_chat", "allow_fallback")
    assert all(token not in product_text for token in forbidden)
