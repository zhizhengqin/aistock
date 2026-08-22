from __future__ import annotations

import time
from datetime import datetime, timedelta
from dataclasses import dataclass
from typing import Any
from zoneinfo import ZoneInfo

from app.datahub.contracts import Capability, DataResult
from app.datahub.errors import DataHubError, DataHubErrorCode
from app.datahub.limiter import ProviderLimiter


@dataclass(frozen=True)
class ProbeResult:
    status: str
    rows: int = 0
    latency_ms: int = 0
    error_code: str | None = None
    safe_sample: dict[str, Any] | None = None


class ProviderAdapter:
    name = "provider"

    def __init__(self, *, limiter: ProviderLimiter | None = None) -> None:
        self.limiter = limiter or ProviderLimiter()

    async def run_sync(self, operation, *, timeout: float | None = None):
        """Run a blocking provider SDK/HTTP operation in a bounded worker."""

        return await self.limiter.run_sync(self.name, operation, timeout=timeout)

    async def run_async(self, operation, *, timeout: float | None = None):
        return await self.limiter.run(self.name, operation, timeout=timeout)

    async def fetch(self, capability: Capability | str, params: dict[str, Any]) -> DataResult:
        raise NotImplementedError

    async def probe(self, capability: Capability | str, params: dict[str, Any] | None = None) -> ProbeResult:
        started = time.perf_counter()
        try:
            capability = Capability(capability)
            result = await self.fetch(capability, dict(params or capability_probe_params(capability)))
            return ProbeResult(
                status="ok",
                rows=result.quality.rows,
                latency_ms=round((time.perf_counter() - started) * 1000),
                safe_sample=_safe_sample(result.data),
            )
        except DataHubError as exc:
            return ProbeResult(
                status="error",
                latency_ms=round((time.perf_counter() - started) * 1000),
                error_code=exc.code.value,
            )
        except Exception:
            # Probes are an operator-facing health check.  Never leak an
            # adapter traceback or request material through the admin API.
            return ProbeResult(
                status="error",
                latency_ms=round((time.perf_counter() - started) * 1000),
                error_code=DataHubErrorCode.INTERNAL.value,
            )


def _recent_trade_date() -> str:
    current = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    while current.weekday() >= 5:
        current -= timedelta(days=1)
    return current.strftime("%Y%m%d")


def capability_probe_params(capability: Capability | str) -> dict[str, Any]:
    """Return deterministic, credential-free sample parameters for probes.

    The same mapping is used by the admin API and the CLI.  Parameters are
    deliberately conservative: one public stock, one representative board,
    the default index set, and a configured public RSS feed.  Providers may
    still reject a capability (for example an unavailable TDX/KPL source),
    but they are no longer probed with an empty request that can never be
    valid for their real protocol.
    """

    capability = Capability(capability)
    trade_date = _recent_trade_date()
    if capability is Capability.MARKET_INDICES:
        return {"codes": ["000001.SS", "399001.SZ", "399006.SZ", "000300.SS", "000688.SS"]}
    if capability in {
        Capability.STOCK_SNAPSHOT,
        Capability.STOCK_FINANCIALS,
        Capability.STOCK_SHAREHOLDERS,
    }:
        values: dict[str, Any] = {"code": "600000.SS"}
        return values
    if capability in {Capability.DRAGON_TIGER_LIST, Capability.DRAGON_TIGER_SEATS}:
        # Let the datacenter return its latest records.  A stock-specific
        # filter or a guessed weekday can legitimately have no LHB row.
        return {"limit": 1}
    if capability is Capability.STOCK_KLINE_DAILY:
        return {"code": "600000.SS", "days": 5}
    if capability is Capability.STOCK_FUND_FLOW:
        return {"code": "600000.SS", "days": 5, "period": "daily"}
    if capability is Capability.STOCK_NEWS:
        return {"code": "600000.SS", "source": "华尔街见闻", "limit": 1}
    if capability in {Capability.MARKET_SECTOR_OVERVIEW, Capability.SECTOR_REALTIME, Capability.SECTOR_FUND_FLOW, Capability.MARKET_FUND_FLOW_RANK}:
        return {"limit": 1}
    if capability is Capability.SECTOR_KLINE:
        return {"code": "BK0475", "days": 5}
    if capability in {
        Capability.KPL_LIMIT_LIST,
        Capability.KPL_CONCEPTS,
        Capability.KPL_CONCEPT_CONSTITUENTS,
        Capability.KPL_LIMIT_LADDER,
        Capability.KPL_STRONG_SECTORS,
        Capability.MARKET_AUCTION_OPEN,
    }:
        return {"trade_date": trade_date}
    return {}


def _safe_sample(data: Any) -> dict[str, Any] | None:
    if isinstance(data, list) and data:
        row = data[0]
    else:
        row = data
    if hasattr(row, "model_dump"):
        row = row.model_dump()
    if not isinstance(row, dict):
        return None
    safe: dict[str, Any] = {}
    for key, value in row.items():
        if "token" in key.lower() or "secret" in key.lower() or "key" in key.lower():
            continue
        safe[key] = value
    return safe


def translate_provider_error(exc: Exception, *, provider: str) -> DataHubError:
    if isinstance(exc, DataHubError):
        return exc
    if isinstance(exc, TimeoutError):
        return DataHubError(DataHubErrorCode.TIMEOUT, "数据源请求超时，请稍后重试", provider=provider, provider_detail=exc)
    status_code = getattr(getattr(exc, "response", None), "status_code", None) or getattr(exc, "status_code", None)
    if status_code == 403:
        return DataHubError(DataHubErrorCode.IP_BLOCKED, "数据源访问被风控拦截，请稍后重试或切换备用源", provider=provider, provider_detail=exc)
    if status_code == 429:
        return DataHubError(DataHubErrorCode.RATE_LIMITED, "数据源请求过于频繁，请稍后重试", provider=provider, provider_detail=exc)
    if status_code in {401, 407}:
        return DataHubError(DataHubErrorCode.AUTHENTICATION_FAILED, "数据源鉴权失败，请重新配置凭据", provider=provider, provider_detail=exc)
    text = str(exc).lower()
    if any(marker in text for marker in ("积分", "permission", "权限", "quota")):
        code = DataHubErrorCode.PERMISSION_DENIED
        message = "数据源权限不足，请检查该能力的积分或授权"
    elif any(marker in text for marker in ("token", "unauthorized", "401", "鉴权")):
        code = DataHubErrorCode.AUTHENTICATION_FAILED
        message = "数据源鉴权失败，请重新配置凭据"
    elif any(marker in text for marker in ("timeout", "timed out", "超时")):
        code = DataHubErrorCode.TIMEOUT
        message = "数据源请求超时，请稍后重试"
    elif any(marker in text for marker in ("empty", "no data", "空数据")):
        code = DataHubErrorCode.EMPTY_INVALID
        message = "数据源暂时没有有效数据"
    else:
        code = DataHubErrorCode.INTERNAL
        message = "数据源请求失败，请稍后重试"
    return DataHubError(code, message, provider=provider, provider_detail=exc)


__all__ = ["ProbeResult", "ProviderAdapter", "capability_probe_params", "translate_provider_error"]
