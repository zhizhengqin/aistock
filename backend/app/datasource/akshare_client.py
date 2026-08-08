import pandas as pd
import akshare as ak
from app.datasource.cache import cache_get, cache_set, make_key
from app.core.logger import logger


MARKET_INDICES = [
    "000001",  # 上证指数
    "000300",  # 沪深300
    "000688",  # 科创50
    "399001",  # 深证成指
    "399006",  # 创业板指
]


def get_market_indices():
    cache_key = make_key("index", "list")
    cached = cache_get(cache_key)
    if cached is not None:
        return cached

    results = []
    for code in MARKET_INDICES:
        try:
            df = ak.stock_zh_index_spot_em(symbol="000001")
            row = df[df["代码"] == code].iloc[0]
            results.append({
                "code": code,
                "name": row.get("名称", ""),
                "price": float(row.get("最新价", 0)),
                "change_pct": float(row.get("涨跌幅", 0)),
            })
        except Exception as e:
            logger.warning(f"get_market_indices failed for {code}: {e}")
            results.append({"code": code, "name": "", "price": 0.0, "change_pct": 0.0})

    cache_set(cache_key, results, ttl=60)
    return results


SECTOR_CATEGORIES = {
    "银行金融": ["银行", "证券", "保险"],
    "科技互联网": ["计算机", "半导体", "通信"],
    "新能源": ["光伏", "锂电", "风电"],
    "大消费": ["食品饮料", "家电", "消费"],
    "高端制造": ["机械", "军工", "汽车"],
    "周期资源": ["煤炭", "有色", "钢铁"],
}


def get_sector_kline(category: str, period: str = "1月"):
    cache_key = make_key("sector", category, period)
    cached = cache_get(cache_key)
    if cached is not None:
        return cached

    sectors = SECTOR_CATEGORIES.get(category, [])
    kline_data = []
    for sec in sectors:
        try:
            df = ak.stock_board_industry_name_em()
            row = df[df["板块名称"] == sec]
            if not row.empty:
                r = row.iloc[0]
                kline_data.append({
                    "name": sec,
                    "change_pct": float(r.get("涨跌幅", 0)),
                    "price": float(r.get("最新价", 0)),
                })
        except Exception as e:
            logger.warning(f"get_sector_kline failed for {sec}: {e}")

    representative_stocks = _get_representative_stocks(sectors)

    result = {
        "category": category,
        "period": period,
        "sectors": kline_data,
        "stocks": representative_stocks,
        "updated_at": pd.Timestamp.now().isoformat(),
    }
    cache_set(cache_key, result, ttl=300)
    return result


def _get_representative_stocks(sectors: list) -> list:
    stocks = []
    try:
        for sec in sectors[:3]:
            try:
                df = ak.stock_board_industry_cons_em(symbol=sec)
                top = df.head(5)[["代码", "名称", "最新价", "涨跌幅"]].to_dict("records")
                for s in top:
                    stocks.append({
                        "code": s["代码"],
                        "name": s["名称"],
                        "price": float(s.get("最新价", 0)),
                        "change_pct": float(s.get("涨跌幅", 0)),
                    })
            except Exception as e:
                logger.warning(f"_get_representative_stocks failed for {sec}: {e}")
    except Exception as e:
        logger.warning(f"_get_representative_stocks outer: {e}")
    return stocks


def get_stock_info(code: str):
    cache_key = make_key("stock", code)
    cached = cache_get(cache_key)
    if cached is not None:
        return cached

    info = {"code": code, "name": "", "price": 0.0, "change_pct": 0.0,
            "pe_ttm": 0.0, "pb": 0.0, "market_cap": 0.0, "industry": ""}
    try:
        df = ak.stock_individual_info_em(symbol=code)
        for _, row in df.iterrows():
            key = row["item"]
            val = row["value"]
            if "股票简称" in key:
                info["name"] = val
            elif "行业" in key:
                info["industry"] = val
            elif "总市值" in key:
                info["market_cap"] = float(val) / 1e8 if val else 0
        spot = ak.stock_bid_ask_em(symbol=code)
        if not spot.empty:
            info["price"] = float(spot.iloc[0].get("latest", 0))
    except Exception as e:
        logger.warning(f"get_stock_info failed for {code}: {e}")

    cache_set(cache_key, info, ttl=30)
    return info


