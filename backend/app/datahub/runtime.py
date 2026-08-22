"""Cache primitives with last-good and Redis-degraded behaviour."""

from __future__ import annotations

import asyncio
import inspect
import json
import time
from dataclasses import dataclass
from typing import Any

import anyio


@dataclass
class _Entry:
    value: Any
    expires_at: float


async def redis_call(client: Any, method_name: str, *args: Any, **kwargs: Any) -> Any:
    """Call either redis-py sync or redis.asyncio clients without blocking."""
    method = getattr(client, method_name)
    if inspect.iscoroutinefunction(method):
        return await method(*args, **kwargs)
    result = await anyio.to_thread.run_sync(lambda: method(*args, **kwargs))
    if inspect.isawaitable(result):
        return await result
    return result


def _encode_cache_value(value: Any) -> str:
    if hasattr(value, "model_dump"):
        return json.dumps(
            {"__datahub_type__": "result", "value": value.model_dump(mode="json", exclude={"meta"})},
            ensure_ascii=False,
            separators=(",", ":"),
        )
    return json.dumps({"__datahub_type__": "raw", "value": value}, ensure_ascii=False, default=str, separators=(",", ":"))


def _decode_cache_value(value: Any) -> Any:
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    if not isinstance(value, str):
        return value
    try:
        payload = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return value
    if not isinstance(payload, dict) or "__datahub_type__" not in payload:
        return value
    if payload.get("__datahub_type__") == "result":
        from app.datahub.contracts import (
            AuctionOpen,
            Capability,
            DataResult,
            DragonTigerItem,
            DragonTigerSeat,
            FinancialSummary,
            FundFlow,
            FundFlowRankItem,
            KlineBar,
            KplConcept,
            KplConceptConstituent,
            KplLimitItem,
            KplLimitLadder,
            KplStrongSector,
            MarketIndex,
            NewsItem,
            SectorFlow,
            SectorOverview,
            SectorQuote,
            ShareholderSummary,
            StockSnapshot,
        )

        try:
            value = payload.get("value") or {}
            capability = Capability(value.get("capability"))
            models = {
                Capability.MARKET_INDICES: list[MarketIndex],
                Capability.MARKET_SECTOR_OVERVIEW: list[SectorOverview],
                Capability.STOCK_SNAPSHOT: StockSnapshot,
                Capability.STOCK_KLINE_DAILY: list[KlineBar],
                Capability.STOCK_FINANCIALS: FinancialSummary,
                Capability.STOCK_FUND_FLOW: FundFlow,
                Capability.STOCK_NEWS: list[NewsItem],
                Capability.MARKET_FUND_FLOW_RANK: list[FundFlowRankItem],
                Capability.STOCK_SHAREHOLDERS: ShareholderSummary,
                Capability.SECTOR_REALTIME: list[SectorQuote],
                Capability.SECTOR_KLINE: list[KlineBar],
                Capability.SECTOR_FUND_FLOW: list[SectorFlow],
                Capability.DRAGON_TIGER_LIST: list[DragonTigerItem],
                Capability.DRAGON_TIGER_SEATS: list[DragonTigerSeat],
                Capability.KPL_LIMIT_LIST: list[KplLimitItem],
                Capability.KPL_CONCEPTS: list[KplConcept],
                Capability.KPL_CONCEPT_CONSTITUENTS: list[KplConceptConstituent],
                Capability.KPL_LIMIT_LADDER: list[KplLimitLadder],
                Capability.KPL_STRONG_SECTORS: list[KplStrongSector],
                Capability.MARKET_AUCTION_OPEN: list[AuctionOpen],
            }
            return DataResult[models[capability]].model_validate(value)
        except Exception:
            return None
    return payload.get("value")


class InMemoryDataCache:
    """Small process cache used as a safe fallback when Redis is unavailable."""

    def __init__(self) -> None:
        self._fresh: dict[str, _Entry] = {}
        self._last_good: dict[str, _Entry] = {}
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> Any | None:
        async with self._lock:
            entry = self._fresh.get(key)
            if entry and entry.expires_at > time.monotonic():
                return entry.value
            if entry:
                self._fresh.pop(key, None)
            return None

    async def set(self, key: str, value: Any, ttl_seconds: int, *, last_good_ttl_seconds: int | None = None) -> None:
        now = time.monotonic()
        async with self._lock:
            self._fresh[key] = _Entry(value, now + max(ttl_seconds, 1))
            self._last_good[key] = _Entry(
                value,
                now + max(last_good_ttl_seconds if last_good_ttl_seconds is not None else ttl_seconds, 1),
            )

    async def get_last_good(self, key: str) -> Any | None:
        async with self._lock:
            entry = self._last_good.get(key)
            if entry and entry.expires_at > time.monotonic():
                return entry.value
            if entry:
                self._last_good.pop(key, None)
            return None

    async def delete(self, key: str) -> None:
        async with self._lock:
            self._fresh.pop(key, None)
            self._last_good.pop(key, None)


class RedisDataCache(InMemoryDataCache):
    """Best-effort Redis cache backed by the process fallback.

    Values are serialized by the caller (usually JSON).  Any Redis error is
    intentionally swallowed after falling back to the local TTL cache.
    """

    def __init__(self, redis_client: Any | None = None) -> None:
        super().__init__()
        self.redis = redis_client

    async def get(self, key: str) -> Any | None:
        if self.redis is not None:
            try:
                value = await redis_call(self.redis, "get", f"datahub:fresh:{key}")
                if value is not None:
                    decoded = _decode_cache_value(value)
                    if decoded is not None:
                        return decoded
            except Exception:
                pass
        return await super().get(key)

    async def set(self, key: str, value: Any, ttl_seconds: int, *, last_good_ttl_seconds: int | None = None) -> None:
        if self.redis is not None:
            try:
                encoded = _encode_cache_value(value)
                await redis_call(self.redis, "set", f"datahub:fresh:{key}", encoded, ex=ttl_seconds)
                await redis_call(
                    self.redis,
                    "set",
                    f"datahub:last-good:{key}",
                    encoded,
                    ex=last_good_ttl_seconds or ttl_seconds,
                )
            except Exception:
                pass
        await super().set(key, value, ttl_seconds, last_good_ttl_seconds=last_good_ttl_seconds)

    async def get_last_good(self, key: str) -> Any | None:
        if self.redis is not None:
            try:
                value = await redis_call(self.redis, "get", f"datahub:last-good:{key}")
                if value is not None:
                    decoded = _decode_cache_value(value)
                    if decoded is not None:
                        return decoded
            except Exception:
                pass
        return await super().get_last_good(key)

    async def delete(self, key: str) -> None:
        if self.redis is not None:
            try:
                await redis_call(self.redis, "delete", f"datahub:fresh:{key}", f"datahub:last-good:{key}")
            except Exception:
                pass
        await super().delete(key)


__all__ = ["InMemoryDataCache", "RedisDataCache", "redis_call"]
