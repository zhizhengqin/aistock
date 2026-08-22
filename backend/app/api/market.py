from typing import Literal

from fastapi import APIRouter, Depends, Path, Query
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.response import success
from app.datahub.consumer import get_market_indices
from app.datahub.errors import DataHubError
from app.services.market_hotspots import HotspotService

router = APIRouter()


@router.get("/stocks/market-indices")
async def market_indices():
    try:
        result = await get_market_indices()
    except DataHubError as exc:
        return JSONResponse(status_code=exc.status_code, content=exc.to_response())
    payload = success(data=result.data, message="行情数据已更新")
    if hasattr(result, "meta"):
        payload["meta"] = result.meta.model_dump(mode="json")
    return payload


@router.get("/stocks/market-hotspots")
async def market_hotspots(
    kind: Literal["industry", "theme"] = Query(default="industry"),
    limit: int = Query(default=12, ge=1, le=12),
    db: Session = Depends(get_db),
):
    try:
        result = await HotspotService(db).get_hotspots(kind, limit)
    except DataHubError as exc:
        return JSONResponse(status_code=exc.status_code, content=exc.to_response())
    payload = success(data=result.model_dump(mode="json"), message="市场热点数据已更新")
    payload["meta"] = result.meta.model_dump(mode="json")
    return payload


@router.get("/stocks/market-cloud")
async def market_cloud(
    kind: Literal["industry", "theme"] = Query(default="industry"),
    limit: int = Query(default=80, ge=1, le=80),
    db: Session = Depends(get_db),
):
    try:
        result = await HotspotService(db).get_market_cloud(kind, limit)
    except DataHubError as exc:
        return JSONResponse(status_code=exc.status_code, content=exc.to_response())
    payload = success(data=result.model_dump(mode="json"), message="大盘云图数据已更新")
    payload["meta"] = result.meta.model_dump(mode="json")
    return payload


@router.get("/stocks/boards/{board_code}/constituents")
async def board_constituents(
    board_code: str = Path(pattern=r"^BK\d{3,6}$"),
    kind: Literal["industry", "theme"] = Query(default="industry"),
    limit: int = Query(default=20, ge=1, le=20),
    db: Session = Depends(get_db),
):
    try:
        result = await HotspotService(db).get_constituents(kind, board_code, limit)
    except DataHubError as exc:
        return JSONResponse(status_code=exc.status_code, content=exc.to_response())
    payload = success(data=result.model_dump(mode="json"), message="代表个股数据已更新")
    payload["meta"] = result.meta.model_dump(mode="json")
    return payload
