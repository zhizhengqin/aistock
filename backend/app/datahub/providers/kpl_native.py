"""Opt-in native Kaipanla adapter.

The protocol follows the approved local guide.  Tokens are accepted only from
runtime configuration; this module contains no sample credentials and never
returns request credentials in errors or response payloads.
"""

from __future__ import annotations

import inspect
from datetime import datetime, timezone
from typing import Any

import anyio
from pydantic import ValidationError

from app.datahub.contracts import Capability, DataQuality, DataResult
from app.datahub.errors import DataHubError, DataHubErrorCode
from app.datahub.providers.base import ProviderAdapter, translate_provider_error
from app.datahub.validators import validate_payload


_SUPPORTED_CAPABILITIES = frozenset(
    {
        Capability.KPL_NATIVE_STOCK_TAGS,
        Capability.KPL_NATIVE_PLATE_RANKING,
        Capability.KPL_NATIVE_PLATE_CONSTITUENTS,
        Capability.KPL_NATIVE_STOCK_RANKING,
    }
)


class KplNativeProvider(ProviderAdapter):
    name = "kpl_native"
    BASE_URL = "https://pchq.kaipanla.com/w1/api/index.php"

    def __init__(self, *, token: str, user_id: str = "", http_client: Any | None = None, limiter=None) -> None:
        super().__init__(limiter=limiter)
        self.token = token
        self.user_id = user_id
        self.http_client = http_client

    def update_credentials(self, credentials: dict[str, str]) -> None:
        user_id = str(credentials.get("user_id") or "")
        token = str(credentials.get("token") or "")
        self.user_id = user_id or self.user_id
        if token != self.token:
            self.token = token

    def _require_credentials(self) -> None:
        if not self.user_id or not self.token:
            raise DataHubError(
                DataHubErrorCode.NOT_CONFIGURED,
                "尚未配置开盘啦 UserID 与 Token（原生接口默认关闭）",
                provider=self.name,
            )

    async def fetch(self, capability: Capability | str, params: dict[str, Any]) -> DataResult:
        capability = Capability(capability)
        if capability not in _SUPPORTED_CAPABILITIES:
            if not self.token:
                raise DataHubError(DataHubErrorCode.NOT_CONFIGURED, "尚未配置开盘啦 Token（原生接口默认关闭）", provider=self.name)
            raise DataHubError(DataHubErrorCode.UNSUPPORTED, "开盘啦原生暂不支持该数据能力", provider=self.name)
        self._require_credentials()
        try:
            response = await self._call_action(capability, params)
            safe = _redact_payload(response)
            rows = _normalise_payload(safe, capability)
            count = validate_payload(capability, rows)
            data_at = _data_time(rows, params)
            if data_at is None:
                raise DataHubError(DataHubErrorCode.STALE_INVALID, "开盘啦原生响应缺少可信数据时间", provider=self.name)
            return DataResult(
                data=safe,
                capability=capability,
                provider=self.name,
                data_at=data_at,
                quality=DataQuality(valid=True, rows=count),
            )
        except DataHubError:
            raise
        except ValidationError:
            raise DataHubError(DataHubErrorCode.SCHEMA_CHANGED, "开盘啦原生响应字段发生变化", provider=self.name) from None
        except Exception as exc:
            raise translate_provider_error(exc, provider=self.name) from None

    async def get_hq_plate(self, stock_code: str, trade_date: str) -> dict[str, Any]:
        """Call the sole protocol documented by the approved KPL guide."""

        self._require_credentials()
        payload = _build_request_payload(
            Capability.KPL_NATIVE_STOCK_TAGS,
            {"stock_code": stock_code, "trade_date": trade_date},
            user_id=self.user_id,
            token=self.token,
        )
        try:
            response = await self._post(payload)
            if not isinstance(response, dict):
                raise DataHubError(DataHubErrorCode.SCHEMA_CHANGED, "开盘啦盘口响应结构变化", provider=self.name)
            safe = {key: response.get(key) for key in ("trend", "pankou", "stockplate") if key in response}
            if not safe.get("trend") or not safe.get("pankou"):
                raise DataHubError(DataHubErrorCode.SCHEMA_CHANGED, "开盘啦盘口响应缺少必填字段", provider=self.name)
            return safe
        except DataHubError:
            raise
        except ValidationError:
            raise DataHubError(DataHubErrorCode.SCHEMA_CHANGED, "数据源字段发生变化", provider=self.name) from None
        except Exception as exc:
            raise translate_provider_error(exc, provider=self.name) from None

    async def _call_action(self, capability: Capability, params: dict[str, Any]) -> Any:
        payload = _build_request_payload(
            capability,
            params,
            user_id=self.user_id,
            token=self.token,
        )
        return await self._post(payload)

    async def _post(self, payload: dict[str, Any]) -> Any:
        async def request():
            if self.http_client is None:
                try:
                    import httpx

                    async with httpx.AsyncClient(timeout=10) as client:
                        response = await client.post(
                            self.BASE_URL,
                            data=payload,
                            headers={"User-Agent": "Mozilla/5.0"},
                        )
                        return _decode_response(response, self.name)
                except ImportError:
                    raise DataHubError(DataHubErrorCode.INTERNAL, "原生开盘啦客户端不可用", provider=self.name)
            post = self.http_client.post
            if inspect.iscoroutinefunction(post):
                response = await post(self.BASE_URL, data=payload)
            else:
                response = await anyio.to_thread.run_sync(lambda: post(self.BASE_URL, data=payload))
            if hasattr(response, "__await__"):
                response = await response
            return _decode_response(response, self.name)

        return await self.limiter.run(self.name, request)