def get_stock_kline(code: str, days: int = 120) -> pd.DataFrame:
    """Get daily OHLCV kline data for a stock."""
    cache_key = make_key("kline", code, str(days))
    cached = cache_get(cache_key)
    if cached is not None:
        return pd.DataFrame(cached)
    try:
        end = pd.Timestamp.now().strftime("%Y%m%d")
        start = (pd.Timestamp.now() - pd.Timedelta(days=int(days * 1.8))).strftime("%Y%m%d")
        df = ak.stock_zh_a_hist(symbol=code, period="daily", start_date=start, end_date=end, adjust="qfq")
        df = df.rename(columns={
            "日期": "date", "开盘": "open", "收盘": "close",
            "最高": "high", "最低": "low", "成交量": "volume",
        })
        for col in ["open", "close", "high", "low", "volume"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.tail(days).reset_index(drop=True)
        cache_set(cache_key, df.to_dict("records"), ttl=300)
        return df
    except Exception as e:
        logger.warning(f"get_stock_kline failed for {code}: {e}")
        return pd.DataFrame()


def get_stock_financial_summary(code: str) -> dict:
    """Get financial summary for fundamental analysis."""
    cache_key = make_key("financial", code)
    cached = cache_get(cache_key)
    if cached is not None:
        return cached
    result = {
        "revenue": 0, "net_profit": 0, "roe": 0, "pe_ttm": 0,
        "pb": 0, "market_cap": 0, "gross_margin": 0, "debt_ratio": 0,
    }
    try:
        df = ak.stock_financial_abstract(symbol=code)
        if df is not None and not df.empty:
            latest = df.iloc[0]
            result["revenue"] = float(latest.get("营业总收入", 0) or 0)
            result["net_profit"] = float(latest.get("净利润", 0) or 0)
            result["roe"] = float(latest.get("净资产收益率", 0) or 0)
            result["gross_margin"] = float(latest.get("销售毛利率", 0) or 0)
            result["debt_ratio"] = float(latest.get("资产负债率", 0) or 0)
    except Exception as e:
        logger.warning(f"get_stock_financial_summary failed for {code}: {e}")

    try:
        info = ak.stock_individual_info_em(symbol=code)
        for _, row in info.iterrows():
            key = row["item"]
            val = row["value"]
            if "市盈率" in key:
                result["pe_ttm"] = float(val) if val else 0
            elif "市净率" in key:
                result["pb"] = float(val) if val else 0
            elif "总市值" in key:
                result["market_cap"] = float(val) / 1e8 if val else 0
    except Exception as e:
        logger.warning(f"get_stock_financial_summary info failed for {code}: {e}")

    cache_set(cache_key, result, ttl=3600)
    return result


def get_stock_capital_flow(code: str, days: int = 20) -> dict:
    """Get capital flow data for capital analysis."""
    cache_key = make_key("capital_flow", code, str(days))
    cached = cache_get(cache_key)
    if cached is not None:
        return cached
    result = {
        "net_main_flow": 0, "net_super_large": 0, "net_large": 0,
        "net_medium": 0, "net_small": 0, "daily_flows": [],
    }
    try:
        df = ak.stock_individual_fund_flow(stock=code, market="")
        if df is not None and not df.empty:
            df = df.tail(days)
            for col in ["主力净流入-净额", "超大单净流入-净额", "大单净流入-净额",
                        "中单净流入-净额", "小单净流入-净额"]:
                if col in df.columns:
                    vals = pd.to_numeric(df[col], errors="coerce")
                    key_map = {
                        "主力净流入-净额": "net_main_flow",
                        "超大单净流入-净额": "net_super_large",
                        "大单净流入-净额": "net_large",
                        "中单净流入-净额": "net_medium",
                        "小单净流入-净额": "net_small",
                    }
                    result[key_map[col]] = float(vals.sum())
            result["daily_flows"] = df.tail(5).to_dict("records")
    except Exception as e:
        logger.warning(f"get_stock_capital_flow failed for {code}: {e}")

    cache_set(cache_key, result, ttl=300)
    return result


def get_stock_news_titles(code: str, limit: int = 10) -> list[dict]:
    """Get recent news titles for a stock."""
    cache_key = make_key("news", code, str(limit))
    cached = cache_get(cache_key)
    if cached is not None:
        return cached
    result = []
    try:
        df = ak.stock_news_em(symbol=code)
        if df is not None and not df.empty:
            for _, row in df.head(limit).iterrows():
                result.append({
                    "title": str(row.get("新闻标题", "")),
                    "content": str(row.get("新闻内容", ""))[:200],
                    "date": str(row.get("发布时间", "")),
                })
    except Exception as e:
        logger.warning(f"get_stock_news_titles failed for {code}: {e}")

    cache_set(cache_key, result, ttl=1800)
    return result


# ---------------------------------------------------------------------------
# M3 data collectors: main-force selection, sector analysis, dragon-tiger
# ---------------------------------------------------------------------------


def get_market_capital_flow_rank(limit: int = 50) -> list[dict]:
    """Full-market capital flow ranking sorted by net main inflow."""
    cache_key = make_key("mkt_flow_rank", str(limit))
    cached = cache_get(cache_key)
    if cached is not None:
        return cached
    result = []
    try:
        df = ak.stock_market_fund_flow()
        if df is not None and not df.empty:
            col_map = {
                "代码": "code", "名称": "name", "今日主力净流入-净额": "net_main_flow",
                "今日涨跌幅": "change_pct", "今日主力净流入-净占比": "net_main_pct",
            }
            df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})
            if "net_main_flow" in df.columns:
                df = df.sort_values("net_main_flow", ascending=False).head(limit)
            for _, row in df.iterrows():
                result.append({
                    "code": str(row.get("code", "")),
                    "name": str(row.get("name", "")),
                    "net_main_flow": float(row.get("net_main_flow", 0) or 0),
                    "change_pct": float(row.get("change_pct", 0) or 0),
                    "net_main_pct": float(row.get("net_main_pct", 0) or 0),
                })
    except Exception as e:
        logger.warning(f"get_market_capital_flow_rank failed: {e}")
    cache_set(cache_key, result, ttl=300)
    return result


