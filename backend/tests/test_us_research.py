import pytest
from unittest.mock import patch
from app.services.us_research_orchestrator import (
    build_report, CORE_US_STOCKS, SAMPLE_INDICES, SAMPLE_BOND_YIELDS,
)
from app.models.us_research_report import UsResearchReport


def test_core_stocks_mapping():
    tickers = [s["ticker"] for s in CORE_US_STOCKS]
    for t in ["NVDA", "AAPL", "MSFT", "TSLA", "AMD", "GOOGL", "META", "AMZN"]:
        assert t in tickers
    for s in CORE_US_STOCKS:
        assert s["a_share_mapping"], f"{s['ticker']} missing a_share_mapping"


@pytest.mark.asyncio
async def test_build_report_structure():
    with patch("app.services.us_research_orchestrator.fetch_us_indices", return_value=SAMPLE_INDICES), \
         patch("app.services.us_research_orchestrator.fetch_us_core_stocks", return_value=[{**s, "change_pct": 1.5, "close": 100.0} for s in CORE_US_STOCKS]), \
         patch("app.services.us_research_orchestrator.fetch_us_bond_yields", return_value=SAMPLE_BOND_YIELDS), \
         patch("app.services.us_research_orchestrator.fetch_us_sector_samples", return_value=[{"name": "半导体", "change_pct": 2.1}]), \
         patch("app.services.us_research_orchestrator.fetch_english_news", return_value=[{"title": "Fed holds rates", "source": "CNBC", "url": "https://x.com"}]), \
         patch("app.services.us_research_orchestrator.fetch_us_movers", return_value={"gainers": [{"ticker": "XYZ", "change_pct": 9.9}], "losers": [{"ticker": "ABC", "change_pct": -8.8}]}):
        report = await build_report("2026-08-07", user_id=1)

    # 四个判断卡片
    cards = report["cards"]
    assert cards["us_sentiment"]
    assert cards["a_share_impact"]
    assert cards["risk_level"]
    assert isinstance(cards["focus_directions"], list) and len(cards["focus_directions"]) >= 1

    # 三大指数
    assert len(report["indices"]) >= 3

    # 核心美股 + A股映射
    assert len(report["core_stocks"]) == 8
    assert report["core_stocks"][0]["a_share_mapping"]

    # 涨跌幅榜
    assert report["movers"]["gainers"] and report["movers"]["losers"]

    # 美债收益率
    by = report["bond_yields"]
    assert "y2" in by and "y10" in by and "y30" in by

    # 重要新闻
    assert len(report["important_news"]) >= 1

    # 八段式章节
    assert len(report["sections"]) >= 6
    titles = [s["title"] for s in report["sections"]]
    assert "核心结论" in titles

    assert report["trade_date"] == "2026-08-07"


@pytest.mark.asyncio
async def test_build_report_fallback_on_source_failure():
    with patch("app.services.us_research_orchestrator.fetch_us_indices", side_effect=Exception("akshare down")), \
         patch("app.services.us_research_orchestrator.fetch_us_core_stocks", side_effect=Exception("down")), \
         patch("app.services.us_research_orchestrator.fetch_us_bond_yields", side_effect=Exception("down")), \
         patch("app.services.us_research_orchestrator.fetch_us_sector_samples", side_effect=Exception("down")), \
         patch("app.services.us_research_orchestrator.fetch_english_news", side_effect=Exception("down")), \
         patch("app.services.us_research_orchestrator.fetch_us_movers", side_effect=Exception("down")):
        report = await build_report("2026-08-07", user_id=1, allow_fallback=True)
    assert report["indices"]
    assert report["data_status"]["indices"] == "fallback"


def test_save_and_latest_report(test_db):
    engine, TestingSession = test_db
    db = TestingSession()
    from app.services.us_research_orchestrator import save_report
    report = {"trade_date": "2026-08-07", "cards": {"us_sentiment": "震荡"}, "sections": []}
    r1 = save_report(db, report, data_status={"indices": "ok"})
    assert r1.id is not None
    # upsert same trade_date
    r2 = save_report(db, {**report, "cards": {"us_sentiment": "上涨"}}, data_status={})
    assert r2.id == r1.id
    assert db.query(UsResearchReport).count() == 1
    db.close()


def test_us_research_api(auth_client, test_db):
    engine, TestingSession = test_db
    db = TestingSession()
    from app.services.us_research_orchestrator import save_report
    save_report(db, {"trade_date": "2026-08-07", "cards": {"us_sentiment": "震荡"},
                     "indices": [], "core_stocks": [], "sections": []}, data_status={})
    db.close()

    r = auth_client.get("/api/us-research/latest")
    assert r.status_code == 200
    assert r.json()["data"]["trade_date"] == "2026-08-07"

    r = auth_client.get("/api/us-research/history")
    assert r.status_code == 200
    assert len(r.json()["data"]) == 1


def test_us_research_api_empty(auth_client):
    r = auth_client.get("/api/us-research/latest")
    assert r.status_code == 200
    assert r.json()["data"] is None
