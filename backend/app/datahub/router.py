"""Capability router with auto/fixed routing, fallback and request coalescing."""

from __future__ import annotations

import asyncio
import inspect
import json
import time
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Any

from app.datahub.contracts import Capability, DataResult, DataQuality, Freshness
from app.datahub.errors import DataHubError, DataHubErrorCode
from app.datahub.runtime import InMemoryDataCache
from app.datahub.validators import validate_payload


@dataclass(frozen=True)
class RouteDefinition:
    mode: str = "auto"
    providers: list[str] = field(default_factory=list)
    contract_version: str = "1.0"
    ttl_seconds: int = 30
    stale_ttl_seconds: int = 900
    generation: int = 0
    provider_fingerprint: str = ""

    def __post_init__(self) -> None:
        if self.mode not in {"auto", "fixed"}:
            raise ValueError("路由模式必须为 auto 或 fixed")
        if not self.providers:
            raise ValueError("路由至少需要一个数据源")


_RETRYABLE = {
    DataHubErrorCode.TIMEOUT,
    DataHubErrorCode.RATE_LIMITED,
    DataHubErrorCode.IP_BLOCKED,
    DataHubErrorCode.SCHEMA_CHANGED,
    DataHubErrorCode.EMPTY_INVALID,
    DataHubErrorCode.INTERNAL,
    DataHubErrorCode.UNSUPPORTED,
}


