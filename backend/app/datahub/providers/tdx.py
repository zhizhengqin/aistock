"""mootdx/TDX TCP adapter.

The adapter accepts an injected mootdx client for deterministic tests.  A
cloud deployment without TCP 7709 reachability reports ``not_configured`` or
``unsupported`` instead of pretending that AkShare is TDX.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.datahub.contracts import Capability, DataResult, KlineBar, StockSnapshot
from app.datahub.errors import DataHubError, DataHubErrorCode
from app.datahub.providers.base import ProviderAdapter, translate_provider_error
from app.datahub.validators import validate_payload


class TdxProvider(ProviderAdapter):
    name = "tdx"

    def __init__(self, *, client: Any | None = None, limiter=None) -> None:
        super().__init__(limiter=limiter)
        self.client = client

    async def fetch(self, capability: Capability | str, params: dict[str, Any]) -> DataResult:
        capability = Capability(capability)
        if capability not in {Capability.STOCK_SNAPSHOT, Capability.STOCK_KLINE_DAILY}:
            raise DataHubError(DataHubErrorCode.UNSUPPORTED, "通达信协议暂不支持该数据能力", provider=self.name)
        if self.client is None:
            raise DataHubError(DataHubErrorCode.NOT_CONFIGURED, "通达信 TCP 7709 尚未配置或不可达", provider=self.name)
        code = str(params.get("code") or params.get("stock_code") or "")
        try:
            if capability is Capability.STOCK_KLINE_DAILY:
                raw = await self.run_sync(lambda: _call_client(self.client, ("get_kline", "get_bars", "bars"), code, params))
                rows = [_normalise_kline(item) for item in _as_rows(raw)]
                rows = [KlineBar.model_validate(item) for item in rows]
            else:
                raw = await self.run_sync(lambda: _call_client(self.client, ("get_snapshot", "get_quotes", "quote"), code, params))
                rows = [StockSnapshot.model_validate(_normalise_snapshot(item, code)) for item in _as_rows(raw)]
            count = validate_payload(capability, rows)
            data_at = _latest_time(rows)
            if data_at is None:
                raise DataHubError(DataHubErrorCode.STALE_INVALID, "通达信响应缺少可信时间戳", provider=self.name)
            return DataResult(data=rows[0] if capability is Capability.STOCK_SNAPSHOT and rows else rows, capability=capability, provider=self.name, data_at=data_at, quality={"valid": True, "rows": count})
        except DataHubError:
            raise
        except Exception as exc:
            raise translate_provider_error(exc, provider=self.name) from None


def _call_client(client: Any, names: tuple[str, ...], code: str, params: dict[str, Any]) -> Any:
    for name in names:
        method = getattr(client, name, None)
        if method is not None:
            try:
                return method(code=code, **params)
            except TypeError:
                try:
                    return method(code, **params)
                except TypeError:
                    return method(code)
    raise DataHubError(DataHubErrorCode.UNSUPPORTED, "通达信客户端缺少所需协议方法", provider="tdx")


def _as_rows(raw: Any) -> list[dict[str, Any]]:
    if raw is None:
        return []
    if hasattr(raw, "to_dict"):
        return raw.to_dict("records")
    if isinstance(raw, dict):
        return [raw]
    return [dict(item) for item in raw if isinstance(item, dict)]


def _normalise_kline(row: dict[str, Any]) -> dict[str, Any]:
    return {"date": row.get("date") or row.get("datetime") or row.get("trade_date"), "open": _num(row.get("open")), "close": _num(row.get("close")), "high": _num(row.get("high")), "low": _num(row.get("low")), "volume": _num(row.get("volume"))}


def _normalise_snapshot(row: dict[str, Any], code: str) -> dict[str, Any]:
    return {"code": row.get("code") or code, "name": row.get("name") or "", "price": _num(row.get("price") or row.get("last")), "change_pct": _num(row.get("change_pct") or row.get("percent")), "data_at": row.get("data_at") or row.get("datetime") or row.get("date")}


def _latest_time(rows: list[Any]) -> datetime | None:
    values: list[datetime] = []
    for row in rows:
        value = getattr(row, "data_at", None) or getattr(row, "date", None)
        if not value:
            continue
        try:
            if isinstance(value, datetime):
                parsed = value
            else:
                parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            values.append(parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc))
        except ValueError:
            continue
    return max(values) if values else None


def _num(value: Any) -> float:
    try:
        return float(value) if value not in (None, "", "-") else 0.0
    except (TypeError, ValueError):
        return 0.0


__all__ = ["TdxProvider"]
