"""Direct Eastmoney JSON adapter for sector/fund-flow/LHB capabilities."""

from __future__ import annotations

from datetime import date, datetime, timezone
import json
import re
from typing import Any

from app.datahub.contracts import (
    BoardConstituent,
    BoardQuote,
    Capability,
    DataResult,
    DragonTigerItem,
    DragonTigerSeat,
    FundFlow,
    FundFlowRankItem,
    KlineBar,
    NewsItem,
    SectorFlow,
    SectorQuote,
    StockProfile,
    ShareholderSummary,
)
from app.datahub.errors import DataHubError, DataHubErrorCode
from app.datahub.providers.base import ProviderAdapter, translate_provider_error
from app.datahub.providers.ticker import normalise_ticker
from app.datahub.validators import validate_payload


class EastmoneyProvider(ProviderAdapter):
    name = "eastmoney"
    BASE_URL = "https://push2.eastmoney.com/api/qt/clist/get"

    _SUPPORTED = {
        Capability.MARKET_BOARD_QUOTES,
        Capability.MARKET_BOARD_CONSTITUENTS,
        Capability.STOCK_FUND_FLOW,
        Capability.STOCK_PROFILE,
        Capability.MARKET_FUND_FLOW_RANK,
        Capability.STOCK_SHAREHOLDERS,
        Capability.SECTOR_REALTIME,
        Capability.SECTOR_FUND_FLOW,
        Capability.STOCK_KLINE_DAILY,
        Capability.DRAGON_TIGER_LIST,
        Capability.DRAGON_TIGER_SEATS,
        Capability.STOCK_NEWS,
        Capability.SECTOR_KLINE,
    }

    def __init__(self, *, http_client: Any | None = None, limiter=None) -> None:
        super().__init__(limiter=limiter)
        self.http_client = http_client

    async def fetch(self, capability: Capability | str, params: dict[str, Any]) -> DataResult:
        capability = Capability(capability)
        if capability not in self._SUPPORTED:
            raise DataHubError(DataHubErrorCode.UNSUPPORTED, "东方财富直连接口暂不支持该数据能力", provider=self.name)
        try:
            payload = await self.run_sync(lambda: self._get_json(capability, params))
            rows = _rows(payload, capability)
            typed = _typed_rows(capability, rows, params)
            count = validate_payload(capability, typed)
            data_at = _data_time(typed, params)
            if data_at is None:
                raise DataHubError(DataHubErrorCode.STALE_INVALID, "东方财富响应缺少可信时间戳", provider=self.name)
            if capability is Capability.STOCK_FUND_FLOW:
                aggregate = _aggregate_fund_flow(typed)
                return DataResult(
                    data=aggregate,
                    capability=capability,
                    provider=self.name,
                    data_at=data_at,
                    quality={"valid": True, "rows": count, "missing_fields": _missing_flow_fields(aggregate)},
                )
            if capability is Capability.STOCK_SHAREHOLDERS:
                aggregate = _aggregate_shareholders(typed)
                return DataResult(data=aggregate, capability=capability, provider=self.name, data_at=data_at, quality={"valid": True, "rows": count})
            return DataResult(
                data=typed[0] if capability is Capability.STOCK_PROFILE and typed else typed,
                capability=capability,
                provider=self.name,
                data_at=data_at,
                quality={"valid": True, "rows": count},
            )
        except DataHubError:
            raise
        except Exception as exc:
            raise translate_provider_error(exc, provider=self.name) from None

    def _get_json(self, capability: Capability, params: dict[str, Any]) -> Any:
        client = self.http_client
        close = False
        if client is None:
            import httpx

            client = httpx.Client()
            close = True
        if callable(client):
            try:
                return client(capability, params)
            finally:
                if close and hasattr(client, "close"):
                    client.close()
        try:
            url, query = _endpoint(capability, params)
            try:
                response = client.get(url, params=query, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"}, timeout=10)
            except TypeError:
                # Tiny fixture clients often expose only ``get(url, params, timeout)``;
                # production HTTP clients still receive the anti-bot headers above.
                response = client.get(url, params=query, timeout=10)
            if hasattr(response, "raise_for_status"):
                response.raise_for_status()
            if capability is Capability.STOCK_NEWS:
                text = str(getattr(response, "text", ""))
                return _parse_jsonp(text)
            return response.json() if hasattr(response, "json") else response
        finally:
            if close and hasattr(client, "close"):
                client.close()


def _rows(payload: Any, capability: Capability | None = None) -> list[dict[str, Any]]:
    if isinstance(payload, dict) and "result" in payload and isinstance(payload.get("result"), dict):
        news_rows = payload["result"].get("cmsArticleWebOld")
        if isinstance(news_rows, list):
            return [dict(item) for item in news_rows if isinstance(item, dict)]
        result_rows = payload["result"].get("data")
        if isinstance(result_rows, list):
            return [dict(item) for item in result_rows if isinstance(item, dict)]
    if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
        data = payload["data"]
        lines = data.get("klines") or data.get("dayks")
        if isinstance(lines, list):
            parsed: list[dict[str, Any]] = []
            for line in lines:
                if isinstance(line, str):
                    parts = line.split(",")
                    if len(parts) >= 6 and capability is Capability.STOCK_FUND_FLOW:
                        parsed.append({"date": parts[0], "net_main_flow": parts[1], "net_small": parts[2], "net_medium": parts[3], "net_large": parts[4], "net_super_large": parts[5], "data_at": parts[0]})
                    elif len(parts) >= 6:
                        parsed.append({"date": parts[0], "open": parts[1], "close": parts[2], "high": parts[3], "low": parts[4], "volume": parts[5], "data_at": parts[0]})
                elif isinstance(line, dict):
                    parsed.append(dict(line))
            return parsed
    if isinstance(payload, list):
        return [dict(item) for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        data = payload.get("data", payload)
        diff = data.get("diff") if isinstance(data, dict) else None
        if isinstance(diff, dict):
            return [dict(item) for item in diff.values() if isinstance(item, dict)]
        if isinstance(diff, list):
            return [dict(item) for item in diff if isinstance(item, dict)]
        if isinstance(data, dict):
            return [data]
    return []


def _typed_rows(capability: Capability, rows: list[dict[str, Any]], params: dict[str, Any]) -> list[Any]:
    output = []
    for row in rows:
        code = str(row.get("code") or row.get("f12") or row.get("SECUCODE") or params.get("code") or "")
        name = str(row.get("name") or row.get("f14") or row.get("SECURITY_NAME_ABBR") or code)
        change = _num(row.get("change_pct", row.get("f3", row.get("PCT_CHANGE", 0))))
        flow = _num(row.get("net_main_flow", row.get("f62", row.get("MAIN_NET_INFLOW", 0))))
        row_time = _row_time(row)
        if capability is Capability.MARKET_BOARD_QUOTES:
            kind = _validated_kind(params.get("kind"))
            data_at = row_time
            if data_at is None:
                continue
            output.append(
                BoardQuote(
                    board_code=str(row.get("board_code") or row.get("f12") or "").strip(),
                    board_name=str(row.get("board_name") or row.get("f14") or "").strip(),
                    kind=kind,
                    change_pct=_optional_num(row.get("change_pct", row.get("f3"))),
                    turnover=_optional_num(row.get("turnover", row.get("f6"))),
                    market_cap=_optional_num(row.get("market_cap", row.get("f20"))),
                    rise_count=_optional_int(row.get("rise_count", row.get("f104"))),
                    fall_count=_optional_int(row.get("fall_count", row.get("f105"))),
                    flat_count=_optional_int(row.get("flat_count", row.get("f106"))),
                    leader_code=_optional_board_leader_code(row.get("leader_code", row.get("f140"))),
                    leader_name=_optional_text(row.get("leader_name", row.get("f128"))),
                    leader_change_pct=_optional_num(row.get("leader_change_pct", row.get("f136"))),
                    data_at=data_at,
                )
            )
        elif capability is Capability.MARKET_BOARD_CONSTITUENTS:
            kind = _validated_kind(params.get("kind"))
            data_at = row_time
            if data_at is None:
                continue
            raw_code = row.get("code") or row.get("f12") or ""
            try:
                normalised_code = normalise_ticker(str(raw_code))
            except DataHubError:
                continue
            output.append(
                BoardConstituent(
                    code=normalised_code,
                    name=str(row.get("name") or row.get("f14") or normalised_code),
                    price=_optional_num(row.get("price", row.get("f2"))),
                    change_pct=_optional_num(row.get("change_pct", row.get("f3"))),
                    turnover=_optional_num(row.get("turnover", row.get("f6"))),
                    market_cap=_optional_num(row.get("market_cap", row.get("f20"))),
                    data_at=data_at,
                )
            )
        elif capability is Capability.SECTOR_REALTIME:
            output.append(SectorQuote(code=code, name=name, change_pct=change, price=_num(row.get("f2", row.get("price", 0))), turnover=_num(row.get("f6", row.get("turnover", 0))), data_at=row_time))
        elif capability is Capability.SECTOR_FUND_FLOW:
            output.append(SectorFlow(name=name, change_pct=change, net_main_flow=flow, net_main_pct=_num(row.get("f184", row.get("net_main_pct", 0))), data_at=row_time))
        elif capability is Capability.MARKET_FUND_FLOW_RANK:
            output.append(FundFlowRankItem(code=code, name=name, net_main_flow=flow, change_pct=change, net_main_pct=_num(row.get("f184", row.get("net_main_pct", 0))), data_at=row_time))
        elif capability is Capability.STOCK_FUND_FLOW:
            output.append(
                FundFlow(
                    code=code,
                    net_main_flow=_optional_num(row.get("net_main_flow", row.get("f62"))),
                    net_super_large=_optional_num(row.get("net_super_large", row.get("f66"))),
                    net_large=_optional_num(row.get("net_large", row.get("f72"))),
                    net_medium=_optional_num(row.get("net_medium", row.get("f78"))),
                    net_small=_optional_num(row.get("net_small", row.get("f84"))),
                    daily_flows=[row],
                    data_at=row_time,
                )
            )
        elif capability is Capability.STOCK_PROFILE:
            output.append(
                StockProfile(
                    code=normalise_ticker(row.get("code") or row.get("f57") or params.get("code")),
                    name=str(row.get("name") or row.get("f58") or code),
                    industry=_optional_text(row.get("industry", row.get("f127"))),
                    data_at=row_time,
                )
            )
        elif capability is Capability.STOCK_SHAREHOLDERS:
            latest = _int(row.get("latest", row.get("HOLDER_NUM", row.get("f2"))))
            previous = _int(row.get("previous", row.get("HOLDER_NUM_PREV")))
            ratio = _num(row.get("HOLDER_NUM_RATIO", row.get("change_pct", 0)))
            output.append(ShareholderSummary(code=code, latest=latest, previous=previous, change_pct=ratio or (round((latest - previous) / previous * 100, 2) if previous else 0), history=[value for value in (latest, previous) if value is not None], data_at=row_time))
        elif capability is Capability.DRAGON_TIGER_LIST:
            output.append(DragonTigerItem(code=code, name=name, date=row.get("date") or row.get("TRADE_DATE") or params.get("trade_date"), reason=str(row.get("reason") or row.get("EXPLANATION") or ""), close=_num(row.get("close", row.get("CLOSE_PRICE", 0))), change_pct=change, buy_amount=_num(row.get("buy_amount", row.get("BUY_AMT", 0))), sell_amount=_num(row.get("sell_amount", row.get("SELL_AMT", 0))), net_amount=flow, data_at=row_time))
        elif capability is Capability.DRAGON_TIGER_SEATS:
            output.append(DragonTigerSeat(name=name, buy_amount=_num(row.get("buy_amount", row.get("BUY_AMT", 0))), sell_amount=_num(row.get("sell_amount", row.get("SELL_AMT", 0))), net_amount=flow, appearances=_int(row.get("appearances", row.get("APPEARANCES", 0))) or 0, last_date=row.get("last_date") or row.get("LAST_DATE") or params.get("trade_date"), data_at=row_time))
        elif capability is Capability.STOCK_NEWS:
            title = str(row.get("title") or "").strip()
            if title:
                output.append(NewsItem(title=title, content=str(row.get("content") or ""), date=row.get("date"), source=str(row.get("source") or row.get("mediaName") or "东方财富"), url=row.get("url")))
        elif capability in {Capability.SECTOR_KLINE, Capability.STOCK_KLINE_DAILY}:
            output.append(KlineBar(date=row.get("date") or row.get("f51"), open=_num(row.get("open", row.get("f52", 0))), close=_num(row.get("close", row.get("f53", 0))), high=_num(row.get("high", row.get("f54", 0))), low=_num(row.get("low", row.get("f55", 0))), volume=_num(row.get("volume", row.get("f56", 0))), data_at=row_time))
    return output


def _data_time(rows: list[Any], params: dict[str, Any]) -> datetime | None:
    values: list[datetime] = []
    for row in rows:
        for key in ("data_at", "date", "trade_date", "last_date"):
            value = getattr(row, key, None)
            if value:
                parsed = _parse_time(value)
                if parsed:
                    values.append(parsed)
                break
    if values:
        return max(values)
    value = params.get("trade_date")
    if value:
        try:
            return datetime.combine(date.fromisoformat(str(value)[:10]), datetime.min.time(), tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def _aggregate_fund_flow(rows: list[FundFlow]) -> FundFlow:
    if not rows:
        raise DataHubError(DataHubErrorCode.EMPTY_INVALID, "数据源返回空数据")
    last = rows[-1]
    return FundFlow(
        code=last.code,
        net_main_flow=_sum_optional(rows, "net_main_flow"),
        net_super_large=_sum_optional(rows, "net_super_large"),
        net_large=_sum_optional(rows, "net_large"),
        net_medium=_sum_optional(rows, "net_medium"),
        net_small=_sum_optional(rows, "net_small"),
        daily_flows=[flow for item in rows for flow in item.daily_flows],
        data_at=max((item.data_at for item in rows if item.data_at), default=None),
    )


def _aggregate_shareholders(rows: list[ShareholderSummary]) -> ShareholderSummary:
    if not rows:
        raise DataHubError(DataHubErrorCode.EMPTY_INVALID, "数据源返回空数据")
    ordered = sorted(rows, key=lambda row: row.data_at or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
    latest = ordered[0]
    previous = ordered[1].latest if len(ordered) > 1 else latest.previous
    change = latest.change_pct
    if not change and latest.latest is not None and previous:
        change = round((latest.latest - previous) / previous * 100, 2)
    return ShareholderSummary(
        code=latest.code,
        latest=latest.latest,
        previous=previous,
        change_pct=change,
        history=[item.latest for item in ordered if item.latest is not None][:8],
        data_at=latest.data_at,
    )


def _endpoint(capability: Capability, params: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Build the documented Eastmoney endpoint and query for each capability."""

    code = str(params.get("code") or params.get("stock_code") or params.get("ts_code") or "").upper()
    plain = code.split(".", 1)[0]
    if code.endswith(".BJ") or plain.startswith(("92", "4", "8")):
        market = "0"
    else:
        market = "1" if code.endswith((".SS", ".SH")) or plain.startswith(("5", "6", "9")) else "0"
    if capability is Capability.STOCK_FUND_FLOW:
        # The documented 120d endpoint is daily data.  ``days`` is the public
        # consumer contract, so any request wider than one intraday point must
        # select this endpoint even when no explicit period is supplied.
        days = max(1, min(int(params.get("days", 120)), 120))
        daily = days > 1 or str(params.get("period", "")).lower() in {"day", "daily", "120d"}
        url = "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get" if daily else "https://push2.eastmoney.com/api/qt/stock/fflow/kline/get"
        return url, {"secid": f"{market}.{plain}", "klt": "101" if daily else "1", "lmt": str(days), "fields1": "f1,f2,f3,f7", "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65"}
    if capability is Capability.STOCK_PROFILE:
        return "https://push2.eastmoney.com/api/qt/stock/get", {
            "secid": f"{market}.{plain}",
            "fields": "f57,f58,f127,f124",
        }
    if capability is Capability.STOCK_NEWS:
        keyword = str(params.get("keyword") or params.get("source") or plain or "A股")
        inner = json.dumps({"uid": "", "keyword": keyword, "type": ["cmsArticleWebOld"], "client": "web", "clientType": "web", "clientVersion": "curr", "param": {"cmsArticleWebOld": {"searchScope": "default", "sort": "default", "pageIndex": 1, "pageSize": int(params.get("limit", 20)), "preTag": "", "postTag": ""}}}, separators=(",", ":"))
        return "https://search-api-web.eastmoney.com/search/jsonp", {"cb": "jQuery_news", "param": inner}
    if capability is Capability.STOCK_SHAREHOLDERS:
        return "https://datacenter-web.eastmoney.com/api/data/v1/get", {"reportName": "RPT_HOLDERNUMLATEST", "columns": "ALL", "filter": f'(SECURITY_CODE="{plain}")', "pageNumber": "1", "pageSize": "10", "sortColumns": "END_DATE", "sortTypes": "-1", "source": "WEB", "client": "WEB"}
    if capability in {Capability.DRAGON_TIGER_LIST, Capability.DRAGON_TIGER_SEATS}:
        report = "RPT_DAILYBILLBOARD_DETAILSNEW" if capability is Capability.DRAGON_TIGER_LIST else "RPT_BILLBOARD_DAILYDETAILSBUY"
        trade_date = params.get("trade_date", "")
        filter_text = f"(SECURITY_CODE=\"{plain}\")" if plain else ""
        if trade_date:
            filter_text += f"(TRADE_DATE='{trade_date}')"
        return "https://datacenter-web.eastmoney.com/api/data/v1/get", {"reportName": report, "columns": "ALL", "filter": filter_text, "pageNumber": "1", "pageSize": str(params.get("limit", 50)), "sortColumns": "TRADE_DATE", "sortTypes": "-1", "source": "WEB", "client": "WEB"}
    fields = "f2,f3,f6,f12,f14,f62,f66,f72,f78,f84,f184,f124,f128,f136,f138,f139,f140,f141"
    if capability is Capability.MARKET_BOARD_QUOTES:
        kind = _validated_kind(params.get("kind"))
        return "https://push2.eastmoney.com/api/qt/clist/get", {
            "pn": "1",
            "pz": "5000",
            "po": "1",
            "np": "1",
            "fltt": "2",
            "invt": "2",
            "fid": "f3",
            "fs": "m:90+t:2" if kind == "industry" else "m:90+t:3",
            "fields": "f12,f14,f3,f6,f20,f104,f105,f106,f128,f136,f140,f124",
        }
    if capability is Capability.MARKET_BOARD_CONSTITUENTS:
        kind = _validated_kind(params.get("kind"))
        board_code = _validated_board_code(params.get("board_code"))
        limit = max(1, min(int(params.get("limit", 20)), 20))
        return "https://push2.eastmoney.com/api/qt/clist/get", {
            "pn": "1",
            "pz": str(limit),
            "po": "1",
            "np": "1",
            "fltt": "2",
            "invt": "2",
            "fid": "f3",
            "fs": f"b:{board_code}",
            "fields": "f2,f3,f6,f12,f14,f20,f124",
        }
    if capability is Capability.MARKET_FUND_FLOW_RANK:
        # Explicitly cover Shanghai main/STAR, Shenzhen, and Beijing.  The
        # separate BJ ``s:2048`` selector avoids silently dropping the new
        # 920xxx listings from a nominal "all A" query.
        fs, fid = "m:1+t:2,m:1+t:23,m:0+t:6,m:0+t:80,m:0+t:81+s:2048", "f62"
    elif capability in {Capability.SECTOR_FUND_FLOW}:
        fs, fid = "m:90+t:2", "f62"
    else:
        fs, fid = "m:90+t:2", "f3"
    if capability is Capability.STOCK_KLINE_DAILY:
        days = max(1, min(int(params.get("days", 120)), 500))
        return "https://push2his.eastmoney.com/api/qt/stock/kline/get", {
            "secid": f"{market}.{plain}",
            "klt": "101",
            "fqt": "1",
            "lmt": str(days),
            "beg": "0",
            "end": "20500101",
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        }
    if capability is Capability.SECTOR_KLINE:
        return "https://push2his.eastmoney.com/api/qt/stock/kline/get", {"secid": f"90.{plain}", "klt": "101", "fqt": "1", "lmt": str(params.get("days", 120)), "fields1": "f1,f2,f3,f4", "fields2": "f51,f52,f53,f54,f55,f56,f57"}
    return "https://push2.eastmoney.com/api/qt/clist/get", {"pn": "1", "pz": str(params.get("limit", 200)), "po": "1", "np": "1", "fltt": "2", "invt": "2", "fid": fid, "fs": fs, "fields": fields}


def _parse_jsonp(text: str) -> dict[str, Any]:
    match = re.search(r"\((.*)\)", text or "", flags=re.S)
    if not match:
        return {}
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return {}


def _row_time(row: dict[str, Any]) -> datetime | None:
    for key in ("data_at", "f124", "date", "trade_date", "TRADE_DATE", "END_DATE", "last_date", "LAST_DATE"):
        value = row.get(key)
        if not value:
            continue
        parsed = _parse_time(value)
        if parsed:
            return parsed
    return None


def _parse_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    raw = str(value).strip()
    try:
        if raw.replace(".", "", 1).isdigit() and float(raw) > 1_000_000_000:
            return datetime.fromtimestamp(float(raw), tz=timezone.utc)
    except (TypeError, ValueError, OverflowError):
        pass
    for fmt in ("%Y%m%d%H%M%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(raw[:len(datetime.now().strftime(fmt))], fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _num(value: Any) -> float:
    try:
        return float(str(value).replace(",", "")) if value not in (None, "", "-") else 0.0
    except (TypeError, ValueError):
        return 0.0


def _sum_optional(rows: list[FundFlow], field: str) -> float | None:
    values = [getattr(row, field) for row in rows if getattr(row, field) is not None]
    return round(sum(values), 6) if values else None


def _missing_flow_fields(flow: FundFlow) -> list[str]:
    return [
        field
        for field in ("net_main_flow", "net_super_large", "net_large", "net_medium", "net_small")
        if getattr(flow, field) is None
    ]


def _int(value: Any) -> int | None:
    try:
        return int(float(value)) if value not in (None, "", "-") else None
    except (TypeError, ValueError):
        return None


def _optional_num(value: Any) -> float | None:
    if value in (None, "", "-", "--"):
        return None
    try:
        number = float(str(value).replace(",", ""))
        return number if number == number and number not in (float("inf"), float("-inf")) else None
    except (TypeError, ValueError):
        return None


def _optional_int(value: Any) -> int | None:
    number = _optional_num(value)
    return int(number) if number is not None else None


def _optional_text(value: Any) -> str | None:
    if value in (None, "", "-", "--"):
        return None
    return str(value)


def _optional_board_leader_code(value: Any) -> str | None:
    if value in (None, "", "-", "--"):
        return None
    raw = str(value).strip()
    try:
        return normalise_ticker(raw).split(".", 1)[0]
    except DataHubError:
        return raw


def _validated_kind(value: Any) -> str:
    if value not in {"industry", "theme"}:
        raise DataHubError(DataHubErrorCode.VALIDATION, "板块类型必须是 industry 或 theme", provider="eastmoney")
    return str(value)


def _validated_board_code(value: Any) -> str:
    raw = str(value or "").upper()
    if re.fullmatch(r"BK\d{7,10}", raw):
        raise DataHubError(DataHubErrorCode.UNSUPPORTED, "东方财富不支持该板块代码", provider="eastmoney")
    if not re.fullmatch(r"BK\d{3,6}", raw):
        raise DataHubError(DataHubErrorCode.VALIDATION, "板块代码格式无效", provider="eastmoney")
    return raw


__all__ = ["EastmoneyProvider"]
