from fastapi import APIRouter
from fastapi.responses import JSONResponse
from app.core.response import success
from app.datahub.consumer import get_market_indices, get_sector_kline
from app.datahub.errors import DataHubError

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


@router.get("/stocks/sectors/overview")
async def sectors_overview(category: str = "银行金融", period: str = "1月"):
    try:
        result = await get_sector_kline(category, period)
    except DataHubError as exc:
        return JSONResponse(status_code=exc.status_code, content=exc.to_response())
    rows = result.data if isinstance(result.data, list) else [result.data]
    keywords = {
        "银行金融": ("银行", "证券", "保险", "金融"),
        "科技互联网": ("科技", "软件", "互联网", "通信", "电子"),
        "新能源": ("新能源", "电池", "光伏", "电力设备"),
        "大消费": ("食品", "饮料", "家电", "消费", "零售"),
        "高端制造": ("机械", "军工", "制造", "汽车", "工业"),
        "周期资源": ("煤炭", "钢铁", "有色", "化工", "资源"),
    }.get(category, ())
    selected = [row for row in rows if keywords and any(word in getattr(row, "name", "") for word in keywords)] or rows[:30]
    sectors = [row.model_dump(mode="json") if hasattr(row, "model_dump") else dict(row) for row in selected]
    stocks = [stock for row in selected for stock in (getattr(row, "representative_stocks", None) or [])]
    data = {
        "category": category,
        "period": period,
        "period_label": "实时/当日（当前来源未提供历史区间）",
        "sectors": sectors,
        "stocks": stocks[:30],
        "updated_at": (result.data_at or result.fetched_at).isoformat(),
    }
    payload = success(data=data, message="板块数据已更新；当前为实时/当日口径")
    if hasattr(result, "meta"):
        payload["meta"] = result.meta.model_dump(mode="json")
    return payload
