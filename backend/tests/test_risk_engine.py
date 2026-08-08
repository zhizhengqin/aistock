import pytest
import pandas as pd
from app.services.risk_engine import (
    check_volatility, check_rsi, check_high_pullback,
    analyze_stock_risk, compute_portfolio_risk,
)


def test_check_volatility_high():
    df = pd.DataFrame({"close": [100 + i * 0.05 + (i % 3) * 3 for i in range(25)]})
    result = check_volatility(df, threshold=0.01)
    assert result is not None
    assert result["category"] == "价格波动"
    assert result["level"] in ["warning", "danger"]


def test_check_volatility_low():
    df = pd.DataFrame({"close": [100.0 + i * 0.001 for i in range(25)]})
    result = check_volatility(df, threshold=0.03)
    assert result is None


def test_check_rsi_overbought():
    result = check_rsi(75.5)
    assert result is not None
    assert "超买" in result["message"]
    assert result["level"] == "warning"


def test_check_rsi_dangerous_overbought():
    result = check_rsi(85)
    assert result["level"] == "danger"


def test_check_rsi_oversold():
    result = check_rsi(25)
    assert result is not None
    assert "超卖" in result["message"]
    assert result["level"] == "warning"


def test_check_rsi_normal():
    assert check_rsi(50) is None
    assert check_rsi(None) is None


def test_check_high_pullback_at_high():
    df = pd.DataFrame({"close": [100 + i * 0.5 for i in range(60)]})
    result = check_high_pullback(df, pct_threshold=0.15)
    # current price IS the high: pullback=0 -> info-level warning
    assert result is not None
    assert result["level"] == "info"


def test_check_high_pullback_warning():
    # max=120 occurs mid-window, current=110, pullback=(120-110)/120=8.3% -> warning level
    prices = [100 + i * 0.4 for i in range(50)]  # up to 119.6
    prices += [120, 115, 112, 110]
    df = pd.DataFrame({"close": prices})
    result = check_high_pullback(df, pct_threshold=0.15)
    assert result is not None
    assert result["level"] == "warning"
    assert "高位" in result["message"]


def test_analyze_stock_risk_multiple():
    import numpy as np
    prices = list(np.linspace(100, 130, 45))
    prices += [132, 130, 80, 85, 82]
    df = pd.DataFrame({"close": prices[:50]})
    warnings = analyze_stock_risk(df, rsi_value=78)
    assert len(warnings) >= 1
    categories = {w["category"] for w in warnings}
    assert "技术信号" in categories


def test_compute_portfolio_risk_empty():
    result = compute_portfolio_risk([])
    assert result["total_warnings"] == 0
    assert result["composite_score"] == 100.0
    assert result["max_level"] == "info"


def test_compute_portfolio_risk_with_warnings():
    holdings = [
        {"stock_code": "600519", "stock_name": "茅台", "warnings": [
            {"category": "技术信号", "level": "warning", "message": "RSI超买(75.5)", "value": "75.5"},
        ]},
        {"stock_code": "000858", "stock_name": "五粮液", "warnings": [
            {"category": "价格波动", "level": "info", "message": "高位回调", "value": "0.05"},
            {"category": "价格波动", "level": "danger", "message": "波动率极高", "value": "0.08"},
        ]},
    ]
    result = compute_portfolio_risk(holdings)
    assert result["total_warnings"] == 3
    assert result["max_level"] == "danger"
    assert result["level_stats"]["warning"] == 1
    assert result["level_stats"]["info"] == 1
    assert result["level_stats"]["danger"] == 1
    assert result["composite_score"] < 100
