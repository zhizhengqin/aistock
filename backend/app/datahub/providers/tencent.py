"""Tencent ``qt.gtimg.cn`` provider.

The endpoint returns GBK-delimited quote fields.  Parsing lives here so the
rest of DataHub never sees vendor-specific strings or encodings.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from app.datahub.contracts import Capability, DataResult, KlineBar, MarketIndex, StockSnapshot
from app.datahub.errors import DataHubError, DataHubErrorCode
from app.datahub.providers.base import ProviderAdapter, translate_provider_error
from app.datahub.providers.ticker import normalise_ticker, vendor_symbol
from app.datahub.validators import validate_payload


_QUOTE_RE = re.compile(r"v_([a-z]{2}\d{6})=\"(.*?)\";", re.S)


class TencentProvider(ProviderAdapter):
    name = "tencent"
    BASE_URL = "https://qt.gtimg.cn/q="
    KLINE_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"

    def __init__(self, *, http_client: Any | None = None, limiter=None) -> None:
        super().__init__(limiter=limiter)
        self.http_client = http_client

    async def fetch(self, capability: Capability | str, params: dict[str, Any]) -> DataResult:
        capability = Capability(capability)
        if capability not in {Capability.MARKET_INDICES, Capability.STOCK_SNAPSHOT, Capability.STOCK_KLINE_DAILY}:
            raise DataHubError(DataHubErrorCode.UNSUPPORTED, "腾讯接口暂不支持该数据能力", provider=self.name)
        try:
            if capability is Capability.STOCK_KLINE_DAILY:
                return await self._fetch_kline(params)
            codes = params.get("codes") or [params.get("code") or params.get("stock_code") or "000001.SS"]
            symbols = [vendor_symbol(code) for code in codes if code]
            if not symbols:
                raise DataHubError(DataHubErrorCode.VALIDATION, "缺少股票代码", provider=self.name)
            text = await self.run_sync(lambda: self._get_text(",".join(symbols)))
            rows = [_parse_quote(symbol, fields) for symbol, fields in _parse_payload(text)]
            rows = [row for row in rows if row is not None]
            if capability is Capability.STOCK_SNAPSHOT:
                rows = [row for row in rows if row["code"] == normalise_ticker(codes[0])]
                model_rows = [StockSnapshot.model_validate(row) for row in rows]
            else:
                model_rows = [MarketIndex.model_validate(row) for row in rows]
            count = validate_payload(capability, model_rows)
            data_at = _latest_time(model_rows)
            if data_at is None:
                raise DataHubError(DataHubErrorCode.STALE_INVALID, "腾讯行情缺少可信时间戳", provider=self.name)
            return DataResult(data=model_rows[0] if capability is Capability.STOCK_SNAPSHOT and model_rows else model_rows, capability=capability, provider=self.name, data_at=data_at, quality={"valid": True, "rows": count})
        except DataHubError:
            raise
        except Exception as exc:
            raise translate_provider_error(exc, provider=self.name) from None

    async def _fetch_kline(self, params: dict[str, Any]) -> DataResult:
        code = params.get("code") or params.get("stock_code") or params.get("ts_code")
        if not code:
            raise DataHubError(DataHubErrorCode.VALIDATION, "缺少股票代码", provider=self.name)
        symbol = vendor_symbol(code)
        days = max(1, min(int(params.get("days", 120)), 500))
        payload = await self.run_sync(lambda: self._get_kline_json(symbol, days))
        data = payload.get("data") if isinstance(payload, dict) else None
        raw_rows = (data or {}).get("qfqday") or (data or {}).get("day") or []
        rows: list[KlineBar] = []
        for raw in raw_rows:
            if isinstance(raw, dict):
                row = raw
            elif isinstance(raw, (list, tuple)) and len(raw) >= 6:
                row = {
                    "date": raw[0],
                    "open": raw[1],
                    "close": raw[2],
                    "high": raw[3],
                    "low": raw[4],
                    "volume": raw[5],
                }
            else:
                continue
            try:
                rows.append(KlineBar.model_validate(row))
            except Exception:
                continue
        count = validate_payload(Capability.STOCK_KLINE_DAILY, rows)
        data_at = _latest_kline_time(rows)
        if data_at is None:
            raise DataHubError(DataHubErrorCode.STALE_INVALID, "腾讯 K 线缺少可信交易日", provider=self.name)
        return DataResult(data=rows, capability=Capability.STOCK_KLINE_DAILY, provider=self.name, data_at=data_at, quality={"valid": True, "rows": count})

    def _get_kline_json(self, symbol: str, days: int) -> Any:
        client = self.http_client
        close = False
        if client is None:
            import httpx

            client = httpx.Client()
            close = True
        try:
            response = client.get(
                self.KLINE_URL,
                params={"param": f"{symbol},day,,,{days},qfq"},
                timeout=10,
            )
            if hasattr(response, "raise_for_status"):
                response.raise_for_status()
            return response.json() if hasattr(response, "json") else {}
        finally:
            if close and hasattr(client, "close"):
                client.close()

    def _get_text(self, query: str) -> str:
        client = self.http_client
        close = False
        if client is None:
            import httpx

            client = httpx.Client()
            close = True
        try:
            response = client.get(f"{self.BASE_URL}{query}", timeout=10)
            if hasattr(response, "raise_for_status"):
                response.raise_for_status()
            content = getattr(response, "content", None)
            if content is not None:
                return bytes(content).decode("gbk", errors="replace")
            return str(getattr(response, "text", ""))
        finally:
            if close and hasattr(client, "close"):
                client.close()


def _parse_payload(text: str) -> list[tuple[str, list[str]]]:
    result: list[tuple[str, list[str]]] = []
    for symbol, raw in _QUOTE_RE.findall(text or ""):
        result.append((symbol, raw.split("~")))
    return result


def _parse_quote(symbol: str, fields: list[str]) -> dict[str, Any] | None:
    if len(fields) < 4:
        return None
    market = "SS" if symbol.startswith("sh") else "BJ" if symbol.startswith("bj") else "SZ"
    code = f"{fields[2]}.{market}" if fields[2] else normalise_ticker(symbol[2:])
    price = _number(fields[3])
    previous = _number(fields[4])
    change_pct = round((price - previous) / previous * 100, 4) if previous else _number(fields[5])
    data_at = None
    for item in fields:
        compact = re.search(r"(?<!\d)(20\d{12})(?!\d)", str(item))
        if compact:
            try:
                local = datetime.strptime(compact.group(1), "%Y%m%d%H%M%S").replace(
                    tzinfo=ZoneInfo("Asia/Shanghai")
                )
            except ValueError:
                pass
            else:
                data_at = local.astimezone(timezone.utc)
                break
        match = re.search(r"(20\d{2})[/-](\d{1,2})[/-](\d{1,2})[ T](\d{1,2}):(\d{2})(?::(\d{2}))?", str(item))
        if match:
            local = datetime(*[int(value or 0) for value in match.groups()], tzinfo=ZoneInfo("Asia/Shanghai"))
            data_at = local.astimezone(timezone.utc)
            break
    # Tencent's quote protocol is positional.  Keep these indexes explicit:
    # 39 PE(TTM), 44 float market cap (亿元), 45 total market cap (亿元),
    # 46 PB and 52 static PE.  Do not infer by shifting fields when a vendor
    # adds an optional value near the end of the record.
    return {
        "code": code,
        "name": fields[1] or code,
        "price": price,
        "change_pct": change_pct,
        "pe_ttm": _number_or_none(fields, 39),
        "market_cap": _number_or_none(fields, 45),
        "float_market_cap": _number_or_none(fields, 44),
        "pb": _number_or_none(fields, 46),
        "pe_static": _number_or_none(fields, 52),
        "data_at": data_at,
    }


def _latest_time(rows: list[Any]) -> datetime | None:
    values = [getattr(row, "data_at", None) for row in rows]
    values = [value for value in values if value is not None]
    return max(values) if values else None


def _latest_kline_time(rows: list[KlineBar]) -> datetime | None:
    values: list[datetime] = []
    for row in rows:
        value = row.date
        try:
            parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
            values.append(parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc))
        except (TypeError, ValueError):
            continue
    return max(values) if values else None


def _number(value: Any) -> float:
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return 0.0


def _number_or_none(fields: list[str], index: int) -> float | None:
    if index >= len(fields):
        return None
    value = fields[index]
    if value in (None, "", "-", "--"):
        return None
    try:
        number = float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None
    return number if number == number and number not in (float("inf"), float("-inf")) else None


__all__ = ["TencentProvider"]
