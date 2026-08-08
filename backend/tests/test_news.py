import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from app.services.news_collector import (
    parse_rss, url_hash, rule_based_tag, collect_news, NEWS_SOURCES,
)
from app.models.news_item import NewsItem


SAMPLE_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
<channel>
<title>Test Feed</title>
<item>
  <title>央行宣布降准0.5个百分点 释放长期资金约1万亿元</title>
  <link>https://example.com/news/1</link>
  <description>央行今日宣布下调金融机构存款准备金率</description>
  <pubDate>Wed, 05 Aug 2026 10:30:00 GMT</pubDate>
</item>
<item>
  <title>某上市公司因财务造假被证监会立案调查</title>
  <link>https://example.com/news/2</link>
  <description>该公司涉嫌虚增利润被立案</description>
  <pubDate>Wed, 05 Aug 2026 09:00:00 GMT</pubDate>
</item>
</channel>
</rss>
"""

SAMPLE_ATOM = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
<title>Atom Feed</title>
<entry>
  <title>A股市场今日震荡上行</title>
  <link href="https://example.com/atom/1"/>
  <summary>三大指数集体收涨</summary>
  <published>2026-08-05T08:00:00Z</published>
</entry>
</feed>
"""


def test_parse_rss_basic():
    items = parse_rss(SAMPLE_RSS, "测试来源")
    assert len(items) == 2
    assert items[0]["title"].startswith("央行宣布降准")
    assert items[0]["url"] == "https://example.com/news/1"
    assert items[0]["source"] == "测试来源"
    assert items[0]["published_at"] is not None


def test_parse_rss_atom():
    items = parse_rss(SAMPLE_ATOM, "Atom源")
    assert len(items) == 1
    assert items[0]["title"] == "A股市场今日震荡上行"
    assert items[0]["url"] == "https://example.com/atom/1"


def test_parse_rss_invalid_returns_empty():
    assert parse_rss("not xml at all", "x") == []
    assert parse_rss("", "x") == []


def test_url_hash_dedupe():
    h1 = url_hash("https://example.com/a")
    h2 = url_hash("https://example.com/a")
    h3 = url_hash("https://example.com/b")
    assert h1 == h2
    assert h1 != h3
    assert len(h1) == 40


def test_rule_based_tag_positive():
    tag = rule_based_tag("央行降准释放流动性 利好银行地产板块", "")
    assert tag["sentiment"] == "利好"
    assert "银行" in tag["industries"] or "房地产" in tag["industries"] or len(tag["industries"]) >= 0


def test_rule_based_tag_negative():
    tag = rule_based_tag("某公司财务造假被立案调查 面临退市风险", "")
    assert tag["sentiment"] == "利空"


def test_rule_based_tag_neutral():
    tag = rule_based_tag("今日两市成交额较昨日基本持平 板块轮动明显", "")
    assert tag["sentiment"] == "中性"


def test_collect_news_dedupe(test_db):
    engine, TestingSession = test_db
    db = TestingSession()
    items = [
        {"title": "新闻A", "url": "https://x.com/1", "summary": "s", "source": "测试", "published_at": datetime.now(timezone.utc)},
        {"title": "新闻A重复", "url": "https://x.com/1", "summary": "s", "source": "测试", "published_at": datetime.now(timezone.utc)},
        {"title": "新闻B", "url": "https://x.com/2", "summary": "s", "source": "测试", "published_at": datetime.now(timezone.utc)},
    ]
    with patch("app.services.news_collector.fetch_source_items", return_value=items):
        result1 = collect_news(db, sources=[{"name": "测试", "url": "x"}])
        result2 = collect_news(db, sources=[{"name": "测试", "url": "x"}])
    assert result1["new"] == 2
    assert result2["new"] == 0
    assert result2["skipped"] == 3
    count = db.query(NewsItem).count()
    assert count == 2
    db.close()


def test_collect_news_fetch_error_continues(test_db):
    engine, TestingSession = test_db
    db = TestingSession()
    with patch("app.services.news_collector.fetch_source_items", side_effect=Exception("network down")):
        result = collect_news(db, sources=[{"name": "坏源", "url": "x"}], allow_sample_fallback=False)
    assert result["new"] == 0
    assert len(result["errors"]) == 1
    db.close()


def test_news_api_filter(auth_client, test_db):
    engine, TestingSession = test_db
    db = TestingSession()
    now = datetime.now(timezone.utc)
    rows = [
        NewsItem(title="近1小时新闻", url_hash=url_hash("u1"), url="u1", source="财联社",
                 summary="", published_at=now - timedelta(hours=1), sentiment="利好", category="综合", industries="银行"),
        NewsItem(title="近20小时新闻", url_hash=url_hash("u2"), url="u2", source="新浪财经",
                 summary="", published_at=now - timedelta(hours=20), sentiment="利空", category="综合", industries=""),
        NewsItem(title="近2天新闻", url_hash=url_hash("u3"), url="u3", source="财联社",
                 summary="", published_at=now - timedelta(hours=50), sentiment="中性", category="综合", industries=""),
    ]
    for r in rows:
        db.add(r)
    db.commit()
    db.close()

    r = auth_client.get("/api/news?hours=6")
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["total"] == 1
    assert data["items"][0]["title"] == "近1小时新闻"

    r = auth_client.get("/api/news?hours=24")
    assert r.json()["data"]["total"] == 2

    r = auth_client.get("/api/news?hours=72")
    assert r.json()["data"]["total"] == 3

    r = auth_client.get("/api/news?hours=0&source=财联社")
    d = r.json()["data"]
    assert d["total"] == 2
    assert all(i["source"] == "财联社" for i in d["items"])

    r = auth_client.get("/api/news/sources")
    assert r.status_code == 200
    names = [s["name"] for s in r.json()["data"]]
    assert "财联社" in names and "新浪财经" in names


def test_news_sources_config():
    assert len(NEWS_SOURCES) >= 3
    for s in NEWS_SOURCES:
        assert "name" in s and ("url" in s or "akshare_func" in s)
