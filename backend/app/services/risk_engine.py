"""Pure-function risk engine for stock risk analysis.

No AI, no external calls — pure math for testability.
Covers: volatility anomaly, RSI overbought/oversold, recent high pullback.
Four-level grading: info / warning / danger / critical.
"""

import math


LEVEL_ORDER = {"info": 1, "warning": 2, "danger": 3, "critical": 4}
LEVEL_NAMES = {v: k for k, v in LEVEL_ORDER.items()}


def _max_level(levels: list[str]) -> str:
    """Return the highest severity level from a list."""
    if not levels:
        return "info"
    return max(levels, key=lambda l: LEVEL_ORDER.get(l, 0))


def check_volatility(kline_df, threshold: float = 0.03) -> dict | None:
    """Check if recent volatility (std of daily returns) exceeds threshold."""
    if kline_df is None or kline_df.empty or len(kline_df) < 10:
        return None
    closes = kline_df["close"].tail(20)
    returns = closes.pct_change().dropna()
    if len(returns) < 5:
        return None
    vol = float(returns.std())
    if vol > threshold:
        level = "warning" if vol < 0.06 else "danger"
        return {
            "category": "价格波动",
            "level": level,
            "message": f"近期波动率{vol:.2%}，高于阈值{threshold:.2%}",
            "value": f"{vol:.4f}",
        }
    return None


def check_rsi(rsi_value: float | None) -> dict | None:
    """Check RSI overbought (>70) or oversold (<30)."""
    if rsi_value is None:
        return None
    if rsi_value > 70:
        level = "warning" if rsi_value < 80 else "danger"
        return {
            "category": "技术信号",
            "level": level,
            "message": f"RSI超买({rsi_value:.1f})，注意回调风险",
            "value": f"{rsi_value:.1f}",
        }
    if rsi_value < 30:
        level = "warning" if rsi_value > 20 else "danger"
        return {
            "category": "技术信号",
            "level": level,
            "message": f"RSI超卖({rsi_value:.1f})，可能存在反弹机会",
            "value": f"{rsi_value:.1f}",
        }
    return None


def check_high_pullback(kline_df, pct_threshold: float = 0.15) -> dict | None:
    """Check if current price is near recent high (within pct_threshold of 60-day high)."""
    if kline_df is None or kline_df.empty or len(kline_df) < 20:
        return None
    closes = kline_df["close"].tail(60)
    recent_high = float(closes.max())
    current = float(closes.iloc[-1])
    if recent_high <= 0:
        return None
    pullback = (recent_high - current) / recent_high
    if pullback < pct_threshold:
        level = "info" if pullback < 0.05 else "warning"
        return {
            "category": "价格波动",
            "level": level,
            "message": f"价格处于近期高位，距离60日高点仅{pullback:.1%}，注意回调风险",
            "value": f"{pullback:.3f}",
        }
    return None


def analyze_stock_risk(kline_df, rsi_value: float | None = None) -> list[dict]:
    """Run all risk checks for a single stock, return list of warnings."""
    warnings = []
    v = check_volatility(kline_df)
    if v:
        warnings.append(v)
    r = check_rsi(rsi_value)
    if r:
        warnings.append(r)
    h = check_high_pullback(kline_df)
    if h:
        warnings.append(h)
    return warnings


def compute_portfolio_risk(holdings: list[dict]) -> dict:
    """Aggregate risk across all holdings.

    Each holding: {stock_code, stock_name, warnings: [...] }
    Returns: {total_warnings, max_level, composite_score, level_stats, warnings_detail}
    """
    all_warnings = []
    level_counts = {"info": 0, "warning": 0, "danger": 0, "critical": 0}

    for h in holdings:
        for w in h.get("warnings", []):
            entry = {
                **w,
                "stock_code": h.get("stock_code", ""),
                "stock_name": h.get("stock_name", ""),
            }
            all_warnings.append(entry)
            level = w.get("level", "info")
            if level in level_counts:
                level_counts[level] += 1

    total = len(all_warnings)
    levels = [w["level"] for w in all_warnings]
    max_level = _max_level(levels)

    # Composite score: 100 - weighted sum of warning levels
    if total == 0:
        composite_score = 100.0
    else:
        penalty = sum(LEVEL_ORDER.get(w["level"], 1) * 10 for w in all_warnings)
        composite_score = max(0.0, 100.0 - penalty)

    return {
        "total_warnings": total,
        "max_level": max_level,
        "composite_score": round(composite_score, 2),
        "level_stats": level_counts,
        "warnings_detail": all_warnings,
    }