def get_stock_shareholder_count(code: str) -> dict:
    """Shareholder count history to detect concentration (decreasing = concentration)."""
    cache_key = make_key("gdhs", code)
    cached = cache_get(cache_key)
    if cached is not None:
        return cached
    result = {"latest": None, "previous": None, "change_pct": 0, "history": []}
    try:
        df = ak.stock_zh_a_gdhs(symbol_em=code)
        if df is not None and not df.empty:
            for col in df.columns:
                if "股东户数" in col or "人数" in col:
                    df = df.rename(columns={col: "count"})
                    break
            if "count" in df.columns:
                df["count"] = pd.to_numeric(df["count"], errors="coerce")
                df = df.sort_values("count", ascending=False)
                counts = df["count"].tolist()
                result["history"] = counts[:4]
                if len(counts) >= 2:
                    result["latest"] = int(counts[0])
                    result["previous"] = int(counts[1])
                    if counts[1] > 0:
                        result["change_pct"] = round((counts[0] - counts[1]) / counts[1] * 100, 2)
    except Exception as e:
        logger.warning(f"get_stock_shareholder_count failed for {code}: {e}")
    cache_set(cache_key, result, ttl=86400)
    return result


def get_sw_sector_list() -> list[dict]:
    """Shenwan (SW) sector index spot data."""
    cache_key = make_key("sw_sector_list")
    cached = cache_get(cache_key)
    if cached is not None:
        return cached
    result = []
    try:
        df = ak.sw_index_spot()
        if df is not None and not df.empty:
            for _, row in df.iterrows():
                result.append({
                    "code": str(row.get("板块代码", "")),
                    "name": str(row.get("板块名称", "")),
                    "change_pct": float(row.get("涨跌幅", 0) or 0),
                    "price": float(row.get("最新价", 0) or 0),
                    "turnover": float(row.get("成交额", 0) or 0),
                })
    except Exception as e:
        logger.warning(f"get_sw_sector_list failed: {e}")
    cache_set(cache_key, result, ttl=3600)
    return result


def get_sw_sector_detail(code: str, days: int = 20) -> list[dict]:
    """SW sector daily kline data."""
    cache_key = make_key("sw_sector", code, str(days))
    cached = cache_get(cache_key)
    if cached is not None:
        return cached
    result = []
    try:
        end = pd.Timestamp.now().strftime("%Y%m%d")
        start = (pd.Timestamp.now() - pd.Timedelta(days=int(days * 2))).strftime("%Y%m%d")
        df = ak.sw_index_daily(symbol=code, start_date=start, end_date=end)
        if df is not None and not df.empty:
            df = df.tail(days)
            result = df.to_dict("records")
    except Exception as e:
        logger.warning(f"get_sw_sector_detail failed for {code}: {e}")
    cache_set(cache_key, result, ttl=1800)
    return result


