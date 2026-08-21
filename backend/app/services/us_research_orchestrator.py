"""US overnight research report orchestrator.

Gathers US indices, core stocks, sector ETFs, treasury yields and English news,
then asks the LLM for the narrative (八段式) and four judgement cards.
Failed fetchers return empty data with an explicit failed data_status; no
hard-coded market sample is used.
"""
import json
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.core.logger import logger
from app.models.us_research_report import UsResearchReport
from app.schemas.llm_outputs import UsResearchOutput

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


def _build_narrative_prompt(data_brief: dict) -> list[dict]:
    brief_text = json.dumps(data_brief, ensure_ascii=False, default=str)[:6000]
    system = """{ANALYST_KEY:us_research}
你是美股隔夜研报分析师。基于提供的真实数据输出严格 JSON，不得编造行情、收益率或新闻。
JSON 顶层只能包含 cards 与 sections。
cards 只能包含 us_sentiment、a_share_impact、risk_level、focus_directions（字符串数组）。
sections 必须且只能包含以下八个中文键：核心结论、隔夜美股表现、核心个股解读、板块与主题、美债与宏观、重要新闻摘要、对A股的启示、风险提示；每个值为非空字符串。"""
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": brief_text},
    ]


async def _set_progress(context, value: int) -> None:
    await context.set_progress(value)


async def _execute_step(context, step_key: str, messages: list[dict], output_type):
    await context.ensure_current()
    return await context.llm.execute_json(
        task_id=context.task_id,
        step_key=step_key,
        messages=messages,
        output_type=output_type,
        prompt_version=step_key,
    )


async def build_report(trade_date: str, user_id: int = 0, execution_ctx=None) -> dict:
    """Assemble a report from real data and one typed narrative step."""
    context = execution_ctx
    await _set_progress(context, 10)
    data_status = {}

    def gather(name, fetcher, empty):
        try:
            data = fetcher()
            data_status[name] = "ok"
            return data
        except Exception as e:
            logger.warning(f"US research source {name} failed: {e}")
            data_status[name] = "failed"
            return empty

    indices = gather("indices", fetch_us_indices, [])
    core_stocks = gather("core_stocks", fetch_us_core_stocks, [])
    bond_yields = gather("bond_yields", fetch_us_bond_yields, {})
    sectors = gather("sectors", fetch_us_sector_samples, [])
    news = gather("news", fetch_english_news, [])
    movers = gather("movers", fetch_us_movers, {"gainers": [], "losers": []})
    await _set_progress(context, 60)

    brief = {"trade_date": trade_date, "indices": indices, "core_stocks": core_stocks,
             "sectors": sectors, "bond_yields": bond_yields,
             "news": [n["title"] for n in news]}
    narrative_result = await _execute_step(
        context,
        "us_research.narrative.v1",
        _build_narrative_prompt(brief),
        UsResearchOutput,
    )
    narrative = narrative_result.model_dump(mode="json")

    section_order = ["核心结论", "隔夜美股表现", "核心个股解读", "板块与主题",
                     "美债与宏观", "重要新闻摘要", "对A股的启示", "风险提示"]
    sections = [{"title": t, "content": narrative["sections"].get(t, "")}
                for t in section_order if narrative["sections"].get(t)]

    await _set_progress(context, 90)
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
