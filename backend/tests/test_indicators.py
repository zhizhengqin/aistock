import pandas as pd
import numpy as np
from app.datasource.indicators import (
    calc_ma, calc_macd, calc_rsi, calc_kdj, calc_boll, compute_all,
)


def _make_df(n=80):
    np.random.seed(42)
    base = 100 + np.cumsum(np.random.randn(n) * 0.5)
    return pd.DataFrame({
        "open": base - 0.2,
        "high": base + 1,
        "low": base - 1,
        "close": base,
        "volume": np.random.randint(1000, 5000, n),
    })


def test_ma_basic():
    closes = pd.Series([1, 2, 3, 4, 5], dtype=float)
    r = calc_ma(closes, periods=(5,))
    assert r["MA5"] == 3.0


def test_ma_insufficient_data():
    closes = pd.Series([1, 2, 3], dtype=float)
    r = calc_ma(closes, periods=(20, 60))
    assert r["MA20"] is None
    assert r["MA60"] is None


def test_macd_returns_three_values():
    df = _make_df()
    r = calc_macd(df["close"])
    assert "DIF" in r and "DEA" in r and "MACD" in r
    assert all(isinstance(v, float) for v in r.values())


def test_macd_insufficient_data():
    closes = pd.Series([1, 2, 3, 4, 5], dtype=float)
    r = calc_macd(closes)
    assert r["DIF"] is None


def test_rsi_range():
    df = _make_df()
    r = calc_rsi(df["close"])
    assert 0 <= r["RSI"] <= 100


def test_rsi_all_gains():
    closes = pd.Series(range(10, 30), dtype=float)
    r = calc_rsi(closes)
    assert r["RSI"] == 100.0


def test_rsi_all_losses():
    closes = pd.Series(range(30, 10, -1), dtype=float)
    r = calc_rsi(closes)
    assert r["RSI"] == 0.0


def test_kdj_returns_kdj():
    df = _make_df()
    r = calc_kdj(df["high"], df["low"], df["close"])
    assert "K" in r and "D" in r and "J" in r
    assert all(isinstance(v, float) for v in r.values())
    assert abs(r["J"] - (3 * r["K"] - 2 * r["D"])) < 0.5


def test_boll_upper_above_mid_above_lower():
    df = _make_df()
    r = calc_boll(df["close"])
    assert r["UP"] > r["MID"] > r["LOW"]


def test_boll_insufficient_data():
    closes = pd.Series([1, 2, 3, 4, 5], dtype=float)
    r = calc_boll(closes)
    assert r["UP"] is None


def test_compute_all_returns_dict():
    df = _make_df()
    r = compute_all(df)
    assert set(r.keys()) == {"ma", "macd", "rsi", "kdj", "boll"}
    assert r["ma"]["MA5"] is not None
    assert r["rsi"]["RSI"] is not None
    assert r["boll"]["UP"] is not None