_MISSING = object()


def _parameter(params: dict[str, Any], *names: str, default: Any = _MISSING) -> Any:
    """Read an explicitly supplied protocol value without treating zero as missing."""

    for name in names:
        if name in params and params[name] is not None:
            return params[name]
    if default is _MISSING:
        return None
    return default


def _required_parameter(params: dict[str, Any], *names: str) -> Any:
    value = _parameter(params, *names)
    if value in (None, ""):
        raise DataHubError(DataHubErrorCode.VALIDATION, "开盘啦原生请求缺少必填参数", provider="kpl_native")
    return value


def _build_request_payload(
    capability: Capability,
    params: dict[str, Any],
    *,
    user_id: str,
    token: str,
) -> dict[str, Any]:
    """Build the exact form payload for one documented native capability."""

    day = str(_parameter(params, "trade_date", "day", "Date", "date", default=""))
    if capability is Capability.KPL_NATIVE_STOCK_TAGS:
        stock_code = str(_required_parameter(params, "stock_code", "code"))
        payload: dict[str, Any] = {
            "c": "PCArrangeData",
            "a": "GetHQPlate",
            "StockID": stock_code.split(".", 1)[0],
            "Day": day,
            "SelType": _parameter(params, "SelType", "sel_type", default="1,2,3,8,9,5,6,7"),
        }
    elif capability is Capability.KPL_NATIVE_PLATE_RANKING:
        payload = {
            "c": "ZhiShuRanking",
            "a": "RealRankingInfo",
            "SelType": _parameter(params, "SelType", "sel_type", default=2),
            "ZSType": _parameter(params, "ZSType", "zstype", default=7),
            "Type": _parameter(params, "Type", "type", default=1),
            "Order": _parameter(params, "Order", "order", default=1),
            "Start": _parameter(params, "Start", "start", default=""),
            "End": _parameter(params, "End", "end", default=""),
            "Index": _parameter(params, "Index", "index", default=0),
            "st": _parameter(params, "st", "St", "limit", default=10),
        }
    elif capability is Capability.KPL_NATIVE_PLATE_CONSTITUENTS:
        payload = {
            "c": "ZhiShuRanking",
            "a": "ZhiShuStockList_W8",
            "PlateID": _required_parameter(params, "plate_id", "PlateID", "plateid"),
            "SelType": _parameter(params, "SelType", "sel_type", default=3),
            "Type": _parameter(params, "Type", "type", default=6),
            "Order": _parameter(params, "Order", "order", default=1),
            "Start": _parameter(params, "Start", "start", default=""),
            "End": _parameter(params, "End", "end", default=""),
            "Index": _parameter(params, "Index", "index", default=0),
            "st": _parameter(params, "st", "St", "limit", default=10),
        }
    elif capability is Capability.KPL_NATIVE_STOCK_RANKING:
        payload = {
            "c": "StockRanking",
            "a": "RealRankingInfo",
            "Date": day,
            "RStart": _parameter(params, "RStart", "rstart", default=""),
            "REnd": _parameter(params, "REnd", "rend", default=""),
            "Ratio": _parameter(params, "Ratio", "ratio", default=2),
            "Type": _parameter(params, "Type", "type", default=1),
            "Order": _parameter(params, "Order", "order", default=1),
            "index": _parameter(params, "index", "Index", default=0),
            "st": _parameter(params, "st", "St", "limit", default=50),
        }
    else:
        raise DataHubError(DataHubErrorCode.UNSUPPORTED, "开盘啦原生暂不支持该数据能力", provider="kpl_native")
    payload["UserID"] = user_id
    payload["Token"] = token
    return payload