class DataHubRouter:
    def __init__(
        self,
        providers: dict[str, Any],
        routes: dict[Capability | str, RouteDefinition],
        *,
        cache: InMemoryDataCache | None = None,
        route_loader=None,
        rate_limiter=None,
        circuit_breaker=None,
        provider_states: dict[str, bool] | None = None,
        clock=None,
    ) -> None:
        self.providers = providers
        self.routes = {Capability(key): route for key, route in routes.items()}
        self.cache = cache or InMemoryDataCache()
        self.route_loader = route_loader
        self.rate_limiter = rate_limiter
        self.circuit_breaker = circuit_breaker
        self.provider_states = dict(provider_states or {})
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self._locks: dict[str, asyncio.Lock] = {}
        self._lock_refs: dict[str, int] = {}
        self._lock_registry_guard = asyncio.Lock()
        self._route_refresh_lock = asyncio.Lock()

    async def refresh_routes(self) -> None:
        if self.route_loader is None:
            return
        async with self._route_refresh_lock:
            loaded = self.route_loader()
            if inspect.isawaitable(loaded):
                loaded = await loaded
            if not loaded:
                return
            states = None
            credentials = None
            if isinstance(loaded, tuple):
                loaded, states, *extra = loaded
                credentials = extra[0] if extra else None
            self.routes = {Capability(key): route for key, route in loaded.items()}
            if states is not None:
                self.provider_states = dict(states)
            if credentials:
                for provider_name, values in credentials.items():
                    provider = self.providers.get(provider_name)
                    if provider is not None and hasattr(provider, "update_credentials"):
                        provider.update_credentials(values)

    def _cache_key(self, capability: Capability, params: dict[str, Any], route: RouteDefinition) -> str:
        clean = {key: value for key, value in params.items() if key != "force_refresh"}
        return json.dumps(
            {
                "capability": capability.value,
                "params": clean,
                "contract": route.contract_version,
                "generation": route.generation,
                "provider_fingerprint": route.provider_fingerprint,
            },
            ensure_ascii=False,
            sort_keys=True,
            default=str,
            separators=(",", ":"),
        )

    async def fetch(
        self,
        capability: Capability | str,
        params: dict[str, Any] | None = None,
        *,
        request_id: str | None = None,
    ) -> DataResult:
        capability = Capability(capability)
        params = dict(params or {})
        await self.refresh_routes()
        route = self.routes.get(capability)
        if route is None:
            raise DataHubError(DataHubErrorCode.UNSUPPORTED, "尚未配置该数据能力", request_id=request_id)
        key = self._cache_key(capability, params, route)
        force_refresh = bool(params.get("force_refresh"))
        if not force_refresh:
            cached = await self.cache.get(key)
            if cached is not None:
                return cached

        async with self._lock_registry_guard:
            lock = self._locks.setdefault(key, asyncio.Lock())
            self._lock_refs[key] = self._lock_refs.get(key, 0) + 1
        try:
            async with lock:
                if not force_refresh:
                    cached = await self.cache.get(key)
                    if cached is not None:
                        return cached
                attempts: list[str] = []
                last_error: DataHubError | None = None
                for provider_name in route.providers:
                    provider = self.providers.get(provider_name)
                    attempts.append(provider_name)
                    if self.provider_states and not self.provider_states.get(provider_name, False):
                        not_configured = DataHubError(
                            DataHubErrorCode.NOT_CONFIGURED,
                            "数据源未启用",
                            provider=provider_name,
                            request_id=request_id,
                        )
                        if route.mode == "fixed":
                            raise not_configured
                        if last_error is None:
                            last_error = not_configured
                        continue
                    if provider is None:
                        not_configured = DataHubError(
                            DataHubErrorCode.NOT_CONFIGURED,
                            "数据源尚未配置",
                            provider=provider_name,
                            request_id=request_id,
                        )
                        if route.mode == "fixed":
                            raise not_configured
                        if last_error is None:
                            last_error = not_configured
                        continue
                    # Breaker state is checked before consuming shared rate budget.
                    if self.circuit_breaker is not None and not await self.circuit_breaker.allow(provider_name):
                        last_error = DataHubError(
                            DataHubErrorCode.RATE_LIMITED,
                            "数据源暂处于熔断保护期，请稍后重试",
                            provider=provider_name,
                            request_id=request_id,
                        )
                        if route.mode == "fixed":
                            raise last_error
                        continue
                    if self.rate_limiter is not None and not await self.rate_limiter.allow(provider_name):
                        last_error = DataHubError(
                            DataHubErrorCode.RATE_LIMITED,
                            "数据源共享限流已触发，请稍后重试",
                            provider=provider_name,
                            request_id=request_id,
                        )
                        if route.mode == "fixed":
                            raise last_error
                        continue
                    try:
                        started = time.perf_counter()
                        value = await provider.fetch(capability, params)
                        result = self._to_result(value, capability, provider_name, route, attempts, started, request_id)
                        await self.cache.set(
                            key,
                            result,
                            route.ttl_seconds,
                            last_good_ttl_seconds=route.stale_ttl_seconds,
                        )
                        if self.circuit_breaker is not None:
                            await self.circuit_breaker.record_success(provider_name)
                        return result
                    except DataHubError as exc:
                        last_error = exc
                        if self.circuit_breaker is not None and exc.code in _RETRYABLE:
                            await self.circuit_breaker.record_failure(provider_name)
                    except Exception as exc:
                        last_error = DataHubError(
                            DataHubErrorCode.INTERNAL,
                            "数据源请求失败，请稍后重试",
                            provider=provider_name,
                            provider_detail=exc,
                            request_id=request_id,
                        )
                        if self.circuit_breaker is not None:
                            await self.circuit_breaker.record_failure(provider_name)
                    if route.mode == "fixed" or last_error.code not in _RETRYABLE:
                        break

                stale = await self.cache.get_last_good(key)
                if stale is not None:
                    return stale.model_copy(
                        update={
                            "freshness": Freshness.STALE,
                            "fallback_used": bool(len(attempts) > 1),
                            "attempts": attempts,
                            "warnings": ["当前返回最近有效数据，数据可能已过期"],
                            "request_id": request_id or stale.request_id,
                        }
                    )
                if last_error is not None:
                    last_error.request_id = request_id or last_error.request_id
                    raise last_error
                raise DataHubError(DataHubErrorCode.INTERNAL, "没有可用数据源", request_id=request_id)
        finally:
            async with self._lock_registry_guard:
                refs = self._lock_refs.get(key, 1) - 1
                if refs <= 0:
                    self._lock_refs.pop(key, None)
                    if self._locks.get(key) is lock:
                        self._locks.pop(key, None)
                else:
                    self._lock_refs[key] = refs

    def _to_result(
        self,
        value: Any,
        capability: Capability,
        provider_name: str,
        route: RouteDefinition,
        attempts: list[str],
        started: float,
        request_id: str | None,
    ) -> DataResult:
        if isinstance(value, DataResult):
            result = value.model_copy(
                update={
                    "capability": capability,
                    "provider": provider_name,
                    "attempts": list(attempts),
                    "fallback_used": len(attempts) > 1,
                    "latency_ms": max(0, round((time.perf_counter() - started) * 1000)),
                    "contract_version": route.contract_version,
                    "request_id": request_id or value.request_id,
                }
            )
            validate_payload(capability, result.data)
            if result.data_at is None:
                raise DataHubError(DataHubErrorCode.STALE_INVALID, "数据源结果缺少可信数据时间", provider=provider_name)
            freshness = result.freshness
            warnings = list(result.warnings)
            data_at = result.data_at
            if data_at.tzinfo is None:
                data_at = data_at.replace(tzinfo=timezone.utc)
            age = (self.clock() - data_at).total_seconds()
            if age > _freshness_window(capability, route) and freshness is Freshness.FRESH:
                freshness = Freshness.STALE
                warnings.append("数据时间超过当前能力时效窗口")
            return result.model_copy(update={"data_at": data_at, "freshness": freshness, "warnings": warnings})
        rows = value if isinstance(value, list) else [value]
        row_count = validate_payload(capability, rows)
        data_at = _extract_data_at(rows)
        if data_at is None:
            raise DataHubError(DataHubErrorCode.STALE_INVALID, "数据源结果缺少可信数据时间", provider=provider_name)
        if data_at.tzinfo is None:
            data_at = data_at.replace(tzinfo=timezone.utc)
        freshness = Freshness.STALE if (self.clock() - data_at).total_seconds() > _freshness_window(capability, route) else Freshness.FRESH
        return DataResult(
            data=rows,
            capability=capability,
            provider=provider_name,
            data_at=data_at,
            latency_ms=max(0, round((time.perf_counter() - started) * 1000)),
            freshness=freshness,
            fallback_used=len(attempts) > 1,
            attempts=list(attempts),
            quality=DataQuality(valid=True, rows=row_count),
            contract_version=route.contract_version,
            request_id=request_id,
        )


