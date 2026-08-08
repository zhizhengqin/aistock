from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import func
from app.core.database import get_db
from app.core.config import settings
from app.core.deps import get_current_user
from app.core.response import success
from app.models.user import User
from app.models.task_record import TaskRecord
from app.models.main_force_run import MainForceRun
from app.models.sector_report import SectorReport
from app.models.dragon_tiger_report import DragonTigerReport
from app.core.logger import logger
from app.services import membership as membership_svc
from pydantic import BaseModel

router = APIRouter()


def _start_task(db: Session, task_type: str, user_id: int, inline_func, args: list) -> TaskRecord:
    """Create a task record and dispatch it (inline or arq)."""
    task = TaskRecord(task_type=task_type, user_id=user_id, status="pending", progress=0)
    db.add(task)
    db.commit()
    db.refresh(task)
    if settings.TASK_INLINE:
        import asyncio
        asyncio.create_task(inline_func(None, task.id, *args))
        logger.info(f"Inline task {task.id} type={task_type}")
    else:
        try:
            from app.tasks.queue import get_redis_settings
            from arq import create_pool
            redis = create_poll = asyncio.get_event_loop().run_until_complete(create_pool(get_redis_settings()))
            job = asyncio.get_event_loop().run_until_complete(redis.enqueue_job(task_type, *args))
            logger.info(f"Enqueued task {task.id}, job_id={job.job_id if job else 'none'}")
        except Exception as e:
            logger.warning(f"arq enqueue failed, running inline: {e}")
            import asyncio
            asyncio.create_task(inline_func(None, task.id, *args))
    return task


# ---------------------------------------------------------------------------
# Main-Force Stock Selection (F-04)
# ---------------------------------------------------------------------------

@router.post("/stocks/main-force/run")
async def run_main_force(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    membership_svc.check_and_consume(db, user, "stock_pick")

    task = _start_task(db, "main_force", user.id,
                       __import__("app.tasks.main_force", fromlist=["main_force_task"]).main_force_task,
                       [user.id])
    return success(data={"task_id": task.id}, message="主力选股任务已提交")


@router.get("/stocks/main-force/history")
async def main_force_history(page: int = 1, page_size: int = 20,
                              user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    total = db.query(func.count(MainForceRun.id)).filter(MainForceRun.user_id == user.id).scalar() or 0
    runs = db.query(MainForceRun).filter(MainForceRun.user_id == user.id) \
        .order_by(MainForceRun.created_at.desc()) \
        .offset((page - 1) * page_size).limit(page_size).all()
    return success(data={"total": total, "page": page, "page_size": page_size, "items": [
        {"id": r.id, "run_date": r.run_date, "candidates_count": r.candidates_count,
         "filtered_count": r.filtered_count, "task_id": r.task_id}
        for r in runs
    ]})


@router.get("/stocks/main-force/{run_id}")
async def main_force_detail(run_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    run = db.query(MainForceRun).filter(MainForceRun.id == run_id, MainForceRun.user_id == user.id).first()
    if not run:
        raise HTTPException(status_code=404, detail="选股记录不存在")
    return success(data={
        "id": run.id, "run_date": run.run_date,
        "candidates_count": run.candidates_count, "filtered_count": run.filtered_count,
        "recommended": run.recommended_json, "excluded": run.excluded_json,
        "analysis": run.analysis_json, "token_total": run.token_total,
    })


# ---------------------------------------------------------------------------
# Sector Analysis (F-05)
# ---------------------------------------------------------------------------

@router.post("/stocks/sectors/analyze")
async def run_sector_analysis(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    membership_svc.check_and_consume(db, user, "sector")
    task = _start_task(db, "sector_analysis", user.id,
                       __import__("app.tasks.sector_analysis", fromlist=["sector_analysis_task"]).sector_analysis_task,
                       [user.id])
    return success(data={"task_id": task.id}, message="板块分析任务已提交")


@router.get("/stocks/sectors/reports/latest")
async def sector_latest(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    report = db.query(SectorReport).order_by(SectorReport.created_at.desc()).first()
    if not report:
        return success(data=None, message="暂无板块分析报告")
    return success(data={
        "id": report.id, "report_date": report.report_date,
        "bull_sectors": report.bull_json, "bear_sectors": report.bear_json,
        "neutral_sectors": report.neutral_json, "operation_advice": report.rotation_json,
        "agents": report.agents_json, "summary": report.summary_json,
    })


@router.get("/stocks/sectors/reports/history")
async def sector_history(page: int = 1, page_size: int = 20,
                         user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    total = db.query(func.count(SectorReport.id)).scalar() or 0
    reports = db.query(SectorReport).order_by(SectorReport.created_at.desc()) \
        .offset((page - 1) * page_size).limit(page_size).all()
    return success(data={"total": total, "page": page, "page_size": page_size, "items": [
        {"id": r.id, "report_date": r.report_date}
        for r in reports
    ]})


# ---------------------------------------------------------------------------
# Dragon-Tiger Board (F-06)
# ---------------------------------------------------------------------------

class DragonTigerRequest(BaseModel):
    period_days: int = 5


@router.post("/stocks/dragon-tiger/analyze")
async def run_dragon_tiger(req: DragonTigerRequest,
                           user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if req.period_days not in [3, 5, 10, 15, 20, 30]:
        raise HTTPException(status_code=400, detail="时间范围仅支持 3/5/10/15/20/30 天")
    membership_svc.check_and_consume(db, user, "dragon_tiger")

    task = _start_task(db, "dragon_tiger", user.id,
                       __import__("app.tasks.dragon_tiger", fromlist=["dragon_tiger_task"]).dragon_tiger_task,
                       [req.period_days, user.id])
    return success(data={"task_id": task.id}, message="龙虎榜分析任务已提交")


@router.get("/stocks/dragon-tiger/reports")
async def dragon_tiger_reports(page: int = 1, page_size: int = 20,
                               user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    total = db.query(func.count(DragonTigerReport.id)).filter(DragonTigerReport.user_id == user.id).scalar() or 0
    reports = db.query(DragonTigerReport).filter(DragonTigerReport.user_id == user.id) \
        .order_by(DragonTigerReport.created_at.desc()) \
        .offset((page - 1) * page_size).limit(page_size).all()
    return success(data={"total": total, "page": page, "page_size": page_size, "items": [
        {"id": r.id, "period_days": r.period_days,
         "created_at": r.created_at.isoformat() if r.created_at else None}
        for r in reports
    ]})


@router.get("/stocks/dragon-tiger/stats")
async def dragon_tiger_stats(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    total_reports = db.query(func.count(DragonTigerReport.id)).filter(DragonTigerReport.user_id == user.id).scalar() or 0
    latest = db.query(DragonTigerReport).filter(DragonTigerReport.user_id == user.id) \
        .order_by(DragonTigerReport.created_at.desc()).first()
    return success(data={
        "total_reports": total_reports,
        "latest_period": latest.period_days if latest else None,
        "latest_created": latest.created_at.isoformat() if latest else None,
    })


@router.get("/stocks/dragon-tiger/reports/{report_id}")
async def dragon_tiger_detail(report_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    report = db.query(DragonTigerReport).filter(
        DragonTigerReport.id == report_id, DragonTigerReport.user_id == user.id
    ).first()
    if not report:
        raise HTTPException(status_code=404, detail="龙虎榜报告不存在")
    return success(data={
        "id": report.id, "period_days": report.period_days,
        "stats": report.stats_json, "top_stocks": report.top_stocks_json,
        "institutions": report.institutions_json, "analysis": report.analysis_text,
        "created_at": report.created_at.isoformat() if report.created_at else None,
    })
