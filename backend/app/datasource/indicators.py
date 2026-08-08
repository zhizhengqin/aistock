import pandas as pd
import numpy as np


def calc_ma(closes: pd.Series, periods=(5, 20, 60)) -> dict:
    result = {}
    for n in periods:
        if len(closes) >= n:
            result[f"MA{n}"] = round(float(closes.rolling(n).mean().iloc[-1]), 4)
        else:
            result[f"MA{n}"] = None
    return result


def calc_ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def calc_macd(closes: pd.Series, fast=12, slow=26, signal=9) -> dict:
    if len(closes) < slow:
        return {"DIF": None, "DEA": None, "MACD": None}
    ema_fast = calc_ema(closes, fast)
    ema_slow = calc_ema(closes, slow)
    dif = ema_fast - ema_slow
    dea = calc_ema(dif, signal)
    macd = (dif - dea) * 2
    return {
        "DIF": round(float(dif.iloc[-1]), 4),
        "DEA": round(float(dea.iloc[-1]), 4),
        "MACD": round(float(macd.iloc[-1]), 4),
    }


def calc_rsi(closes: pd.Series, period=14) -> dict:
    if len(closes) < period + 1:
        return {"RSI": None}
    delta = closes.diff()
    gains = delta.where(delta > 0, 0)
    losses = (-delta).where(delta < 0, 0)
    avg_gain = gains.rolling(period).mean()
    avg_loss = losses.rolling(period).mean()
    avg_loss_safe = avg_loss.replace(0, np.nan)
    rs = avg_gain / avg_loss_safe
    rsi = 100 - (100 / (1 + rs))
    rsi = rsi.fillna(100)  # avg_loss=0 (all gains) → RSI=100
    rsi = rsi.where(avg_gain > 0, 0)  # avg_gain=0 (all losses) → RSI=0
    return {"RSI": round(float(rsi.iloc[-1]), 2)}


def calc_kdj(highs: pd.Series, lows: pd.Series, closes: pd.Series, n=9) -> dict:
    if len(closes) < n:
        return {"K": None, "D": None, "J": None}
    low_n = lows.rolling(n).min()
    high_n = highs.rolling(n).max()
    rsv = (closes - low_n) / (high_n - low_n).replace(0, np.nan) * 100
    k = rsv.ewm(span=3, adjust=False).mean()
    d = k.ewm(span=3, adjust=False).mean()
    j = 3 * k - 2 * d
    return {
        "K": round(float(k.iloc[-1]), 2),
        "D": round(float(d.iloc[-1]), 2),
        "J": round(float(j.iloc[-1]), 2),
    }


def calc_boll(closes: pd.Series, period=20, std_mult=2) -> dict:
    if len(closes) < period:
        return {"UP": None, "MID": None, "LOW": None}
    mid = closes.rolling(period).mean()
    std = closes.rolling(period).std()
    up = mid + std_mult * std
    low = mid - std_mult * std
    return {
        "UP": round(float(up.iloc[-1]), 4),
        "MID": round(float(mid.iloc[-1]), 4),
        "LOW": round(float(low.iloc[-1]), 4),
    }


def compute_all(df: pd.DataFrame) -> dict:
    """Compute all indicators from an OHLCV DataFrame. Returns latest values."""
    closes = df["close"]
    highs = df["high"]
    lows = df["low"]
    return {
        "ma": calc_ma(closes),
        "macd": calc_macd(closes),
        "rsi": calc_rsi(closes),
        "kdj": calc_kdj(highs, lows, closes),
        "boll": calc_boll(closes),
    }
