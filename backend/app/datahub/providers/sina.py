"""Independent Sina quote adapter for basic snapshot/index fallback."""

from __future__ import annotations

import json
import re
import zlib
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from app.datahub.contracts import BoardConstituent, BoardQuote, Capability, DataResult, FinancialSummary, MarketIndex, StockSnapshot
from app.datahub.errors import DataHubError, DataHubErrorCode
from app.datahub.providers.base import ProviderAdapter, translate_provider_error
from app.datahub.providers.ticker import normalise_ticker, vendor_symbol
from app.datahub.validators import validate_payload


class SinaProvider(ProviderAdapter):
    name = "sina"
    BASE_URL = "https://hq.sinajs.cn/list="
    BOARD_URLS = {
        "industry": "https://vip.stock.finance.sina.com.cn/q/view/newSinaHy.php",
        "theme": "https://money.finance.sina.com.cn/q/view/newFLJK.php?param=class",
    }
    CONSTITUENT_URL = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData"
    _SUPPORTED = {
        Capability.MARKET_INDICES,
        Capability.MARKET_BOARD_QUOTES,
        Capability.MARKET_BOARD_CONSTITUENTS,
        Capability.STOCK_SNAPSHOT,
        Capability.STOCK_FINANCIALS,
    }

    def __init__(self, *, http_client: Any | None = None, limiter=None) -> None:
        super().__init__(limiter=limiter)
        self.http_client = http_client

    async def fetch(self, capability: Capability | str, params: dict[str, Any]) -> DataResult:
        capability = Capability(capability)
        if capability not in self._SUPPORTED:
            raise DataHubError(DataHubErrorCode.UNSUPPORTED, "新浪接口暂不支持该数据能力", provider=self.name)
        values = params.get("codes") or [params.get("code") or params.get("stock_code") or "000001.SS"]
        try:
            if capability is Capability.STOCK_FINANCIALS:
                return await self._fetch_financials(params)
            if capability is Capability.MARKET_BOARD_QUOTES:
                typed, data_at = await self.run_sync(lambda: self._fetch_board_quotes_sync(params))
                count = validate_payload(capability, typed)
                return DataResult(data=typed, capability=capability, provider=self.name, data_at=data_at, quality={"valid": True, "rows": count})
            if capability is Capability.MARKET_BOARD_CONSTITUENTS:
                typed, data_at = await self.run_sync(lambda: self._fetch_constituents_sync(params))
                count = validate_payload(capability, typed)
                return DataResult(data=typed, capability=capability, provider=self.name, data_at=data_at, quality={"valid": True, "rows": count})
            text = await self.run_sync(lambda: self._get_text(",".join(vendor_symbol(value) for value in values)))
            rows = [_parse_line(symbol, raw) for symbol, raw in _parse_lines(text)]
            rows = [row for row in rows if row]
            model = StockSnapshot if capability is Capability.STOCK_SNAPSHOT else MarketIndex
            typed = [model.model_validate(row) for row in rows]
            count = validate_payload(capability, typed)
            data_at = max((row.data_at for row in typed if getattr(row, "data_at", None)), default=None)
            if data_at is None:
                raise DataHubError(DataHubErrorCode.STALE_INVALID, "新浪行情缺少可信时间戳", provider=self.name)
            return DataResult(data=typed[0] if capability is Capability.STOCK_SNAPSHOT and typed else typed, capability=capability, provider=self.name, data_at=data_at, quality={"valid": True, "rows": count})
        except DataHubError:
            raise
        except Exception as exc:
            raise translate_provider_error(exc, provider=self.name) from None

    def _fetch_board_quotes_sync(self, params: dict[str, Any]) -> tuple[list[BoardQuote], datetime]:
        kind = _validated_kind(params.get("kind"))
        rows = _load_board_rows(self._get_text_url(self.BOARD_URLS[kind]), kind=kind)
        data_at = _index_data_at(self._get_text("sh000001"))
        if data_at is None:
            raise DataHubError(DataHubErrorCode.STALE_INVALID, "新浪板块数据缺少可信时间戳", provider=self.name)
        typed = [
            BoardQuote(
                board_code=board_code,
                board_name=values[1],
                kind=kind,
                change_pct=_maybe_number(values[5]),
                turnover=_maybe_number(values[7]),
                market_cap=None,
                rise_count=None,
                fall_count=None,
                flat_count=None,
                leader_code=_normalise_leader(values[8]),
                leader_name=_optional_text(values[12]),
                leader_change_pct=_maybe_number(values[9]),
                data_at=data_at,
            )
            for board_code, values in rows
        ]
        if not typed:
            raise DataHubError(DataHubErrorCode.EMPTY_INVALID, "新浪板块返回空数据", provider=self.name)
        return typed, data_at

    def _fetch_constituents_sync(self, params: dict[str, Any]) -> tuple[list[BoardConstituent], datetime]:
        kind = _validated_kind(params.get("kind"))
        board_code = str(params.get("board_code") or "").upper()
        rows = _load_board_rows(self._get_text_url(self.BOARD_URLS[kind]), kind=kind)
        native = next((values[0] for code, values in rows if code == board_code), None)
        if native is None:
            raise DataHubError(DataHubErrorCode.VALIDATION, "新浪板块代码无效", provider=self.name)
        limit = max(1, min(int(params.get("limit", 20)), 20))
        payload = _parse_json_payload(
            self._get_text_url(
                self.CONSTITUENT_URL,
                params={
                    "page": 1,
                    "num": limit,
                    "sort": "changepercent",
                    "asc": 0,
                    "node": native,
                    "symbol": "",
                    "_s_r_a": "page",
                },
            )
        )
        data_at = _index_data_at(self._get_text("sh000001"))
        if data_at is None:
            raise DataHubError(DataHubErrorCode.STALE_INVALID, "新浪成分股数据缺少可信时间戳", provider=self.name)
        typed: list[BoardConstituent] = []
        for row in payload if isinstance(payload, list) else []:
            if not isinstance(row, dict):
                continue
            try:
                code = normalise_ticker(row.get("code"))
            except DataHubError:
                continue
            typed.append(
                BoardConstituent(
                    code=code,
                    name=_optional_text(row.get("name")) or code,
                    price=_maybe_number(row.get("trade")),
                    change_pct=_maybe_number(row.get("changepercent")),
                    turnover=_maybe_number(row.get("amount")),
                    market_cap=_scale_market_cap(row.get("mktcap")),
                    data_at=data_at,
                )
            )
        if not typed:
            raise DataHubError(DataHubErrorCode.EMPTY_INVALID, "新浪成分股返回空数据", provider=self.name)
        return typed[:limit], data_at

    async def _fetch_financials(self, params: dict[str, Any]) -> DataResult:
        code = params.get("code") or params.get("stock_code") or params.get("ts_code")
        if not code:
            raise DataHubError(DataHubErrorCode.VALIDATION, "缺少股票代码", provider=self.name)
        symbol = vendor_symbol(code)
        payload = await self.run_sync(
            lambda: self._get_financial_json(symbol, params.get("report_type", "lrb"), int(params.get("periods", 8)))
        )
        report_list = (((payload or {}).get("result") or {}).get("data") or {}).get("report_list") or {}
        if not isinstance(report_list, dict) or not report_list:
            raise DataHubError(DataHubErrorCode.EMPTY_INVALID, "新浪财报暂时没有有效数据", provider=self.name)
        period = sorted(report_list.keys(), reverse=True)[0]
        obj = report_list.get(period) or {}
        values = {
            str(item.get("item_title", "")): item.get("item_value")
            for item in (obj.get("data") or [])
            if isinstance(item, dict) and item.get("item_title") and item.get("item_value") is not None
        }
        report_date = _format_period(period)
        typed = FinancialSummary(
            code=normalise_ticker(code),
            report_date=report_date,
            revenue=_first_number(values, ("营业收入", "营业总收入", "营业总收入(元)")),
            net_profit=_first_number(values, ("净利润", "归属于母公司股东的净利润")),
            roe=_first_number(values, ("净资产收益率", "净资产收益率(%)")),
            gross_margin=_first_number(values, ("销售毛利率", "销售毛利率(%)")),
            debt_ratio=_first_number(values, ("资产负债率", "资产负债率(%)")),
            data_at=_period_time(report_date),
        )
        count = validate_payload(Capability.STOCK_FINANCIALS, [typed])
        return DataResult(data=typed, capability=Capability.STOCK_FINANCIALS, provider=self.name, data_at=typed.data_at, quality={"valid": True, "rows": count})

    def _get_text(self, query: str) -> str:
        return self._get_text_url(f"{self.BASE_URL}{query}")

    def _get_text_url(self, url: str, *, params: dict[str, Any] | None = None) -> str:
        client = self.http_client
        close = False
        if client is None:
            import httpx

            client = httpx.Client()
            close = True
        try:
            try:
                request_kwargs: dict[str, Any] = {
                    "timeout": 10,
                    "headers": {"User-Agent": "Mozilla/5.0", "Referer": "https://stock.finance.sina.com.cn/"},
                }
                if params is not None:
                    request_kwargs["params"] = params
                response = client.get(url, **request_kwargs)
            except TypeError:
                # Keep tiny recorded fixtures compatible while production
                # clients receive the origin headers required by Sina.
                if params is None:
                    response = client.get(url, timeout=10)
                else:
                    response = client.get(url, params=params, timeout=10)
            if hasattr(response, "raise_for_status"):
                response.raise_for_status()
            content = getattr(response, "content", None)
            return bytes(content).decode("gbk", errors="replace") if content is not None else str(getattr(response, "text", ""))
        finally:
            if close and hasattr(client, "close"):
                client.close()

    def _get_financial_json(self, symbol: str, report_type: str, periods: int) -> Any:
        client = self.http_client
        close = False
        if client is None:
            import httpx

            client = httpx.Client()
            close = True
        try:
            response = client.get(
                "https://quotes.sina.cn/cn/api/openapi.php/CompanyFinanceService.getFinanceReport2022",
                params={"paperCode": symbol, "source": report_type, "type": "0", "page": "1", "num": str(max(1, min(periods, 20)))},
                timeout=10,
            )
            if hasattr(response, "raise_for_status"):
                response.raise_for_status()
            return response.json() if hasattr(response, "json") else {}
        finally:
            if close and hasattr(client, "close"):
                client.close()


