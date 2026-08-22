"""Read-only DataHub health and capability status endpoint."""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.response import success
from app.datahub.contracts import Capability
from app.datahub.registry import PROVIDER_REGISTRY
from app.models.datahub import DataSourceConfig, DataSourceRoute


router = APIRouter()


@router.get("/health/datahub")
async def datahub_health(db: Session = Depends(get_db)):
    configs = {row.provider: row for row in db.scalars(select(DataSourceConfig)).all()}
    routes = {row.capability: row for row in db.scalars(select(DataSourceRoute)).all()}
    return success(data={
        "status": "ok",
        "providers": [
            {"provider": name, "display_name": definition.display_name, "enabled": configs.get(name).enabled if configs.get(name) else definition.enabled_by_default, "configured": name in configs}
            for name, definition in PROVIDER_REGISTRY.items()
        ],
        "capabilities": [
            {"capability": capability.value, "route_configured": capability.value in routes, "mode": routes[capability.value].mode if capability.value in routes else "auto"}
            for capability in Capability
        ],
    })


__all__ = ["router"]
