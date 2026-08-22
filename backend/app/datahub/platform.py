"""Application-level DataHub composition helpers."""

from __future__ import annotations

import hashlib
import json
import anyio
import time
from dataclasses import replace

from app.datahub.contracts import Capability
from app.datahub.providers.akshare import AkshareProvider
from app.datahub.providers.eastmoney import EastmoneyProvider
from app.datahub.providers.kpl_native import KplNativeProvider
from app.datahub.providers.official import OfficialProvider
from app.datahub.providers.sina import SinaProvider
from app.datahub.providers.tdx import TdxProvider
from app.datahub.providers.tushare import TushareProvider
from app.datahub.providers.tencent import TencentProvider
from app.datahub.providers.rss import RssProvider
from app.datahub.router import DataHubRouter, RouteDefinition
from app.datahub.runtime import InMemoryDataCache, RedisDataCache
from app.datahub.limiter import ProviderLimiter, RedisCircuitBreaker, RedisRateLimiter
from app.datahub.registry import PROVIDER_REGISTRY
from app.datahub.credentials import CredentialCipher, CredentialEnvelope
from app.core.config import settings


def default_routes() -> dict[Capability, RouteDefinition]:
    return {
        Capability.MARKET_INDICES: RouteDefinition(providers=["tencent", "sina"], ttl_seconds=60, stale_ttl_seconds=900),
        Capability.MARKET_BOARD_QUOTES: RouteDefinition(providers=["eastmoney", "sina"], ttl_seconds=300, stale_ttl_seconds=3600),
        Capability.MARKET_BOARD_CONSTITUENTS: RouteDefinition(providers=["eastmoney", "sina"], ttl_seconds=300, stale_ttl_seconds=3600),
        Capability.STOCK_SNAPSHOT: RouteDefinition(providers=["tencent", "sina"], ttl_seconds=30, stale_ttl_seconds=900),
        Capability.STOCK_KLINE_DAILY: RouteDefinition(providers=["tencent", "tdx"], ttl_seconds=300, stale_ttl_seconds=86400),
        Capability.STOCK_FINANCIALS: RouteDefinition(providers=["sina"], ttl_seconds=3600, stale_ttl_seconds=604800),
        Capability.STOCK_FUND_FLOW: RouteDefinition(providers=["eastmoney"], ttl_seconds=300, stale_ttl_seconds=3600),
        Capability.STOCK_NEWS: RouteDefinition(providers=["rss", "eastmoney"], ttl_seconds=1800, stale_ttl_seconds=86400),
        Capability.MARKET_FUND_FLOW_RANK: RouteDefinition(providers=["eastmoney"], ttl_seconds=300, stale_ttl_seconds=3600),
        Capability.STOCK_SHAREHOLDERS: RouteDefinition(providers=["eastmoney"], ttl_seconds=86400, stale_ttl_seconds=604800),
        Capability.SECTOR_REALTIME: RouteDefinition(providers=["eastmoney"], ttl_seconds=300, stale_ttl_seconds=3600),
        Capability.SECTOR_KLINE: RouteDefinition(providers=["eastmoney"], ttl_seconds=1800, stale_ttl_seconds=86400),
        Capability.SECTOR_FUND_FLOW: RouteDefinition(providers=["eastmoney"], ttl_seconds=300, stale_ttl_seconds=3600),
        Capability.DRAGON_TIGER_LIST: RouteDefinition(providers=["eastmoney"], ttl_seconds=1800, stale_ttl_seconds=86400),
        Capability.DRAGON_TIGER_SEATS: RouteDefinition(providers=["eastmoney"], ttl_seconds=1800, stale_ttl_seconds=86400),
        Capability.KPL_LIMIT_LIST: RouteDefinition(providers=["tushare"], ttl_seconds=1800, stale_ttl_seconds=86400),
        Capability.KPL_CONCEPTS: RouteDefinition(providers=["tushare"], ttl_seconds=1800, stale_ttl_seconds=86400),
        Capability.KPL_CONCEPT_CONSTITUENTS: RouteDefinition(providers=["tushare"], ttl_seconds=1800, stale_ttl_seconds=86400),
        Capability.KPL_LIMIT_LADDER: RouteDefinition(providers=["tushare"], ttl_seconds=1800, stale_ttl_seconds=86400),
        Capability.KPL_STRONG_SECTORS: RouteDefinition(providers=["tushare"], ttl_seconds=1800, stale_ttl_seconds=86400),
        Capability.MARKET_AUCTION_OPEN: RouteDefinition(providers=["tushare"], ttl_seconds=60, stale_ttl_seconds=900),
    }