def canonical_board_code(kind: str, native_node: str) -> str:
    """Generate a stable product code without exposing Sina's native node."""

    digest = zlib.crc32(f"{kind}:{native_node}".encode("utf-8")) & 0xFFFFFFFF
    return f"BK{digest:010d}"


def _validated_kind(value: Any) -> str:
    kind = str(value or "")
    if kind not in {"industry", "theme"}:
        raise DataHubError(DataHubErrorCode.VALIDATION, "板块类型必须是 industry 或 theme", provider="sina")
    return kind


def _parse_json_payload(text: str) -> Any:
    source = str(text or "").lstrip("\ufeff \t\r\n")
    for marker in ("[", "{"):
        start = source.find(marker)
        if start < 0:
            continue
        try:
            return json.JSONDecoder().raw_decode(source[start:])[0]
        except json.JSONDecodeError:
            continue
    raise DataHubError(DataHubErrorCode.SCHEMA_CHANGED, "新浪响应格式发生变化", provider="sina")


def _board_fields(value: Any) -> list[str]:
    if isinstance(value, str):
        return [part.strip() for part in value.split(",")]
    if isinstance(value, (list, tuple)):
        return [str(part).strip() if part is not None else "" for part in value]
    if isinstance(value, dict):
        return [str(value.get(str(index), "")).strip() for index in range(13)]
    return []


