from fastapi import APIRouter
from app.core.response import success
from app.datasource.akshare_client import get_market_indices, get_sector_kline

router = APIRouter()


@router.get("/stocks/market-indices")
async def market_indices():
    return success(data=get_market_indices())


@router.get("/stocks/sectors/overview")
async def sectors_overview(category: str = "银行金融", period: str = "1月"):
    return success(data=get_sector_kline(category, period))
