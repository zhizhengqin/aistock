import pytest
from unittest.mock import patch
from app.schemas.llm_outputs import (
    SectorCapitalOutput,
    SectorChiefOutput,
    SectorDiagnosisOutput,
    SectorMacroOutput,
    SectorSentimentOutput,
)
from app.services.sector_orchestrator import run_sector_analysis


class _Context:
    task_id = 303

    def __init__(self):
        self.llm = _Llm()

    async def ensure_current(self):
        return None

    async def set_progress(self, _value):
        return None


class _Llm:
    async def execute_json(self, **kwargs):
        output_type = kwargs["output_type"]
        if output_type is SectorMacroOutput:
            payload = {"report": "宏观环境温和", "score": 7}
        elif output_type is SectorDiagnosisOutput:
            payload = {"sectors": [{"name": "半导体", "health": "良好", "trend": "向上"}], "score": 7}
        elif output_type is SectorCapitalOutput:
            payload = {"inflow_sectors": ["半导体"], "outflow_sectors": ["地产"], "report": "资金轮动", "score": 7}
        elif output_type is SectorSentimentOutput:
            payload = {"sentiment_score": 65, "width": "偏暖", "assessment": "情绪回暖"}
        else:
            item = {"name": "半导体", "confidence": 8, "logic": "景气改善", "risk": "波动"}
            payload = {
                "bull_sectors": [item],
                "bear_sectors": [{**item, "name": "地产"}],
                "neutral_sectors": [{**item, "name": "银行"}],
                "operation_advice": "逢低配置",
                "risk_triggers": "跌破均线",
                "key_indicators": ["成交额"],
            }
        return output_type.model_validate(payload)


@pytest.mark.asyncio
async def test_sector_full_report_structure():
    mock_indices = [
        {"code": "000001", "name": "上证指数", "price": 3200, "change_pct": 0.5},
        {"code": "399001", "name": "深证成指", "price": 10500, "change_pct": 0.8},
    ]
    mock_sw = [
        {"code": "801013", "name": "白酒", "change_pct": 1.5, "price": 5000, "turnover": 1e9},
        {"code": "801081", "name": "半导体", "change_pct": 2.3, "price": 3000, "turnover": 2e9},
    ]
    mock_flow = [
        {"name": "半导体", "change_pct": 2.3, "net_main_flow": 5e8, "net_main_pct": 3.5},
        {"name": "银行", "change_pct": 0.5, "net_main_flow": 3e8, "net_main_pct": 2.1},
    ]

    with patch("app.services.sector_orchestrator.get_market_indices", return_value=mock_indices), \
         patch("app.services.sector_orchestrator.get_sw_sector_list", return_value=mock_sw), \
         patch("app.services.sector_orchestrator.get_sector_capital_flow", return_value=mock_flow):
        report = await run_sector_analysis(1, _Context(), None)

    assert "agents" in report
    assert "decision" in report
    assert "report_date" in report
    assert "market_snapshot" in report
    assert "macro" in report["agents"]
    assert "diagnosis" in report["agents"]
    assert "capital" in report["agents"]
    assert "sentiment" in report["agents"]
    # Mock chief returns bull/bear/neutral
    decision = report["decision"]
    if "bull_sectors" in decision:
        assert len(decision["bull_sectors"]) >= 1
        assert len(decision["bear_sectors"]) >= 1
        assert "operation_advice" in decision
