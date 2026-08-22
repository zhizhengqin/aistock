"""Concurrency, timeout and circuit-breaker controls for provider calls."""

from __future__ import annotations

import asyncio
import threading
import time
from contextlib import asynccontextmanager
from typing import Any, Awaitable, Callable, TypeVar

import anyio

from app.datahub.runtime import redis_call


T = TypeVar("T")


class CircuitBreaker:
    def __init__(self, *, failure_threshold: int = 3, recovery_seconds: float = 30.0) -> None:
        self.failure_threshold = max(1, failure_threshold)
        self.recovery_seconds = max(0.0, recovery_seconds)
        self._failures = 0
        self._opened_at: float | None = None
        self._lock = threading.Lock()

    def allow(self) -> bool:
        with self._lock:
            if self._opened_at is None:
                return True
            if time.monotonic() - self._opened_at >= self.recovery_seconds:
                return True
            return False

    def record_failure(self) -> None:
        with self._lock:
            self._failures += 1
            if self._failures >= self.failure_threshold:
                self._opened_at = time.monotonic()

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._opened_at = None


class ProviderLimiter:
    def __init__(
        self,
        *,
        provider_limit: int = 4,
        global_limit: int = 16,
        socket_timeout: float = 10.0,
    ) -> None:
        self.provider_limit = max(1, provider_limit)
        self.global_limit = max(1, global_limit)
        self.socket_timeout = max(0.1, socket_timeout)
        self._global = asyncio.Semaphore(self.global_limit)
        self._providers: dict[str, asyncio.Semaphore] = {}
        self._lock = asyncio.Lock()
        self.breakers: dict[str, CircuitBreaker] = {}

    async def _provider_semaphore(self, provider: str) -> asyncio.Semaphore:
        async with self._lock:
            return self._providers.setdefault(provider, asyncio.Semaphore(self.provider_limit))

    @asynccontextmanager
    async def slot(self, provider: str):
        breaker = self.breakers.setdefault(provider, CircuitBreaker())
        if not breaker.allow():
            raise RuntimeError("数据源熔断中，请稍后重试")
        semaphore = await self._provider_semaphore(provider)
        async with self._global, semaphore:
            try:
                yield
            except Exception:
                breaker.record_failure()
                raise
            else:
                breaker.record_success()

    async def run(self, provider: str, operation: Callable[[], Awaitable[T]], *, timeout: float | None = None) -> T:
        async with self.slot(provider):
            with anyio.fail_after(timeout or self.socket_timeout):
                return await operation()

    async def run_sync(self, provider: str, operation: Callable[[], T], *, timeout: float | None = None) -> T:
        async with self.slot(provider):
            with anyio.fail_after(timeout or self.socket_timeout):
                return await anyio.to_thread.run_sync(operation)


class RedisRateLimiter:
    """Best-effort shared request budget with a local conservative fallback."""

    def __init__(self, redis_client: Any | None, *, limit: int = 60, window_seconds: int = 60, local: ProviderLimiter | None = None) -> None:
        self.redis = redis_client
        self.limit = max(1, limit)
        self.window_seconds = max(1, window_seconds)
        self.local = local or ProviderLimiter(provider_limit=1, global_limit=2)
        self._local_events: dict[str, list[float]] = {}
        self._local_lock = asyncio.Lock()
        self._fixed_window_script = "local c=redis.call('INCR',KEYS[1]); if c==1 then redis.call('EXPIRE',KEYS[1],ARGV[1]); end; return c"

    async def allow(self, provider: str) -> bool:
        if self.redis is not None:
            try:
                key = f"datahub:rate:{provider}"
                if hasattr(self.redis, "eval"):
                    count = await redis_call(self.redis, "eval", self._fixed_window_script, 1, key, self.window_seconds)
                else:
                    # Minimal fixture/sync clients may not expose EVAL. Real
                    # Redis clients always take the atomic Lua path above.
                    count = await redis_call(self.redis, "incr", key)
                    if count == 1:
                        await redis_call(self.redis, "expire", key, self.window_seconds)
                return count <= self.limit
            except Exception:
                # Redis outage must not remove the public-IP safety guard.
                pass
        now = time.monotonic()
        async with self._local_lock:
            events = [stamp for stamp in self._local_events.get(provider, []) if now - stamp < self.window_seconds]
            if len(events) >= self.limit:
                self._local_events[provider] = events
                return False
            events.append(now)
            self._local_events[provider] = events
            return True


class RedisCircuitBreaker:
    """Cross-process circuit state with a local fallback during Redis outage."""

    def __init__(self, redis_client: Any | None, *, failure_threshold: int = 3, recovery_seconds: int = 30) -> None:
        self.redis = redis_client
        self.failure_threshold = max(1, failure_threshold)
        self.recovery_seconds = max(1, recovery_seconds)
        self._local: dict[str, CircuitBreaker] = {}
        self._failure_script = (
            "local count=redis.call('INCR',KEYS[1]); "
            "redis.call('EXPIRE',KEYS[1],ARGV[2]); "
            "if count >= tonumber(ARGV[1]) then "
            "redis.call('SET',KEYS[2],'1','EX',ARGV[2]); end; return count"
        )
        self._success_script = "redis.call('DEL',KEYS[1],KEYS[2]); return 1"

    def _local_breaker(self, provider: str) -> CircuitBreaker:
        return self._local.setdefault(provider, CircuitBreaker(failure_threshold=self.failure_threshold, recovery_seconds=self.recovery_seconds))

    async def allow(self, provider: str) -> bool:
        if self.redis is not None:
            try:
                return not bool(await redis_call(self.redis, "get", f"datahub:circuit:open:{provider}"))
            except Exception:
                pass
        return self._local_breaker(provider).allow()

    async def record_failure(self, provider: str) -> None:
        if self.redis is not None:
            try:
                key = f"datahub:circuit:fail:{provider}"
                open_key = f"datahub:circuit:open:{provider}"
                if hasattr(self.redis, "eval"):
                    await redis_call(
                        self.redis,
                        "eval",
                        self._failure_script,
                        2,
                        key,
                        open_key,
                        self.failure_threshold,
                        self.recovery_seconds,
                    )
                else:
                    count = await redis_call(self.redis, "incr", key)
                    await redis_call(self.redis, "expire", key, self.recovery_seconds)
                    if int(count) >= self.failure_threshold:
                        await redis_call(self.redis, "set", open_key, "1", ex=self.recovery_seconds)
                return
            except Exception:
                pass
        self._local_breaker(provider).record_failure()

    async def record_success(self, provider: str) -> None:
        if self.redis is not None:
            try:
                keys = (f"datahub:circuit:fail:{provider}", f"datahub:circuit:open:{provider}")
                if hasattr(self.redis, "eval"):
                    await redis_call(self.redis, "eval", self._success_script, len(keys), *keys)
                else:
                    await redis_call(self.redis, "delete", *keys)
                return
            except Exception:
                pass
        self._local_breaker(provider).record_success()


__all__ = ["CircuitBreaker", "ProviderLimiter", "RedisCircuitBreaker", "RedisRateLimiter"]
