"""Exchange-official fallback adapter.

Only explicitly supplied JSON fixtures/clients are accepted.  No AkShare
fallback is hidden behind this provider.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.datahub.contracts import Capability, DataResult, MarketIndex
from app.datahub.errors import DataHubError, DataHubErrorCode
from app.datahub.providers.base import ProviderAdapter, translate_provider_error
from app.datahub.validators import validate_payload


class OfficialProvider(ProviderAdapter):
    name = "official"

    def __init__(self, *, http_client: Any | None = None, limiter=None) -> None:
        super().__init__(limiter=limiter)
        self.http_client = http_client

    async def fetch(self, capability: Capability | str, params: dict[str, Any]) -> DataResult:
        capability = Capability(capability)
        if capability is not Capability.MARKET_INDICES:
            raise DataHubError(DataHubErrorCode.UNSUPPORTED, "交易所官方适配器暂不支持该数据能力", provider=self.name)
        if self.http_client is None:
            raise DataHubError(DataHubErrorCode.NOT_CONFIGURED, "交易所官方接口尚未配置", provider=self.name)
        try:
            payload = await self.run_sync(lambda: self._get_payload(params))
            raw = payload.get("data", payload) if isinstance(payload, dict) else payload
            rows = raw if isinstance(raw, list) else [raw]
            typed = [MarketIndex.model_validate(row) for row in rows if isinstance(row, dict)]
            count = validate_payload(capability, typed)
            data_at = max((item.data_at for item in typed if item.data_at), default=None)
            if data_at is None:
                raise DataHubError(DataHubErrorCode.STALE_INVALID, "官方指数响应缺少可信时间戳", provider=self.name)
            return DataResult(data=typed, capability=capability, provider=self.name, data_at=data_at, quality={"valid": True, "rows": count})
        except DataHubError:
            raise
        except Exception as exc:
            raise translate_provider_error(exc, provider=self.name) from None

    def _get_payload(self, params: dict[str, Any]) -> Any:
        if callable(self.http_client):
            return self.http_client(params)
        response = self.http_client.get("official://market-indices", params=params, timeout=10)
        if hasattr(response, "raise_for_status"):
            response.raise_for_status()
        return response.json() if hasattr(response, "json") else response


__all__ = ["OfficialProvider"]
