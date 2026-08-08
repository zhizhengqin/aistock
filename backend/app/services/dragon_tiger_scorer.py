"""Pure-function scoring engine for dragon-tiger (lhb) data.

No AI, no external calls — pure math for testability.
"""


def _grade(score: float) -> str:
    if score >= 80:
        return "A"
    if score >= 60:
        return "B"
    if score >= 40:
        return "C"
    return "D"


def score_stock(stock: dict) -> dict:
    """Score a single dragon-tiger record.

    Dimensions (weighted sum -> 0-100):
      - net_amount: 40% (scaled by 10_YUVr: 5YUVr ~ 50 points)
      - appearances: 20% (max 5 appearances ~ 20 points)
      - buy_sell_ratio: 20% (ratio > 2 -> 20 points)
      - change_pct: 20% (abs(change_pct) > 10 -> 20 points)
    """
    net = abs(stock.get("net_amount", 0)) / 1e8
    appearances = min(stock.get("appearances", 1), 10)
    buy = stock.get("buy_amount", 0) / 1e8
    sell = stock.get("sell_amount", 0) / 1e8
    change_pct = abs(stock.get("change_pct", 0))

    net_score = min(net / 5, 1) * 40
    appear_score = appearances / 10 * 20
    ratio = (buy / sell) if sell > 0 else 2
    ratio_score = min(ratio / 2, 1) * 20
    change_score = min(change_pct / 10, 1) * 20

    total = round(net_score + appear_score + ratio_score + change_score, 1)

    return {
        **stock,
        "score": total,
        "grade": _grade(total),
        "buy_amount": buy,
        "sell_amount": sell,
    }


def rank_top_stocks(records: list[dict], top_n: int = 10) -> list[dict]:
    """Aggregate per stock, score, sort, return top-N with full detail."""
    if not records:
        return []

    # Group by stock code
    agg = {}
    for r in records:
        code = r.get("code", "")
        if not code:
            continue
        if code not in agg:
            agg[code] = {
                "code": code,
                "name": r.get("name", ""),
                "net_amount": 0,
                "buy_amount": 0,
                "sell_amount": 0,
                "appearances": 0,
                "change_pct": 0,
                "dates": [],
                "reasons": [],
            }
        a = agg[code]
        a["net_amount"] += r.get("net_amount", 0) * 1e8
        a["buy_amount"] += r.get("buy_amount", 0) * 1e8
        a["sell_amount"] += r.get("sell_amount", 0) * 1e8
        a["appearances"] += 1
        a["change_pct"] = max(a["change_pct"], abs(r.get("change_pct", 0)))
        if r.get("date"):
            a["dates"].append(r["date"])
        if r.get("reason"):
            a["reasons"].append(r["reason"])

    # Score each
    scored = [score_stock(s) for s in agg.values()]
    scored.sort(key=lambda x: x["score"], reverse=True)

    return scored[:top_n]


def compute_stats(records: list[dict]) -> dict:
    """Aggregate statistics from raw dragon-tiger records."""
    if not records:
        return {
            "total_records": 0,
            "sticky_stocks": 0,
            "total_net_flow": 0,
            "avg_net_flow": 0,
        }

    codes = set()
    dates = set()
    total_net = 0
    for r in records:
        codes.add(r.get("code", ""))
        if r.get("date"):
            dates.add(r["date"])
        total_net += r.get("net_amount", 0)

    return {
        "total_records": len(records),
        "unique_stocks": len(codes),
        "total_net_flow": round(total_net, 2),
        "avg_net_flow": round(total_net / max(len(records), 1), 2),
        "date_range": sorted(dates) if dates else [],
    }


def rank_institutions(institutions: list[dict], top_n: int = 10) -> list[dict]:
    """Rank hot-money institutions by appearances and net flow."""
    if not institutions:
        return []
    ranked = sorted(institutions, key=lambda x: x.get("appearances", 0), reverse=True)
    for inst in ranked:
        inst["success_rate"] = round(inst.get("success_rate", 0), 2)
    return ranked[:top_n]
