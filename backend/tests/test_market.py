import pandas as pd
from unittest.mock import patch
from tests.conftest import client


def test_market_indices_route(client, fake_redis):
    mock_df = pd.DataFrame([
        {"代码": "000001", "名称": "上证指数", "最新价": 3200.5, "涨跌幅": 0.32},
        {"代码": "000300", "名称": "沪深300", "最新价": 3800.1, "涨跌幅": -0.12},
        {"代码": "399006", "名称": "创业板指", "最新价": 2100.0, "涨跌幅": 1.5},
    ])
    with patch("app.datasource.akshare_client.ak") as mock_ak:
        mock_ak.stock_zh_index_spot_em.return_value = mock_df
        resp = client.get("/api/stocks/market-indices")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert isinstance(data, list)
    assert len(data) == 5
    codes = [d["code"] for d in data]
    assert "000001" in codes


def test_market_indices_cache_hit(client, fake_redis):
    import json
    fake_redis.setex("cache:index:list", 60, '{"cached": true}')
    resp = client.get("/api/stocks/market-indices")
    assert resp.status_code == 200
    assert resp.json()["data"] == {"cached": True}


def test_sectors_overview_route(client, fake_redis):
    board_df = pd.DataFrame([{"板块名称": "银行", "涨跌幅": 1.2, "最新价": 100.0}])
    cons_df = pd.DataFrame([
        {"代码": "002142", "名称": "宁波银行", "最新价": 25.3, "涨跌幅": 1.1},
    ])
    with patch("app.datasource.akshare_client.ak") as mock_ak:
        mock_ak.stock_board_industry_name_em.return_value = board_df
        mock_ak.stock_board_industry_cons_em.return_value = cons_df
        resp = client.get("/api/stocks/sectors/overview?category=银行金融")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["category"] == "银行金融"
    assert "sectors" in data
    assert "stocks" in data


def test_sectors_overview_cached(client, fake_redis):
    import json
    payload = {"category": "周期资源", "cached": True, "sectors": [], "stocks": []}
    fake_redis.setex("cache:sector:周期资源:1月", 300, json.dumps(payload, ensure_ascii=False))
    resp = client.get("/api/stocks/sectors/overview?category=周期资源")
    assert resp.status_code == 200
    assert resp.json()["data"]["category"] == "周期资源"
