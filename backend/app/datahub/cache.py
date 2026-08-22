"""Compatibility imports for DataHub cache implementations."""

from app.datahub.runtime import InMemoryDataCache, RedisDataCache

DataHubCache = InMemoryDataCache

__all__ = ["DataHubCache", "InMemoryDataCache", "RedisDataCache"]
