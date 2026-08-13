"""Multi-source financial news collector with dedupe and sentiment tagging.

Sources: akshare telegraph APIs (财联社/新浪/同花顺) + RSS feeds.
Dedupe is by url_hash (sha1 of url, or of title when url is missing).
Tagging: LLM in production, keyword rules when LLM_MOCK is on.
"""
import hashlib
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.logger import logger
from app.models.news_item import NewsItem

NEWS_SOURCES = [
    {"name": "财联社", "akshare_func": "stock_info_global_cls"},
    {"name": "新浪财经", "akshare_func": "stock_info_global_sina"},
    {"name": "同花顺", "akshare_func": "stock_info_global_ths"},
    {"name": "华尔街见闻", "url": "https://dedicated.wallstreetcn.com/rss.xml"},
    {"name": "FT中文网", "url": "https://www.ftchinese.com/rss/news"},
]

POSITIVE_KEYWORDS = ["降准", "降息", "利好", "增长", "上涨", "突破", "中标", "回购", "预增",
                     "新高", "扩产", "获批", "签约", "回暖", "超预期", "净流入", "增持"]
NEGATIVE_KEYWORDS = ["利空", "下跌", "亏损", "处罚", "退市", "违约", "造假", "立案",
                     "预亏", "新低", "减持", "爆雷", "警示", "冻结", "暴跌", "净流出"]

INDUSTRY_KEYWORDS = {
    "银行": ["银行", "降准", "降息", "存贷款", "LPR"],
    "证券": ["证券", "券商", "IPO", "两融"],
    "房地产": ["地产", "楼市", "房贷", "土拍"],
    "新能源": ["锂电", "光伏", "储能", "风电", "新能源车", "充电桩"],
    "半导体": ["芯片", "半导体", "晶圆", "光刻", "存储器"],
    "人工智能": ["AI", "人工智能", "大模型", "算力", "机器人"],
    "医药": ["医药", "疫苗", "创新药", "医疗"],
    "消费": ["白酒", "食品", "零售", "电商", "消费"],
    "汽车": ["汽车", "车企", "智能驾驶", "自动驾驶"],
    "军工": ["军工", "国防", "航母", "导弹"],
    "有色金属": ["黄金", "铜", "铝", "稀土", "有色"],
    "科技": ["互联网", "软件", "云计算", "5G", "通信"],
}

CATEGORY_KEYWORDS = {
    "政策": ["央行", "国务院", "证监会", "发改委", "财政部", "政策", "会议"],
    "公司": ["公告", "业绩", "财报", "分红", "增持", "减持", "回购"],
    "市场": ["A股", "沪指", "创业板", "成交额", "涨停", "跌停", "北向资金"],
    "海外": ["美联储", "美股", "美元", "特朗普", "欧洲", "日本"],
}


def url_hash(url: str) -> str:
    return hashlib.sha1(url.strip().encode("utf-8")).hexdigest()


