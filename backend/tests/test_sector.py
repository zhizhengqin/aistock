import pytest
from unittest.mock import patch
from app.services.sector_orchestrator import run_sector_analysis


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
        report = await run_sector_analysis(1, None, None)

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
