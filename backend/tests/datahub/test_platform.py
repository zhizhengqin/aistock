from app.datahub.platform import build_router
from app.datahub.runtime import RedisDataCache


def test_build_router_uses_named_adapters_and_degraded_redis_cache():
    class FakeAk:
        pass

    router = build_router(ak_module=FakeAk(), redis_client=None)
    assert router.providers["tencent"].name == "tencent"
    assert router.providers["eastmoney"].name == "eastmoney"
    assert isinstance(router.cache, RedisDataCache)