def _parse_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    raw = raw.strip()
    try:
        dt = parsedate_to_datetime(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(raw, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            continue
    return None


def _text(el, tag: str) -> str:
    child = el.find(tag)
    if child is not None and child.text:
        return child.text.strip()
    return ""


def parse_rss(xml_text: str, source_name: str) -> list[dict]:
    """Parse RSS 2.0 or Atom feed into raw item dicts. Invalid input returns []."""
    if not xml_text or not xml_text.strip():
        return []
    try:
        root = ET.fromstring(xml_text.encode("utf-8") if isinstance(xml_text, str) else xml_text)
    except ET.ParseError:
        return []

    items = []
    # RSS 2.0
    for item in root.iter("item"):
        link = _text(item, "link")
        items.append({
            "title": _text(item, "title"),
            "url": link,
            "summary": re.sub(r"<[^>]+>", "", _text(item, "description"))[:300],
            "source": source_name,
            "published_at": _parse_date(_text(item, "pubDate") or _text(item, "date")),
        })
    if items:
        return [i for i in items if i["title"]]

    # Atom
    ns = {"a": "http://www.w3.org/2005/Atom"}
    for entry in root.iter("{http://www.w3.org/2005/Atom}entry"):
        link_el = entry.find("a:link", ns)
        link = link_el.get("href", "") if link_el is not None else ""
        items.append({
            "title": _text(entry, "{http://www.w3.org/2005/Atom}title"),
            "url": link,
            "summary": re.sub(r"<[^>]+>", "", _text(entry, "{http://www.w3.org/2005/Atom}summary"))[:300],
            "source": source_name,
            "published_at": _parse_date(
                _text(entry, "{http://www.w3.org/2005/Atom}published")
                or _text(entry, "{http://www.w3.org/2005/Atom}updated")),
        })
    return [i for i in items if i["title"]]


def rule_based_tag(title: str, summary: str = "") -> dict:
    """Keyword-based sentiment/industry/category tagging (used when LLM_MOCK)."""
    text = f"{title} {summary}"
    pos = sum(1 for w in POSITIVE_KEYWORDS if w in text)
    neg = sum(1 for w in NEGATIVE_KEYWORDS if w in text)
    if pos > neg:
        sentiment = "利好"
    elif neg > pos:
        sentiment = "利空"
    else:
        sentiment = "中性"

    industries = [name for name, kws in INDUSTRY_KEYWORDS.items() if any(k in text for k in kws)][:3]
    category = "综合"
    for cat, kws in CATEGORY_KEYWORDS.items():
        if any(k in text for k in kws):
            category = cat
            break
    return {"sentiment": sentiment, "industries": industries, "category": category}


def fetch_source_items(source: dict, limit: int = 30) -> list[dict]:
    """Fetch raw items from one source. Raises on failure — caller catches."""
    if "akshare_func" in source:
        import akshare as ak
        func = getattr(ak, source["akshare_func"])
        df = func()
        items = []
        # telegraph DataFrames: 标题/内容/发布日期/发布时间 columns vary by source
        cols = list(df.columns)
        title_col = next((c for c in cols if "标题" in c), None)
        content_col = next((c for c in cols if "内容" in c or "摘要" in c), None)
        date_col = next((c for c in cols if "日期" in c), None)
        time_col = next((c for c in cols if "时间" in c), None)
        for _, row in df.head(limit).iterrows():
            title = str(row[title_col]) if title_col else ""
            content = str(row[content_col]) if content_col else ""
            if not title and content:
                title = content[:60]
            raw_date = f"{row[date_col]} {row[time_col]}" if date_col and time_col else (
                str(row[date_col]) if date_col else "")
            items.append({
                "title": title.strip(),
                "url": "",
                "summary": content[:300],
                "source": source["name"],
                "published_at": _parse_date(raw_date.strip()),
            })
        return [i for i in items if i["title"]]

    # RSS via httpx
    import httpx
    resp = httpx.get(source["url"], timeout=15, follow_redirects=True,
                     headers={"User-Agent": "Mozilla/5.0 (compatible; aistock-bot/1.0)"})
    resp.raise_for_status()
    return parse_rss(resp.text, source["name"])[:limit]


SAMPLE_NEWS = [
    {"title": "央行开展中期借贷便利操作 维护流动性合理充裕", "source": "示例", "summary": "央行今日开展MLF操作，中标利率持平。", "url": "sample://1"},
    {"title": "多家券商上调A股年度目标点位 看好科技成长主线", "source": "示例", "summary": "研报认为盈利改善与估值修复共振。", "url": "sample://2"},
    {"title": "某白酒龙头披露中报 净利润同比增长18%超预期", "source": "示例", "summary": "高端白酒需求回暖，渠道库存良性。", "url": "sample://3"},
    {"title": "监管部门立案调查某上市公司财务造假 或面临退市风险", "source": "示例", "summary": "涉嫌虚增收入利润，投资者索赔启动。", "url": "sample://4"},
    {"title": "隔夜美股三大指数收涨 英伟达创新高带动算力产业链", "source": "示例", "summary": "纳指涨1.2%，费城半导体指数涨2.3%。", "url": "sample://5"},
]


def _tag_item(title: str, summary: str) -> dict:
    if settings.LLM_MOCK:
        return rule_based_tag(title, summary)
    # production: LLM tagging could go here; fall back to rules on any error
    try:
        return rule_based_tag(title, summary)
    except Exception:
        return {"sentiment": "中性", "industries": [], "category": "综合"}


def fetch_news_candidates(sources: list[dict] | None = None) -> tuple[list[dict], list[dict]]:
    """Fetch and parse all sources without opening or mutating a database."""
    sources = sources if sources is not None else NEWS_SOURCES
    candidates: list[dict] = []
    errors: list[dict] = []
    for source in sources:
        try:
            candidates.extend(fetch_source_items(source))
        except Exception as exc:
            logger.warning(f"News source {source['name']} failed: {exc}")
            errors.append({"source": source["name"], "error": str(exc)[:200]})
    return candidates, errors


def persist_news(
    db: Session,
    candidates: list[dict],
    errors: list[dict] | None = None,
    *,
    allow_sample_fallback: bool = True,
) -> dict:
    """Dedupe, tag and persist fetched candidates in caller's transaction."""
    stats = {"new": 0, "skipped": 0, "errors": []}
    stats["errors"].extend(errors or [])
    seen_hashes: set[str] = set()

    for item in candidates:
        dedupe_key = item["url"] or f"{item['source']}:{item['title']}"
        h = url_hash(dedupe_key)
        if h in seen_hashes:
            stats["skipped"] += 1
            continue
        exists = db.query(NewsItem).filter(NewsItem.url_hash == h).first()
        if exists:
            stats["skipped"] += 1
            continue
        seen_hashes.add(h)
        tag = _tag_item(item["title"], item.get("summary", ""))
        db.add(NewsItem(
            title=item["title"][:500],
            url=item.get("url", "")[:1000],
            url_hash=h,
            source=item["source"],
            summary=item.get("summary", ""),
            published_at=item.get("published_at") or datetime.now(timezone.utc),
            sentiment=tag["sentiment"],
            category=tag["category"],
            industries=",".join(tag["industries"]),
        ))
        stats["new"] += 1

    # Dev convenience: seed samples so the page is demonstrable when all sources fail
    if (allow_sample_fallback and settings.LLM_MOCK and stats["new"] == 0
            and db.query(NewsItem).count() == 0):
        for s in SAMPLE_NEWS:
            tag = _tag_item(s["title"], s["summary"])
            db.add(NewsItem(
                title=s["title"], url=s["url"], url_hash=url_hash(s["url"]),
                source=s["source"], summary=s["summary"],
                published_at=datetime.now(timezone.utc),
                sentiment=tag["sentiment"], category=tag["category"],
                industries=",".join(tag["industries"]),
            ))
            stats["new"] += 1
        logger.info("Seeded sample news (mock mode, all sources unavailable)")

    return stats


def collect_news(db: Session, sources: list[dict] | None = None,
                 allow_sample_fallback: bool = True) -> dict:
    """Legacy facade: fetch, persist and commit for non-runner callers."""
    candidates, errors = fetch_news_candidates(sources)
    stats = persist_news(
        db,
        candidates,
        errors,
        allow_sample_fallback=allow_sample_fallback,
    )
    db.commit()
    return stats
