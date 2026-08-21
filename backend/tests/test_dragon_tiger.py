import pytest
from unittest.mock import patch
from app.services.dragon_tiger_scorer import score_stock, rank_top_stocks, compute_stats, rank_institutions
from app.services.dragon_tiger_orchestrator import run_dragon_tiger_analysis
from tests.services.test_remaining_llm_contracts import _Context, _TypedLlm


# --- Scoring engine tests (pure functions) ---

def test_score_stock_high_net_flow():
    stock = {"code": "600519", "name": "茅台", "net_amount": 6e8, "buy_amount": 8e8,
             "sell_amount": 2e8, "appearances": 5, "change_pct": 9.5}
    result = score_stock(stock)
    assert "score" in result
    assert "grade" in result
    assert result["score"] > 50
    assert result["grade"] in ["A", "B", "C", "D"]


def test_score_stock_low_net_flow():
    stock = {"code": "000001", "name": "X", "net_amount": 1e8, "buy_amount": 1e8,
             "sell_amount": 0.9e8, "appearances": 1, "change_pct": 1}
    result = score_stock(stock)
    assert result["score"] < 30
    assert result["grade"] == "D"


def test_score_stock_grade_thresholds():
    assert score_stock({"net_amount": 10e8, "buy_amount": 10e8, "sell_amount": 1e8, "appearances": 10, "change_pct": 15})["grade"] == "A"
    assert score_stock({"net_amount": 0.1e8, "buy_amount": 1e8, "sell_amount": 0.9e8, "appearances": 0, "change_pct": 0.5})["grade"] == "D"


def test_rank_top_stocks_aggregation():
    records = [
        {"code": "600519", "name": "茅台", "net_amount": 3e8, "buy_amount": 4e8, "sell_amount": 1e8, "appearances": 1, "change_pct": 7, "date": "2026-08-01", "reason": "涨幅偏离"},
        {"code": "600519", "name": "茅台", "net_amount": 2e8, "buy_amount": 3e8, "sell_amount": 1e8, "appearances": 1, "change_pct": 5, "date": "2026-08-02", "reason": "涨幅偏离"},
        {"code": "000858", "name": "五粮液", "net_amount": 1e8, "buy_amount": 1.5e8, "sell_amount": 0.5e8, "appearances": 1, "change_pct": 3, "date": "2026-08-01", "reason": "换手率"},
    ]
    ranked = rank_top_stocks(records, top_n=10)
    assert len(ranked) == 2
    # 茅台 should rank first (higher aggregate)
    assert ranked[0]["code"] == "600519"
    assert ranked[0]["appearances"] == 2


def test_rank_top_stocks_empty():
    assert rank_top_stocks([]) == []


def test_compute_stats():
    records = [
        {"code": "600519", "name": "茅台", "net_amount": 3e8, "date": "2026-08-01"},
        {"code": "000858", "name": "五粮液", "net_amount": 2e8, "date": "2026-08-01"},
        {"code": "600519", "name": "茅台", "net_amount": 1e8, "date": "2026-08-02"},
    ]
    stats = compute_stats(records)
    assert stats["total_records"] == 3
    assert stats["unique_stocks"] == 2
    assert stats["total_net_flow"] > 0
    assert len(stats["date_range"]) == 2


def test_compute_stats_empty():
    stats = compute_stats([])
    assert stats["total_records"] == 0


def test_rank_institutions():
    insts = [
        {"name": "东方财富", "appearances": 12, "success_rate": 48.2, "net_amount": 5e8},
        {"name": "华泰证券", "appearances": 8, "success_rate": 52.1, "net_amount": 3e8},
        {"name": "中信证券", "appearances": 5, "success_rate": 60.0, "net_amount": 2e8},
    ]
    ranked = rank_institutions(insts, top_n=2)
    assert len(ranked) == 2
    assert ranked[0]["name"] == "东方财富"


def test_rank_institutions_empty():
    assert rank_institutions([]) == []


# --- Orchestrator test ---

@pytest.mark.asyncio
async def test_dragon_tiger_orchestrator_structure():
    mock_records = [
        {"code": "600519", "name": "茅台", "net_amount": 3e8, "buy_amount": 4e8,
         "sell_amount": 1e8, "appearances": 1, "change_pct": 7, "date": "2026-08-01", "reason": "涨幅偏离"},
        {"code": "000858", "name": "五粮液", "net_amount": 2e8, "buy_amount": 2.5e8,
         "sell_amount": 0.5e8, "appearances": 1, "change_pct": 5, "date": "2026-08-01", "reason": "涨幅偏离"},
    ]
    mock_institutions = [
        {"name": "东方财富拉萨", "appearances": 12, "suance_rate": 48.2, "net_amount": 5e8},
    ]
    with patch("app.services.dragon_tiger_orchestrator.get_dragon_tiger_list", return_value=mock_records), \
         patch("app.services.dragon_tiger_orchestrator.get_dragon_tiger_institution", return_value=mock_institutions):
        report = await run_dragon_tiger_analysis(5, 1, _Context(_TypedLlm()), None)

    assert "period_days" in report
    assert "stats" in report
    assert "top_stocks" in report
    assert "institutions" in report
    assert "analysis" in report
    assert report["period_days"] == 5
    assert len(report["top_stocks"]) >= 1
