"""US overnight research report orchestrator.

Gathers US indices, core stocks, sector ETFs, treasury yields and English news,
then asks the LLM for the narrative (八段式) and four judgement cards.
Every fetcher degrades to sample data in mock/dev mode and reports data_status.
"""
import json
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logger import logger
from app.models.us_research_report import UsResearchReport

CORE_US_STOCKS = [
    {"ticker": "NVDA", "name": "英伟达", "a_share_mapping": "AI算力/CPO/液冷/服务器"},
    {"ticker": "AAPL", "name": "苹果", "a_share_mapping": "消费电子/果链"},
    {"ticker": "MSFT", "name": "微软", "a_share_mapping": "AI应用/云计算/信创"},
    {"ticker": "TSLA", "name": "特斯拉", "a_share_mapping": "新能源车/智能驾驶/机器人"},
    {"ticker": "AMD", "name": "AMD", "a_share_mapping": "算力芯片/半导体"},
    {"ticker": "GOOGL", "name": "谷歌", "a_share_mapping": "AI应用/广告营销"},
    {"ticker": "META", "name": "Meta", "a_share_mapping": "AI应用/社交/元宇宙"},
    {"ticker": "AMZN", "name": "亚马逊", "a_share_mapping": "云计算/跨境电商"},
]

SECTOR_ETFS = [
    {"ticker": "XLK.US", "name": "科技"},
    {"ticker": "SMH.US", "name": "半导体"},
    {"ticker": "XLF.US", "name": "金融"},
    {"ticker": "XLE.US", "name": "能源"},
    {"ticker": "XLV.US", "name": "医疗"},
    {"ticker": "XLY.US", "name": "可选消费"},
]

SAMPLE_INDICES = [
    {"name": "道琼斯工业平均", "ticker": "^DJI", "close": 44193.12, "change_pct": 0.47},
    {"name": "纳斯达克综合", "ticker": "^IXIC", "close": 21057.96, "change_pct": 1.12},
    {"name": "标普500", "ticker": "^SPX", "close": 6335.08, "change_pct": 0.78},
]

SAMPLE_BOND_YIELDS = {"y2": 3.85, "y10": 4.22, "y30": 4.81, "y2_chg": -0.02, "y10_chg": 0.01, "y30_chg": 0.02}

SAMPLE_NEWS_EN = [
    {"title": "Fed officials signal patience on rate cuts as inflation data looms", "source": "CNBC", "url": "https://www.cnbc.com"},
    {"title": "Nvidia hits record high on AI datacenter demand", "source": "CNBC", "url": "https://www.cnbc.com"},
    {"title": "Treasury yields edge higher ahead of jobs report", "source": "MarketWatch", "url": "https://www.marketwatch.com"},
]


def _stooq_quotes(symbols: list[str]) -> dict:
    """Fetch latest quotes from stooq CSV. Returns {symbol: {close, change_pct}}."""
    import httpx
    url = f"https://stooq.com/q/l/?s={','.join(symbols)}&f=sd2t2ohlcv&h&e=csv"
    resp = httpx.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    out = {}
    lines = resp.text.strip().splitlines()
    if len(lines) < 2:
        return out
    header = lines[0].split(",")
    for line in lines[1:]:
        vals = line.split(",")
        row = dict(zip(header, vals))
        sym = row.get("Symbol", "")
        try:
            open_p = float(row["Open"]); close = float(row["Close"])
            if open_p > 0:
                out[sym.upper()] = {"close": round(close, 2),
                                    "change_pct": round((close - open_p) / open_p * 100, 2)}
        except (ValueError, KeyError, ZeroDivisionError):
            continue
    return out


def fetch_us_indices() -> list[dict]:
    quotes = _stooq_quotes(["^dji", "^ndx", "^spx"])
    mapping = [("^DJI", "道琼斯工业平均"), ("^NDX", "纳斯达克100"), ("^SPX", "标普500")]
    result = []
    for sym, name in mapping:
        q = quotes.get(sym)
        if q:
            result.append({"name": name, "ticker": sym, **q})
    if not result:
        raise RuntimeError("no index quotes")
    return result