def _load_board_rows(text: str, *, kind: str) -> list[tuple[str, list[str]]]:
    payload = _parse_json_payload(text)
    if isinstance(payload, dict):
        nested = payload.get("data") or payload.get("result")
        payload = nested if nested is not None else list(payload.values())
    if not isinstance(payload, list):
        raise DataHubError(DataHubErrorCode.SCHEMA_CHANGED, "新浪板块响应格式发生变化", provider="sina")
    output: list[tuple[str, list[str]]] = []
    seen: dict[str, str] = {}
    for raw in payload:
        values = _board_fields(raw)
        if len(values) < 13 or not values[0] or not values[1]:
            continue
        code = canonical_board_code(kind, values[0])
        previous = seen.get(code)
        if previous is not None and previous != values[0]:
            raise DataHubError(DataHubErrorCode.SCHEMA_CHANGED, "新浪板块代码发生冲突", provider="sina")
        if previous is not None:
            continue
        seen[code] = values[0]
        output.append((code, values))
    if not output:
        raise DataHubError(DataHubErrorCode.EMPTY_INVALID, "新浪板块返回空数据", provider="sina")
    return output


def _index_data_at(text: str) -> datetime | None:
    for symbol, raw in _parse_lines(text):
        row = _parse_line(symbol, raw)
        if row and row.get("data_at") is not None:
            return row["data_at"]
    return None


