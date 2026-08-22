"""DataHub-backed financial news collector with dedupe and deterministic tags.

Sources: the configured DataHub RSS adapters.  Unsupported historical source
names are intentionally not called through an old SDK or a direct HTTP path.
Dedupe is by url_hash (sha1 of url, or of title when url is missing).
Tagging is ordinary keyword classification and never consumes model budget.
"""
import hashlib
import anyio
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from sqlalchemy.orm import Session

from app.core.logger import logger
from app.models.news_item import NewsItem
from app.datahub.consumer import get_global_news

NEWS_SOURCES = [
    {"name": "华尔街见闻", "source": "华尔街见闻"},
    {"name": "FT中文网", "source": "FT中文网"},
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
    """Keyword-based sentiment, industry and category classification."""
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
    result = anyio.run(get_global_news, source["source"], limit)
    items = []
    for row_model in result.data:
        row = row_model.model_dump(mode="json")
        title = str(row.get("title", ""))
        content = str(row.get("content", ""))
        items.append({
            "title": title.strip(),
            "url": str(row.get("url") or ""),
            "summary": content[:300],
            "source": source["name"],
            "published_at": _parse_date(str(row.get("date", ""))),
        })
    return [i for i in items if i["title"]]


def _tag_item(title: str, summary: str) -> dict:
    return rule_based_tag(title, summary)


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

    return stats


def collect_news(db: Session, sources: list[dict] | None = None) -> dict:
    """Legacy facade: fetch, persist and commit for non-runner callers."""
    candidates, errors = fetch_news_candidates(sources)
    stats = persist_news(db, candidates, errors)
    db.commit()
    return stats
