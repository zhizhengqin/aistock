"""AkShare compatibility adapter.

All synchronous SDK calls pass through ``ProviderLimiter.run_sync``.  In
particular, probes never call AkShare directly from an uvloop event loop.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

import pandas as pd
from pydantic import ValidationError

from app.datahub.contracts import (
    AuctionOpen,
    Capability,
    DataResult,
    DragonTigerItem,
    DragonTigerSeat,
    FinancialSummary,
    FundFlow,
    FundFlowRankItem,
    KlineBar,
    MarketIndex,
    NewsItem,
    SectorFlow,
    SectorQuote,
    ShareholderSummary,
    StockSnapshot,
)
from app.datahub.errors import DataHubError, DataHubErrorCode
from app.datahub.providers.base import ProviderAdapter, translate_provider_error
from app.datahub.validators import validate_payload


class AkshareProvider(ProviderAdapter):
    name = "akshare"

    def __init__(self, *, ak_module: Any | None = None, limiter=None) -> None:
        super().__init__(limiter=limiter)
        if ak_module is None:
            import akshare as ak_module  # type: ignore

        self.ak = ak_module

    async def fetch(self, capability: Capability | str, params: dict[str, Any]) -> DataResult:
        capability = Capability(capability)
        try:
            rows = await self._fetch_rows(capability, params)
            count = validate_payload(capability, rows)
            data_at = _data_time(rows, params)
            model = self._model_for(capability)
            typed = [model.model_validate(row) for row in rows] if model else rows
            singleton = capability in {
                Capability.STOCK_SNAPSHOT,
                Capability.STOCK_FINANCIALS,
                Capability.STOCK_FUND_FLOW,
                Capability.STOCK_SHAREHOLDERS,
            }
            return DataResult(
                data=typed[0] if singleton and typed else typed,
                capability=capability,
                provider=self.name,
                data_at=data_at,
                quality={"valid": True, "rows": count},
            )
        except DataHubError:
            raise
        except ValidationError:
            raise DataHubError(DataHubErrorCode.SCHEMA_CHANGED, "数据源字段发生变化", provider=self.name) from None
        except Exception as exc:
            raise translate_provider_error(exc, provider=self.name) from None

    async def _fetch_rows(self, capability: Capability, params: dict[str, Any]) -> list[dict[str, Any]]:
        if capability is Capability.MARKET_INDICES:
            # AkShare expects a category label, not the numeric index code.
            frame = await self.limiter.run_sync(
                self.name, lambda: self.ak.stock_zh_index_spot_em(symbol="上证系列指数")
            )
            return _frame_records(frame, {"代码": "code", "名称": "name", "最新价": "price", "涨跌幅": "change_pct"})

        code = params.get("code") or params.get("stock_code") or params.get("ts_code")
        if capability is Capability.STOCK_SNAPSHOT:
            info = await self.limiter.run_sync(self.name, lambda: self.ak.stock_individual_info_em(symbol=_plain_code(code)))
            rows = _frame_records(info)
            result: dict[str, Any] = {"code": code or "", "name": "", "price": 0, "change_pct": 0, "pe_ttm": None, "pb": None, "market_cap": None, "industry": ""}
            for row in rows:
                key, value = str(row.get("item", "")), row.get("value")
                if "简称" in key:
                    result["name"] = str(value)
                elif "行业" in key:
                    result["industry"] = str(value)
                elif "市值" in key:
                    result["market_cap"] = _number(value) / 1e8
                elif "市盈率" in key:
                    result["pe_ttm"] = _number(value)
                elif "市净率" in key:
                    result["pb"] = _number(value)
            spot = await self.limiter.run_sync(self.name, lambda: self.ak.stock_bid_ask_em(symbol=_plain_code(code)))
            spot_rows = _frame_records(spot)
            if spot_rows:
                result["price"] = _number(spot_rows[0].get("latest", spot_rows[0].get("最新价", 0)))
                result["change_pct"] = _number(spot_rows[0].get("change_pct", spot_rows[0].get("涨跌幅", 0)))
            return [result]

        if capability is Capability.STOCK_KLINE_DAILY:
            days = int(params.get("days", 120))
            frame = await self.limiter.run_sync(
                self.name,
                lambda: self.ak.stock_zh_a_hist(symbol=_plain_code(code), period="daily", start_date=params.get("start_date", ""), end_date=params.get("end_date", ""), adjust="qfq"),
            )
            records = _frame_records(frame, {"日期": "date", "开盘": "open", "收盘": "close", "最高": "high", "最低": "low", "成交量": "volume"})
            return records[-days:]

        if capability is Capability.STOCK_FINANCIALS:
            frame = await self.limiter.run_sync(self.name, lambda: self.ak.stock_financial_abstract(symbol=_plain_code(code)))
            rows = _frame_records(frame)
            latest = rows[0] if rows else {}
            return [{"code": code or "", "revenue": _number(latest.get("营业总收入")), "net_profit": _number(latest.get("净利润")), "roe": _number(latest.get("净资产收益率")), "gross_margin": _number(latest.get("销售毛利率")), "debt_ratio": _number(latest.get("资产负债率"))}]

        if capability is Capability.STOCK_FUND_FLOW:
            frame = await self.limiter.run_sync(self.name, lambda: self.ak.stock_individual_fund_flow(stock=_plain_code(code), market=""))
            rows = _frame_records(frame)
            return [{"code": code or "", "net_main_flow": sum(_number(r.get("主力净流入-净额")) for r in rows), "net_super_large": sum(_number(r.get("超大单净流入-净额")) for r in rows), "net_large": sum(_number(r.get("大单净流入-净额")) for r in rows), "net_medium": sum(_number(r.get("中单净流入-净额")) for r in rows), "net_small": sum(_number(r.get("小单净流入-净额")) for r in rows), "daily_flows": rows[-5:]}]

        if capability is Capability.STOCK_NEWS:
            frame = await self.limiter.run_sync(self.name, lambda: self.ak.stock_news_em(symbol=_plain_code(code)))
            return _frame_records(frame, {"新闻标题": "title", "新闻内容": "content", "发布时间": "date"})[: int(params.get("limit", 10))]

        if capability is Capability.MARKET_FUND_FLOW_RANK:
            frame = await self.limiter.run_sync(self.name, self.ak.stock_market_fund_flow)
            return _frame_records(frame, {"代码": "code", "名称": "name", "今日主力净流入-净额": "net_main_flow", "今日涨跌幅": "change_pct", "今日主力净流入-净占比": "net_main_pct"})[: int(params.get("limit", 50))]

        if capability is Capability.STOCK_SHAREHOLDERS:
            frame = await self.limiter.run_sync(self.name, lambda: self.ak.stock_zh_a_gdhs(symbol_em=_plain_code(code)))
            rows = _frame_records(frame)
            counts = [_number(row.get(next((key for key in row if "股东户数" in key or "人数" in key), ""))) for row in rows]
            return [{"code": code or "", "latest": int(counts[0]) if counts else None, "previous": int(counts[1]) if len(counts) > 1 else None, "change_pct": round((counts[0] - counts[1]) / counts[1] * 100, 2) if len(counts) > 1 and counts[1] else 0, "history": [int(value) for value in counts[:4]]}]

        if capability is Capability.SECTOR_REALTIME:
            frame = await self.limiter.run_sync(self.name, lambda: self.ak.index_realtime_sw(symbol="一级行业"))
            return _frame_records(frame, {"指数代码": "code", "指数名称": "name", "最新价": "price", "成交额": "turnover"})
        if capability is Capability.SECTOR_KLINE:
            frame = await self.limiter.run_sync(self.name, lambda: self.ak.index_hist_sw(symbol=code, period="day"))
            return _frame_records(
                frame,
                {"日期": "date", "开盘": "open", "收盘": "close", "最高": "high", "最低": "low", "成交量": "volume"},
            )[-int(params.get("days", 20)):]
        if capability is Capability.SECTOR_FUND_FLOW:
            frame = await self.limiter.run_sync(self.name, lambda: self.ak.stock_sector_fund_flow_rank(indicator="今日", sector_type="行业资金流"))
            return _frame_records(frame, {"名称": "name", "今日涨跌幅": "change_pct", "今日主力净流入-净额": "net_main_flow", "今日主力净流入-净占比": "net_main_pct"})
        if capability is Capability.DRAGON_TIGER_LIST:
            frame = await self.limiter.run_sync(self.name, lambda: self.ak.stock_lhb_detail_em(start_date=params.get("start_date", ""), end_date=params.get("end_date", "")))
            return _frame_records(frame, {"代码": "code", "名称": "name", "上榜日": "date", "解读": "reason", "收价": "close", "涨跌幅": "change_pct", "买入额": "buy_amount", "卖出额": "sell_amount", "净额": "net_amount"})
        if capability is Capability.DRAGON_TIGER_SEATS:
            frame = await self.limiter.run_sync(self.name, lambda: self.ak.stock_lhb_stock_detail_em(symbol=_plain_code(code) if code else ""))
            return _frame_records(frame, {"营业部名称": "name", "买入额": "buy_amount", "卖出额": "sell_amount", "净额": "net_amount", "上榜次数": "appearances", "最近上榜日": "last_date"})

        raise DataHubError(DataHubErrorCode.UNSUPPORTED, "AkShare 兼容适配器暂不支持该能力")

    @staticmethod
    def _model_for(capability: Capability):
        return {
            Capability.MARKET_INDICES: MarketIndex,
            Capability.STOCK_SNAPSHOT: StockSnapshot,
            Capability.STOCK_KLINE_DAILY: KlineBar,
            Capability.SECTOR_KLINE: KlineBar,
            Capability.STOCK_FINANCIALS: FinancialSummary,
            Capability.STOCK_FUND_FLOW: FundFlow,
            Capability.STOCK_NEWS: NewsItem,
            Capability.MARKET_FUND_FLOW_RANK: FundFlowRankItem,
            Capability.STOCK_SHAREHOLDERS: ShareholderSummary,
            Capability.SECTOR_REALTIME: SectorQuote,
            Capability.SECTOR_FUND_FLOW: SectorFlow,
            Capability.DRAGON_TIGER_LIST: DragonTigerItem,
            Capability.DRAGON_TIGER_SEATS: DragonTigerSeat,
            Capability.MARKET_AUCTION_OPEN: AuctionOpen,
        }.get(capability)


def _plain_code(value: Any) -> str:
    return str(value or "").split(".")[0]


def _number(value: Any) -> float:
    try:
        if value in (None, "", "--", "-", "None"):
            return 0.0
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return 0.0


def _frame_records(frame: Any, rename: dict[str, str] | None = None) -> list[dict[str, Any]]:
    if frame is None:
        return []
    if isinstance(frame, pd.DataFrame):
        frame = frame.rename(columns=rename or {})
        return frame.where(pd.notna(frame), None).to_dict("records")
    if isinstance(frame, list):
        return [dict(row) for row in frame]
    if isinstance(frame, dict):
        return [dict(frame)]
    return []


def _data_time(rows: list[dict[str, Any]], params: dict[str, Any]) -> datetime | None:
    values: list[datetime] = []
    for row in rows:
        for key, value in row.items():
            if not value or not any(marker in str(key).lower() for marker in ("date", "time", "日期", "时间", "报告期", "上榜日")):
                continue
            parsed = _parse_time(value)
            if parsed:
                values.append(parsed)
    for key in ("trade_date", "end_date", "date"):
        if params.get(key):
            parsed = _parse_time(params[key])
            if parsed:
                values.append(parsed)
    return max(values) if values else None


def _parse_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time(), tzinfo=timezone.utc)
    raw = str(value).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y%m%d", "%Y/%m/%d %H:%M:%S"):
        try:
            return datetime.strptime(raw[:len(datetime.now().strftime(fmt))], fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


__all__ = ["AkshareProvider"]