__all__ = ["DataHubRouter", "RouteDefinition"]


_CALENDAR_CAPABILITY_WINDOWS = {
    Capability.STOCK_KLINE_DAILY: 3 * 86400,
    Capability.STOCK_FINANCIALS: 90 * 86400,
    Capability.STOCK_SHAREHOLDERS: 90 * 86400,
    Capability.KPL_LIMIT_LIST: 3 * 86400,
    Capability.KPL_CONCEPTS: 3 * 86400,
    Capability.KPL_CONCEPT_CONSTITUENTS: 3 * 86400,
    Capability.KPL_LIMIT_LADDER: 3 * 86400,
    Capability.KPL_STRONG_SECTORS: 3 * 86400,
    Capability.MARKET_AUCTION_OPEN: 3 * 86400,
    Capability.KPL_NATIVE_STOCK_TAGS: 3 * 86400,
    Capability.KPL_NATIVE_PLATE_RANKING: 3 * 86400,
    Capability.KPL_NATIVE_PLATE_CONSTITUENTS: 3 * 86400,
    Capability.KPL_NATIVE_STOCK_RANKING: 3 * 86400,
}


def _freshness_window(capability: Capability, route: RouteDefinition) -> int:
    """Use calendar-aware windows for daily/after-close data.

    A trade date at midnight must remain usable over a weekend or holiday;
    the route's short polling TTL controls cache expiry, while this window
    controls whether the upstream timestamp can still be considered fresh.
    """

    return max(route.ttl_seconds, _CALENDAR_CAPABILITY_WINDOWS.get(capability, route.ttl_seconds))


def _extract_data_at(rows: list[Any]) -> datetime | None:
    values: list[datetime] = []
    for row in rows:
        value = getattr(row, "data_at", None) if not isinstance(row, dict) else row.get("data_at")
        if not value and isinstance(row, dict):
            value = row.get("trade_date") or row.get("date") or row.get("datetime")
        if not value:
            continue
        if isinstance(value, datetime):
            parsed = value
        else:
            try:
                parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            except ValueError:
                continue
        values.append(parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc))
    return max(values) if values else None