def build_router(
    *,
    ak_module=None,
    tushare_token: str = "",
    kpl_token: str = "",
    redis_client=None,
    cache: InMemoryDataCache | None = None,
) -> DataHubRouter:
    """Build the in-process router; credentials remain caller-owned."""

    limiter = ProviderLimiter()
    providers = {
        "akshare": AkshareProvider(ak_module=ak_module, limiter=limiter),
        "tencent": TencentProvider(limiter=limiter),
        "eastmoney": EastmoneyProvider(limiter=limiter),
        "sina": SinaProvider(limiter=limiter),
        "official": OfficialProvider(limiter=limiter),
        "tdx": TdxProvider(limiter=limiter),
        "rss": RssProvider(limiter=limiter),
    }
    providers["tushare"] = TushareProvider(token=tushare_token, limiter=limiter)
    providers["kpl_native"] = KplNativeProvider(token=kpl_token, limiter=limiter)
    if cache is None:
        if redis_client is None:
            try:
                from app.core.redis import redis_client as configured_redis

                redis_client = configured_redis
            except Exception:
                redis_client = None
        cache = RedisDataCache(redis_client)
    return DataHubRouter(
        providers,
        default_routes(),
        cache=cache,
        route_loader=_DATABASE_ROUTE_LOADER,
        provider_states={name: definition.enabled_by_default for name, definition in PROVIDER_REGISTRY.items()},
        rate_limiter=RedisRateLimiter(redis_client, limit=60, window_seconds=60) if redis_client is not None else RedisRateLimiter(None, limit=20, window_seconds=60),
        circuit_breaker=RedisCircuitBreaker(redis_client, failure_threshold=3, recovery_seconds=30),
    )


def _load_database_routes_sync():
    """Read routes/config state from PostgreSQL as the runtime fact source.

    A worker may start before the database is reachable; in that case the
    immutable registry defaults remain active and the next fetch retries the
    database read.
    """

    try:
        from sqlalchemy import select

        from app.core.database import SessionLocal
        from app.models.datahub import DataSourceConfig, DataSourceRoute

        db = SessionLocal()
        try:
            configs = {row.provider: row for row in db.scalars(select(DataSourceConfig)).all()}
            states = {
                name: (configs[name].enabled if name in configs else definition.enabled_by_default)
                for name, definition in PROVIDER_REGISTRY.items()
            }
            credentials = {
                name: _decrypt_runtime_credentials(row)
                for name, row in configs.items()
                if row.encrypted_credentials
            }
            routes = _merge_database_routes(default_routes(), db.scalars(select(DataSourceRoute)).all(), configs)
            return routes, states, credentials
        finally:
            db.close()
    except Exception as exc:
        from app.datahub.errors import DataHubError, DataHubErrorCode

        raise DataHubError(DataHubErrorCode.INTERNAL, "数据源路由配置暂时不可读取", provider_detail=exc) from None


class _DatabaseRouteLoader:
    def __init__(self, ttl_seconds: float = 2.0) -> None:
        self.ttl_seconds = ttl_seconds
        self._loaded_at = 0.0
        self._value = None
        self._lock = None

    async def __call__(self):
        now = time.monotonic()
        if self._value is not None and now - self._loaded_at < self.ttl_seconds:
            return self._value
        if self._lock is None:
            import asyncio

            self._lock = asyncio.Lock()
        async with self._lock:
            now = time.monotonic()
            if self._value is not None and now - self._loaded_at < self.ttl_seconds:
                return self._value
            try:
                value = await anyio.to_thread.run_sync(_load_database_routes_sync)
            except Exception as exc:
                from app.datahub.errors import DataHubError, DataHubErrorCode

                if isinstance(exc, DataHubError):
                    raise
                raise DataHubError(DataHubErrorCode.INTERNAL, "数据源路由配置暂时不可读取", provider_detail=exc) from None
            self._value = value
            self._loaded_at = time.monotonic()
            return value

    def invalidate(self) -> None:
        self._loaded_at = 0.0