def _normalise_payload(payload: Any, capability: Capability) -> list[dict[str, Any]]:
    if capability is Capability.KPL_NATIVE_STOCK_TAGS:
        if not isinstance(payload, dict):
            raise DataHubError(DataHubErrorCode.SCHEMA_CHANGED, "开盘啦盘口响应结构变化", provider="kpl_native")
        if not payload.get("trend") or not payload.get("pankou"):
            raise DataHubError(DataHubErrorCode.SCHEMA_CHANGED, "开盘啦盘口响应缺少必填字段", provider="kpl_native")
        return [dict(payload)]

    if isinstance(payload, list):
        if not payload:
            raise DataHubError(DataHubErrorCode.EMPTY_INVALID, "开盘啦原生响应没有有效业务数据", provider="kpl_native")
        return [dict(row) for row in payload]
    if isinstance(payload, dict):
        for key in ("data", "list", "rows", "result"):
            if isinstance(payload.get(key), list):
                if not payload[key]:
                    raise DataHubError(DataHubErrorCode.EMPTY_INVALID, "开盘啦原生响应没有有效业务数据", provider="kpl_native")
                return [dict(row) if isinstance(row, dict) else {"value": row} for row in payload[key]]
        raise DataHubError(DataHubErrorCode.EMPTY_INVALID, "开盘啦原生响应没有有效业务数据", provider="kpl_native")
    raise DataHubError(DataHubErrorCode.SCHEMA_CHANGED, "开盘啦原生响应结构变化", provider="kpl_native")


def _data_time(rows: list[Any], params: dict[str, Any]) -> datetime | None:
    for row in rows:
        value = getattr(row, "trade_date", None) if not isinstance(row, dict) else row.get("trade_date") or row.get("Day") or row.get("date")
        if value:
            try:
                return datetime.strptime(str(value)[:8], "%Y%m%d").replace(tzinfo=timezone.utc)
            except ValueError:
                try:
                    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
                    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
                except ValueError:
                    pass
    value = params.get("trade_date")
    if value:
        try:
            return datetime.strptime(str(value)[:8], "%Y%m%d").replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def _parse_json_text(value: Any) -> Any:
    import json

    source = str(value or "").lstrip("\ufeff \t\r\n")
    for marker in ("{", "["):
        start = source.find(marker)
        if start < 0:
            continue
        try:
            return json.JSONDecoder().raw_decode(source[start:])[0]
        except json.JSONDecodeError:
            continue
    raise DataHubError(DataHubErrorCode.SCHEMA_CHANGED, "开盘啦原生响应格式发生变化", provider="kpl_native")


def _decode_response(response: Any, provider: str) -> Any:
    status_code = getattr(response, "status_code", 200)
    if status_code < 200 or status_code >= 300:
        if hasattr(response, "raise_for_status"):
            response.raise_for_status()
        raise DataHubError(DataHubErrorCode.INTERNAL, "开盘啦原生请求失败", provider=provider)
    try:
        value = response.json() if hasattr(response, "json") else response
    except Exception:
        value = _parse_json_text(getattr(response, "text", ""))
    if isinstance(value, str):
        value = _parse_json_text(value)
    if isinstance(value, dict):
        errcode = _business_error_code(value)
        if errcode is not None:
            if errcode == "1016":
                raise DataHubError(DataHubErrorCode.AUTHENTICATION_FAILED, "开盘啦未登录或凭证无效", provider=provider)
            safe_code = errcode if errcode.isdecimal() else "未知"
            raise DataHubError(DataHubErrorCode.INTERNAL, f"开盘啦业务请求失败（错误码：{safe_code}）", provider=provider)
    return value


def _business_error_code(value: dict[str, Any]) -> str | None:
    for key in ("errcode", "errCode", "code"):
        if key not in value:
            continue
        raw = value.get(key)
        if raw in (None, "", 0, "0", False):
            continue
        text = str(raw).strip()
        try:
            if int(text) == 0:
                continue
        except ValueError:
            pass
        if text.lower() in {"", "ok", "success"}:
            continue
        return text
    nested = value.get("data")
    if isinstance(nested, dict):
        return _business_error_code(nested)
    return None


def _redact_payload(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _redact_payload(item)
            for key, item in value.items()
            if key.lower() not in {"token", "userid", "user_id", "user\u200bid"}
        }
    if isinstance(value, list):
        return [_redact_payload(item) for item in value]
    return value


__all__ = ["KplNativeProvider"]
