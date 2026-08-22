"""Independent Sina quote adapter for basic snapshot/index fallback."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from app.datahub.contracts import Capability, DataResult, FinancialSummary, MarketIndex, StockSnapshot
from app.datahub.errors import DataHubError, DataHubErrorCode
from app.datahub.providers.base import ProviderAdapter, translate_provider_error
from app.datahub.providers.ticker import normalise_ticker, vendor_symbol
from app.datahub.validators import validate_payload


class SinaProvider(ProviderAdapter):
    name = "sina"
    BASE_URL = "https://hq.sinajs.cn/list="

    def __init__(self, *, http_client: Any | None = None, limiter=None) -> None:
        super().__init__(limiter=limiter)
        self.http_client = http_client

    async def fetch(self, capability: Capability | str, params: dict[str, Any]) -> DataResult:
        capability = Capability(capability)
        if capability not in {Capability.MARKET_INDICES, Capability.STOCK_SNAPSHOT, Capability.STOCK_FINANCIALS}:
            raise DataHubError(DataHubErrorCode.UNSUPPORTED, "新浪接口暂不支持该数据能力", provider=self.name)
        values = params.get("codes") or [params.get("code") or params.get("stock_code") or "000001.SS"]
        try:
            if capability is Capability.STOCK_FINANCIALS:
                return await self._fetch_financials(params)
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
        client = self.http_client
        close = False
        if client is None:
            import httpx

            client = httpx.Client()
            close = True
        try:
            try:
                response = client.get(
                    f"{self.BASE_URL}{query}",
                    timeout=10,
                    headers={
                        "User-Agent": "Mozilla/5.0",
                        "Referer": "https://stock.finance.sina.com.cn/",
                    },
                )
            except TypeError:
                # Keep tiny recorded fixtures compatible while production
                # clients receive the origin headers required by Sina.
                response = client.get(f"{self.BASE_URL}{query}", timeout=10)
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


__all__ = ["SinaProvider"]