def _optional_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _maybe_number(value: Any) -> float | None:
    text = str(value or "").strip()
    if not text or text in {"-", "--", "null", "None"}:
        return None
    try:
        return float(text.replace(",", ""))
    except (TypeError, ValueError):
        return None


def _scale_market_cap(value: Any) -> float | None:
    number = _maybe_number(value)
    return number * 10000 if number is not None else None


def _normalise_leader(value: Any) -> str | None:
    text = _optional_text(value)
    if not text:
        return None
    try:
        return normalise_ticker(text)
    except DataHubError:
        return text


def _parse_lines(text: str) -> list[tuple[str, str]]:
    return re.findall(r"hq_str_(\w+)=[\"'](.*?)[\"'];", text or "", flags=re.S)


def _parse_line(symbol: str, raw: str) -> dict[str, Any] | None:
    fields = raw.split(",")
    if len(fields) < 6:
        return None
    code = symbol[2:]
    market = "SS" if symbol.startswith("sh") else "BJ" if symbol.startswith("bj") else "SZ"
    date = next((value for value in fields if re.fullmatch(r"20\d{2}-\d{2}-\d{2}", value.strip())), "")
    clock = next((value for value in fields if re.fullmatch(r"\d{2}:\d{2}:\d{2}", value.strip())), "")
    data_at = None
    try:
            data_at = datetime.strptime(f"{date} {clock}", "%Y-%m-%d %H:%M:%S").replace(tzinfo=ZoneInfo("Asia/Shanghai")).astimezone(timezone.utc)
    except ValueError:
        pass
    price = _number(fields[3])
    previous = _number(fields[2])
    return {
        "code": f"{code}.{market}",
        "name": fields[0] or code,
        "price": price,
        "change_pct": round((price - previous) / previous * 100, 4) if previous else 0,
        "data_at": data_at,
    }


def _number(value: Any) -> float:
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return 0.0


def _format_period(value: Any) -> str:
    raw = str(value)
    if len(raw) == 8 and raw.isdigit():
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"
    return raw[:10]


def _period_time(value: str) -> datetime | None:
    try:
        return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _first_number(values: dict[str, Any], names: tuple[str, ...]) -> float:
    for name in names:
        if name in values:
            return _number(values[name])
    return 0.0


__all__ = ["SinaProvider", "canonical_board_code"]