_DATABASE_ROUTE_LOADER = _DatabaseRouteLoader()


def _decrypt_runtime_credentials(row) -> dict[str, str]:
    configured = getattr(settings, "DATAHUB_CONFIG_ENCRYPTION_KEY", "")
    if not configured:
        if str(getattr(settings, "ENV", "")).lower() in {"prod", "production"}:
            raise RuntimeError("DataHub encryption key is required in production")
        configured = f"dev:{getattr(settings, 'JWT_SECRET', '')}"
    cipher = CredentialCipher.from_key(hashlib.sha256(configured.encode("utf-8")).digest(), key_id="datahub-current")
    try:
        envelope = CredentialEnvelope(**json.loads(row.encrypted_credentials))
        plaintext = cipher.decrypt(envelope, aad=b"datahub:credentials:v1")
        payload = json.loads(plaintext)
        return payload if isinstance(payload, dict) else {}
    except Exception as exc:
        raise RuntimeError("DataHub credentials could not be decrypted") from exc


def _provider_config_fingerprint(provider: str, row: object | None) -> str:
    """Return a stable, secret-free fingerprint for a provider configuration."""

    configured = getattr(row, "fingerprint", None) if row is not None else None
    if configured:
        return str(configured)
    public = getattr(row, "public_config_json", None) if row is not None else None
    encoded = json.dumps(public or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(f"{provider}|{encoded}|v1".encode("utf-8")).hexdigest()[:32]


def _route_fingerprint(providers: list[str], configs: dict[str, object]) -> str:
    values = [f"{name}:{_provider_config_fingerprint(name, configs.get(name))}" for name in providers]
    return hashlib.sha256("|".join(values).encode("utf-8")).hexdigest()[:24]


def _merge_database_routes(
    base_routes: dict[Capability, RouteDefinition],
    rows: list[object],
    configs: dict[str, object],
) -> dict[Capability, RouteDefinition]:
    """Apply database route overrides without losing capability cache policy.

    PostgreSQL owns provider order/mode and versions.  The immutable code
    defaults continue to own the capability-specific TTLs, so a route row
    cannot accidentally turn a financial or daily-K response into a 30s/15m
    cache.  Provider configuration fingerprints are applied to *all* routes,
    including defaults, so credential rotation invalidates their cache keys.
    """

    routes: dict[Capability, RouteDefinition] = {}
    for capability, base in base_routes.items():
        generation = max(
            [int(getattr(configs.get(name), "version", 0) or 0) for name in base.providers] or [0]
        )
        routes[capability] = replace(
            base,
            generation=generation,
            provider_fingerprint=_route_fingerprint(base.providers, configs),
        )
    for row in rows:
        try:
            capability = Capability(getattr(row, "capability"))
        except (TypeError, ValueError):
            # Database rows can outlive a removed capability enum.  Ignore
            # those historical overrides while retaining every code default.
            continue
        base = routes.get(capability)
        if base is None:
            continue
        providers = list(getattr(row, "provider_order_json", None) or [])
        if not providers:
            continue
        routes[capability] = replace(
            base,
            mode=str(getattr(row, "mode", base.mode)),
            providers=providers,
            contract_version=str(getattr(row, "contract_version", base.contract_version)),
            generation=int(getattr(row, "version", 0) or 0),
            provider_fingerprint=_route_fingerprint(providers, configs),
        )
    return routes


_default_router: DataHubRouter | None = None


def get_datahub_router() -> DataHubRouter:
    """Return the process-scoped router used by business consumers."""

    global _default_router
    if _default_router is None:
        _default_router = build_router()
    return _default_router


def set_datahub_router(router: DataHubRouter | None) -> None:
    """Replace the process router in tests and controlled worker startup."""

    global _default_router
    _default_router = router


def invalidate_datahub_routes() -> None:
    _DATABASE_ROUTE_LOADER.invalidate()
    if _default_router is not None:
        _default_router.routes = default_routes()


__all__ = ["build_router", "default_routes", "get_datahub_router", "set_datahub_router"]
