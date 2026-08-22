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

from app.datahub.contracts import (
    AuctionOpen,
    Capability,
    DataQuality,
    DataResult,
    KplConcept,
    KplConceptConstituent,
    KplLimitItem,
    KplLimitLadder,
    KplStrongSector,
)
from app.datahub.errors import DataHubError, DataHubErrorCode
from app.datahub.providers.base import ProviderAdapter, translate_provider_error
from app.datahub.validators import validate_payload


class KplNativeProvider(ProviderAdapter):
    name = "kpl_native"
    BASE_URL = "https://pchq.kaipanla.com/w1/api/index.php"

    def __init__(self, *, token: str, user_id: str = "", http_client: Any | None = None, limiter=None) -> None:
        super().__init__(limiter=limiter)
        self.token = token
        self.user_id = user_id
        self.http_client = http_client

    def update_credentials(self, credentials: dict[str, str]) -> None:
        token = str(credentials.get("token") or "")
        if token != self.token:
            self.token = token

    async def fetch(self, capability: Capability | str, params: dict[str, Any]) -> DataResult:
        capability = Capability(capability)
        if not self.token:
            raise DataHubError(DataHubErrorCode.NOT_CONFIGURED, "尚未配置开盘啦 Token（原生接口默认关闭）", provider=self.name)
        raise DataHubError(
            DataHubErrorCode.UNSUPPORTED,
            "开盘啦原生仅验证 GetHQPlate 盘口标签接口，尚未映射到当前数据能力",
            provider=self.name,
        )

    async def get_hq_plate(self, stock_code: str, trade_date: str) -> dict[str, Any]:
        """Call the sole protocol documented by the approved KPL guide."""

        if not self.token:
            raise DataHubError(DataHubErrorCode.NOT_CONFIGURED, "尚未配置开盘啦 Token（原生接口默认关闭）", provider=self.name)
        payload = {
            "c": "PCArrangeData",
            "a": "GetHQPlate",
            "StockID": str(stock_code).split(".")[0],
            "Day": trade_date,
            "SelType": "1,2,3,8,9,5,6,7",
            "UserID": self.user_id,
            "Token": self.token,
        }
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
                        response.raise_for_status()
                        return response.json()
                except ImportError:
                    raise DataHubError(DataHubErrorCode.INTERNAL, "原生开盘啦客户端不可用", provider=self.name)
            post = self.http_client.post
            if inspect.iscoroutinefunction(post):
                response = await post(self.BASE_URL, data=payload)
            else:
                response = await anyio.to_thread.run_sync(lambda: post(self.BASE_URL, data=payload))
            if hasattr(response, "__await__"):
                response = await response
            if hasattr(response, "raise_for_status"):
                response.raise_for_status()
            return response.json() if hasattr(response, "json") else response

        return await self.limiter.run(self.name, request)


def _normalise_payload(payload: Any, capability: Capability) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [dict(row) for row in payload]
    if isinstance(payload, dict):
        for key in ("data", "list", "rows", "result"):
            if isinstance(payload.get(key), list):
                return [dict(row) if isinstance(row, dict) else {"value": row} for row in payload[key]]
        # The guide's GetHQPlate response is object-shaped.  Preserve it as a
        # single row for capability-specific parsing without exposing Token.
        return [{key: value for key, value in payload.items() if key.lower() != "token"}]
    return []


_MODEL_BY_CAPABILITY = {
    Capability.KPL_LIMIT_LIST: KplLimitItem,
    Capability.KPL_CONCEPTS: KplConcept,
    Capability.KPL_CONCEPT_CONSTITUENTS: KplConceptConstituent,
    Capability.KPL_LIMIT_LADDER: KplLimitLadder,
    Capability.KPL_STRONG_SECTORS: KplStrongSector,
    Capability.MARKET_AUCTION_OPEN: AuctionOpen,
}


def _data_time(rows: list[Any], params: dict[str, Any]) -> datetime | None:
    for row in rows:
        value = getattr(row, "trade_date", None)
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


__all__ = ["KplNativeProvider"]
