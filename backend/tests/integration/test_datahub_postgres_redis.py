"""Real PostgreSQL 16 + Redis 7 evidence for the DataHub guarantees."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import anyio
import pytest
import redis
from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from app.datahub.cache import RedisDataCache
from app.datahub.config_service import DataHubConfigService
from app.datahub.errors import DataHubConflict
from app.datahub.ingestion import SnapshotStore
from app.datahub.limiter import RedisRateLimiter
from app.datahub.route_store import RouteStore
from app.models.datahub import DataSnapshot, DataSourceAuditEvent


class AsyncRedisAdapter:
    """Use the real synchronous redis-py client without blocking the loop."""

    def __init__(self, client: redis.Redis):
        self.client = client

    async def get(self, key: str):
        return await anyio.to_thread.run_sync(self.client.get, key)

    async def set(self, key: str, value, ex: int | None = None):
        return await anyio.to_thread.run_sync(lambda: self.client.set(key, value, ex=ex))

    async def incr(self, key: str):
        return await anyio.to_thread.run_sync(self.client.incr, key)

    async def expire(self, key: str, seconds: int):
        return await anyio.to_thread.run_sync(lambda: self.client.expire(key, seconds))

    async def ttl(self, key: str):
        return await anyio.to_thread.run_sync(self.client.ttl, key)

    async def eval(self, script: str, numkeys: int, *args):
        return await anyio.to_thread.run_sync(lambda: self.client.eval(script, numkeys, *args))

    async def delete(self, key: str):
        return await anyio.to_thread.run_sync(self.client.delete, key)


@pytest.mark.integration
def test_postgres_migration_exposes_datahub_tables(postgres_engine):
    tables = set(inspect(postgres_engine).get_table_names())
    assert {
        "data_source_configs",
        "data_source_routes",
        "data_source_probe_runs",
        "data_source_audit_events",
        "data_ingestion_runs",
        "data_snapshots",
    } <= tables


@pytest.mark.integration
def test_postgres_config_versions_and_snapshot_identity_are_durable(postgres_session_factory):
    first_session: Session = postgres_session_factory()
    second_session: Session = postgres_session_factory()
    try:
        first = DataHubConfigService(first_session, encryption_key=b"0123456789abcdef0123456789abcdef")
        saved = first.save_config(
            "tushare",
            public_config={},
            credentials={"token": "integration-token-a"},
            expected_version=None,
            actor_id=1,
        )
        assert saved.version == 1

        second = DataHubConfigService(second_session, encryption_key=b"0123456789abcdef0123456789abcdef")
        updated = second.save_config(
            "tushare",
            public_config={},
            credentials={"token": "integration-token-b"},
            expected_version=saved.version,
            actor_id=2,
        )
        assert updated.version == 2

        with pytest.raises(DataHubConflict):
            first.save_config(
                "tushare",
                public_config={},
                credentials={"token": "stale-token"},
                expected_version=saved.version,
                actor_id=1,
            )

        snapshots = SnapshotStore(second_session)
        first_snapshot = snapshots.upsert(
            "kpl.limit_list", "20260822", "all", "1.0", "tushare", [{"code": "000001"}]
        )
        second_snapshot = snapshots.upsert(
            "kpl.limit_list", "20260822", "all", "1.0", "tushare", [{"code": "000002"}]
        )
        assert first_snapshot.id == second_snapshot.id
        assert second_session.scalar(select(DataSnapshot).where(DataSnapshot.id == first_snapshot.id)).payload_json == [{"code": "000002"}]
        assert second_session.query(DataSourceAuditEvent).count() >= 2
    finally:
        first_session.close()
        second_session.close()


@pytest.mark.integration
def test_postgres_snapshot_upsert_is_atomic_under_concurrency(postgres_session_factory):
    """Two independent sessions must not race into a duplicate identity row."""

    barrier = Barrier(2)
    identity = ("kpl.limit_list", "20260822", "all", "1.0", "tushare")

    def write(payload):
        session: Session = postgres_session_factory()
        try:
            barrier.wait(timeout=10)
            return SnapshotStore(session).upsert(*identity, payload)
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(write, [{"code": code}]) for code in ("000001", "000002")]
        results = [future.result(timeout=30) for future in futures]

    assert results[0].id == results[1].id
    verify = postgres_session_factory()
    try:
        rows = verify.query(DataSnapshot).filter(
            DataSnapshot.dataset == identity[0],
            DataSnapshot.trade_date == identity[1],
            DataSnapshot.scope_key == identity[2],
            DataSnapshot.schema_version == identity[3],
            DataSnapshot.source == identity[4],
        ).all()
        assert len(rows) == 1
        assert rows[0].payload_json in ([{"code": "000001"}], [{"code": "000002"}])
    finally:
        verify.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_redis_rate_budget_is_shared_and_cache_survives_restart(isolated_redis_server):
    client = redis.Redis.from_url(isolated_redis_server.url, decode_responses=True)
    adapter = AsyncRedisAdapter(client)
    cache = RedisDataCache(adapter)
    limiter = RedisRateLimiter(adapter, limit=3, window_seconds=30)
    try:
        allowed = await asyncio.gather(*(limiter.allow("tencent") for _ in range(8)))
        assert sum(allowed) == 3
        assert await adapter.ttl("datahub:rate:tencent") > 0

        await cache.set("indices", '{"price":3000}', 60, last_good_ttl_seconds=120)
        assert await cache.get("indices") == '{"price":3000}'

        isolated_redis_server.stop()
        # Redis is unavailable, but the process-local TTL cache remains usable.
        assert await cache.get("indices") == '{"price":3000}'
        isolated_redis_server.start()
        await cache.set("indices", '{"price":3001}', 60, last_good_ttl_seconds=120)
        assert await cache.get("indices") == '{"price":3001}'
    finally:
        client.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_redis_circuit_breaker_is_shared_and_atomic(isolated_redis_server):
    from app.datahub.limiter import RedisCircuitBreaker

    client = redis.Redis.from_url(isolated_redis_server.url, decode_responses=True)
    adapter = AsyncRedisAdapter(client)
    first = RedisCircuitBreaker(adapter, failure_threshold=2, recovery_seconds=30)
    second = RedisCircuitBreaker(adapter, failure_threshold=2, recovery_seconds=30)
    try:
        assert await second.allow("eastmoney") is True
        await asyncio.gather(first.record_failure("eastmoney"), second.record_failure("eastmoney"))
        assert await first.allow("eastmoney") is False
        assert await second.allow("eastmoney") is False
        await first.record_success("eastmoney")
        assert await second.allow("eastmoney") is True
    finally:
        client.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_route_hint_invalidation_converges_to_postgres(postgres_session_factory, isolated_redis_server):
    session = postgres_session_factory()
    client = redis.Redis.from_url(isolated_redis_server.url, decode_responses=True)
    adapter = AsyncRedisAdapter(client)
    try:
        service = DataHubConfigService(session, encryption_key=b"0123456789abcdef0123456789abcdef")
        route = service.save_route(
            "market.indices",
            mode="auto",
            providers=["tencent"],
            expected_version=None,
            actor_id=1,
        )
        store = RouteStore(session, redis_client=adapter)
        await store.hint_generation("market.indices", route.version)
        assert await adapter.get("datahub:route-generation:market.indices") == str(route.version)
        await store.invalidate("market.indices")
        assert await adapter.get("datahub:route-generation:market.indices") is None
        assert store.get("market.indices").providers == ["tencent"]
    finally:
        session.close()
        client.close()