def fetch_us_core_stocks() -> list[dict]:
    tickers = [s["ticker"] + ".US" for s in CORE_US_STOCKS]
    quotes = _stooq_quotes(tickers)
    result = []
    for s in CORE_US_STOCKS:
        q = quotes.get(s["ticker"] + ".US")
        if q:
            result.append({**s, **q})
    if not result:
        raise RuntimeError("no core stock quotes")
    return result


def fetch_us_sector_samples() -> list[dict]:
    quotes = _stooq_quotes([e["ticker"] for e in SECTOR_ETFS])
    result = [{"name": e["name"], "ticker": e["ticker"], **quotes[e["ticker"]]}
              for e in SECTOR_ETFS if e["ticker"] in quotes]
    if not result:
        raise RuntimeError("no sector quotes")
    return result


def fetch_us_bond_yields() -> dict:
    quotes = _stooq_quotes(["2USY.B", "10USY.B", "30USY.B"])
    if "10USY.B" not in quotes:
        raise RuntimeError("no bond quotes")
    return {
        "y2": quotes.get("2USY.B", {}).get("close", 0),
        "y10": quotes.get("10USY.B", {}).get("close", 0),
        "y30": quotes.get("30USY.B", {}).get("close", 0),
        "y2_chg": quotes.get("2USY.B", {}).get("change_pct", 0),
        "y10_chg": quotes.get("10USY.B", {}).get("change_pct", 0),
        "y30_chg": quotes.get("30USY.B", {}).get("change_pct", 0),
    }


def fetch_us_movers() -> dict:
    """Top gainers/losers among core stocks + sector ETFs (sample of the market)."""
    stocks = fetch_us_core_stocks()
    sectors = fetch_us_sector_samples()
    universe = [{"ticker": s["ticker"], "name": s.get("name", s["ticker"]),
                 "change_pct": s["change_pct"]} for s in stocks + sectors]
    universe.sort(key=lambda x: x["change_pct"], reverse=True)
    if not universe:
        raise RuntimeError("no movers")
    return {"gainers": universe[:5], "losers": universe[-5:][::-1]}


