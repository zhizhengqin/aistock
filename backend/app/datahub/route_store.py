"""PostgreSQL-fact route reads with best-effort Redis generation hints."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.datahub.contracts import Capability
from app.datahub.router import RouteDefinition
from app.datahub.runtime import redis_call
from app.models.datahub import DataSourceRoute


class RouteStore:
    def __init__(self, db: Session, *, redis_client: Any | None = None) -> None:
        self.db = db
        self.redis = redis_client

    def get(self, capability: Capability | str) -> RouteDefinition | None:
        capability = Capability(capability)
        row = self.db.scalar(select(DataSourceRoute).where(DataSourceRoute.capability == capability.value))
        if row is None:
            return None
        return RouteDefinition(mode=row.mode, providers=list(row.provider_order_json), contract_version=row.contract_version)

    async def hint_generation(self, capability: Capability | str, version: int) -> None:
        if self.redis is None:
            return
        try:
            await redis_call(self.redis, "set", f"datahub:route-generation:{Capability(capability).value}", str(version))
        except Exception:
            return

    async def invalidate(self, capability: Capability | str) -> None:
        if self.redis is None:
            return
        try:
            await redis_call(self.redis, "delete", f"datahub:route-generation:{Capability(capability).value}")
        except Exception:
            return


__all__ = ["RouteStore"]