def get_sector_capital_flow() -> list[dict]:
    """Sector-level capital flow ranking."""
    cache_key = make_key("sector_flow")
    cached = cache_get(cache_key)
    if cached is not None:
        return cached
    result = []
    try:
        df = ak.stock_sector_fund_flow_rank(indicator="今日", sector_type="行业资金流")
        if df is not None and not df.empty:
            col_map = {
                "名称": "name", "今日涨跌幅": "change_pct",
                "今日主力净流入-净额": "net_main_flow", "今日主力净流入-净占比": "net_main_pct",
            }
            df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})
            if "net_main_flow" in df.columns:
                df = df.sort_values("net_main_flow", ascending=False)
            for _, row in df.iterrows():
                result.append({
                    "name": str(row.get("name", "")),
                    "change_pct": float(row.get("change_pct", 0) or 0),
                    "net_main_flow": float(row.get("net_main_flow", 0) or 0),
                    "net_main_pct": float(row.get("net_main_pct", 0) or 0),
                })
    except Exception as e:
        logger.warning(f"get_sector_capital_flow failed: {e}")
    cache_set(cache_key, result, ttl=300)
    return result


def get_dragon_tiger_list(days: int = 5) -> list[dict]:
    """Dragon-tiger (lhb) detailed records for recent N days."""
    cache_key = make_key("dragon_tiger", str(days))
    cached = cache_get(cache_key)
    if cached is not None:
        return cached
    result = []
    try:
        end = pd.Timestamp.now().strftime("%Y%m%d")
        start = (pd.Timestamp.now() - pd.Timedelta(days=days)).strftime("%Y%m%d")
        df = ak.stock_lhb_detail_em(start_date=start, end_date=end)
        if df is not None and not df.empty:
            col_map = {
                "代码": "code", "名称": "name", "上榜日": "date",
                "解读": "reason", "收价": "close", "涨跌幅": "change_pct",
                "买入额": "buy_amount", "卖出额": "sell_amount", "净额": "net_amount",
            }
            df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})
            for _, row in df.iterrows():
                result.append({
                    "code": str(row.get("code", "")),
                    "name": str(row.get("name", "")),
                    "date": str(row.get("date", "")),
                    "reason": str(row.get("reason", "")),
                    "close": float(row.get("close", 0) or 0),
                    "change_pct": float(row.get("change_pct", 0) or 0),
                    "buy_amount": float(row.get("buy_amount", 0) or 0) / 1e8,
                    "sell_amount": float(row.get("sell_amount", 0) or 0) / 1e8,
                    "net_amount": float(row.get("net_amount", 0) or 0) / 1e8,
                })
    except Exception as e:
        logger.warning(f"get_dragon_tiger_list failed: {e}")
    cache_set(cache_key, result, ttl=1800)
    return result


def get_dragon_tiger_institution(code: str = None) -> list[dict]:
    """Dragon-tiger institution (broker) seat detail for a single stock."""
    cache_key = make_key("dragon_tiger_inst", code or "all")
    cached = cache_get(cache_key)
    if cached is not None:
        return cached
    result = []
    try:
        if code:
            df = ak.stock_lhb_stock_detail_em(symbol=code)
        else:
            df = ak.stock_lhb_stock_detail_em(symbol="")
        if df is not None and not df.empty:
            col_map = {
                "营业部名称": "name", "买入额": "buy_amount", "卖出额": "sell_amount",
                "净额": "net_amount", "上榜次数": "appearances", "最近上榜日": "last_date",
            }
            df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})
            for _, row in df.iterrows():
                result.append({
                    "name": str(row.get("name", "")),
                    "buy_amount": float(row.get("buy_amount", 0) or 0) / 1e8,
                    "sell_amount": float(row.get("sell_amount", 0) or 0) / 1e8,
                    "net_amount": float(row.get("net_amount", 0) or 0) / 1e8,
                    "appearances": int(row.get("appearances", 0) or 0),
                    "last_date": str(row.get("last_date", "")),
                })
    except Exception as e:
        logger.warning(f"get_dragon_tiger_institution failed: {e}")
    cache_set(cache_key, result, ttl=1800)
    return result