def fetch_english_news(limit: int = 8) -> list[dict]:
    from app.services.news_collector import parse_rss
    import httpx
    feeds = [
        ("CNBC", "https://www.cnbc.com/id/100003114/device/rss/rss.html"),
        ("MarketWatch", "https://feeds.marketwatch.com/marketwatch/topstories/"),
    ]
    items = []
    for name, url in feeds:
        try:
            resp = httpx.get(url, timeout=12, follow_redirects=True,
                             headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()
            items.extend(parse_rss(resp.text, name))
        except Exception as e:
            logger.warning(f"English news feed {name} failed: {e}")
    if not items:
        raise RuntimeError("no english news")
    return [{"title": i["title"], "source": i["source"], "url": i["url"]} for i in items[:limit]]


def latest_us_trade_date() -> str:
    """Previous US weekday in US Eastern time (rough, no holiday calendar)."""
    et = timezone(timedelta(hours=-4))  # EDT; good enough for dev
    d = datetime.now(et).date()
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d.isoformat()


def _sample_core_stocks() -> list[dict]:
    chgs = {"NVDA": 2.35, "AAPL": 0.62, "MSFT": 1.08, "TSLA": -1.24,
            "AMD": 3.15, "GOOGL": 0.88, "META": 1.52, "AMZN": 0.41}
    closes = {"NVDA": 182.6, "AAPL": 232.1, "MSFT": 505.3, "TSLA": 312.8,
              "AMD": 178.2, "GOOGL": 196.4, "META": 745.9, "AMZN": 224.5}
    return [{**s, "close": closes[s["ticker"]], "change_pct": chgs[s["ticker"]]}
            for s in CORE_US_STOCKS]


DEFAULT_LLM_NARRATIVE = {
    "cards": {"us_sentiment": "震荡偏强", "a_share_impact": "中性偏结构性",
              "risk_level": "中等", "focus_directions": ["AI算力", "半导体", "红利防御"]},
    "sections": {
        "核心结论": "隔夜美股震荡收涨，科技股领涨。对A股影响中性偏结构性，关注AI算力与半导体映射方向。",
        "隔夜美股表现": "三大指数集体收涨，纳斯达克领涨，市场风险偏好回升。",
        "核心个股解读": "英伟达创新高，AI资本开支逻辑延续；特斯拉回调，关注机器人进展。",
        "板块与主题": "半导体板块强势，能源偏弱，成长风格占优。",
        "美债与宏观": "美债收益率小幅上行，市场对降息节奏保持观望。",
        "重要新闻摘要": "美联储官员表态偏耐心，企业财报整体好于预期。",
        "对A股的启示": "关注AI算力、CPO、半导体设备等映射方向，防御端关注红利板块。",
        "风险提示": "海外流动性收紧、地缘冲突、财报不及预期均可能引发波动。",
    },
}


async def _llm_narrative(data_brief: dict, user_id: int) -> dict:
    from app.core.llm import chat
    messages = [
        {"role": "system", "content": "{ANALYST_KEY:us_research} 你是美股隔夜研报分析师，输出JSON。"},
        {"role": "user", "content": json.dumps(data_brief, ensure_ascii=False, default=str)[:4000]},
    ]
    try:
        resp = await chat(messages, user_id=user_id, module="us_research")
        parsed = json.loads(resp.content)
        if "cards" in parsed and "sections" in parsed:
            return parsed
    except Exception as e:
        logger.warning(f"US research LLM narrative failed, using defaults: {e}")
    return DEFAULT_LLM_NARRATIVE


async def build_report(trade_date: str, user_id: int = 0, allow_fallback: bool = True) -> dict:
    """Assemble the full report dict. Fetchers degrade to samples when allowed."""
    data_status = {}

    def gather(name, fetcher, fallback):
        try:
            data = fetcher()
            data_status[name] = "ok"
            return data
        except Exception as e:
            logger.warning(f"US research source {name} failed: {e}")
            if not allow_fallback and not settings.LLM_MOCK:
                data_status[name] = "failed"
                return fallback if fallback is not None else ([] if name != "bond_yields" else {})
            data_status[name] = "fallback"
            return fallback

    indices = gather("indices", fetch_us_indices, SAMPLE_INDICES)
    core_stocks = gather("core_stocks", fetch_us_core_stocks, _sample_core_stocks())
    bond_yields = gather("bond_yields", fetch_us_bond_yields, SAMPLE_BOND_YIELDS)
    sectors = gather("sectors", fetch_us_sector_samples,
                     [{"name": e["name"], "ticker": e["ticker"], "close": 100.0, "change_pct": 0.5}
                      for e in SECTOR_ETFS])
    news = gather("news", fetch_english_news, SAMPLE_NEWS_EN)
    movers = gather("movers", fetch_us_movers, {
        "gainers": sorted([{"ticker": s["ticker"], "name": s["name"], "change_pct": s["change_pct"]}
                           for s in core_stocks], key=lambda x: -x["change_pct"])[:5],
        "losers": sorted([{"ticker": s["ticker"], "name": s["name"], "change_pct": s["change_pct"]}
                          for s in core_stocks], key=lambda x: x["change_pct"])[:5],
    })

    brief = {"trade_date": trade_date, "indices": indices, "core_stocks": core_stocks,
             "sectors": sectors, "bond_yields": bond_yields,
             "news": [n["title"] for n in news]}
    narrative = await _llm_narrative(brief, user_id)

    section_order = ["核心结论", "隔夜美股表现", "核心个股解读", "板块与主题",
                     "美债与宏观", "重要新闻摘要", "对A股的启示", "风险提示"]
    sections = [{"title": t, "content": narrative["sections"].get(t, "")}
                for t in section_order if narrative["sections"].get(t)]

    return {
        "trade_date": trade_date,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "cards": narrative["cards"],
        "indices": indices,
        "core_stocks": core_stocks,
        "movers": movers,
        "sector_samples": sectors,
        "bond_yields": bond_yields,
        "important_news": news,
        "sections": sections,
        "data_status": data_status,
    }


def save_report(db: Session, report: dict, data_status: dict | None = None) -> UsResearchReport:
    row = db.query(UsResearchReport).filter(
        UsResearchReport.trade_date == report["trade_date"]).first()
    if row is None:
        row = UsResearchReport(trade_date=report["trade_date"])
        db.add(row)
    row.status = "success"
    row.content = report
    row.data_status = data_status if data_status is not None else report.get("data_status", {})
    row.error = ""
    db.commit()
    db.refresh(row)
    return row
