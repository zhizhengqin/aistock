from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func
from datetime import datetime, timedelta, timezone
from app.core.database import get_db
from app.core.config import settings
from app.core.deps import get_current_user
from app.core.response import success
from app.models.user import User
from app.models.task_record import TaskRecord
from app.models.news_item import NewsItem
from app.models.us_research_report import UsResearchReport
from app.core.logger import logger
from app.services.task_submission import (
    TaskSubmission,
    TaskSubmissionService,
    schedule_inline_after_commit,
)
import asyncio

router = APIRouter()


async def _start_task(db, task_type, user_id, inline_func, args, *, requires_llm=True):
    args_dict = {"user_id": user_id}
    if task_type == "us_research":
        args_dict["trade_date"] = args[0]
    submission = TaskSubmission(
        task_type=task_type,
        user_id=user_id,
        feature=None,
        feature_cost=0,
        args=args_dict,
        input_snapshot={"args": args},
        prompt_version=f"{task_type}-v1",
        requires_llm=requires_llm,
    )
    result = TaskSubmissionService(db).submit(submission)
    if settings.TASK_INLINE:
        await schedule_inline_after_commit(db, result, inline_func, tuple(args))
        logger.info(f"Inline task {result.task.id} type={task_type}")
    return result.task


# ---------------------------------------------------------------------------
# News (F-10)
# ---------------------------------------------------------------------------

def _news_to_dict(n: NewsItem) -> dict:
    return {
        "id": n.id,
        "title": n.title,
        "url": n.url,
        "source": n.source,
        "summary": n.summary,
        "published_at": n.published_at.isoformat() if n.published_at else None,
        "sentiment": n.sentiment,
        "category": n.category,
        "industries": [i for i in n.industries.split(",") if i] if n.industries else [],
    }


@router.get("/news")
async def list_news(hours: int = 24, source: str = "", limit: int = 50, offset: int = 0,
                    user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    q = db.query(NewsItem)
    if hours and hours > 0:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        q = q.filter(NewsItem.published_at >= cutoff)
    if source:
        q = q.filter(NewsItem.source == source)
    total = q.count()
    rows = q.order_by(NewsItem.published_at.desc().nullslast(), NewsItem.id.desc()) \
            .offset(offset).limit(min(limit, 200)).all()
    return success(data={"total": total, "items": [_news_to_dict(n) for n in rows]})


@router.get("/news/sources")
async def list_news_sources(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.query(NewsItem.source, func.count(NewsItem.id)) \
             .group_by(NewsItem.source).order_by(func.count(NewsItem.id).desc()).all()
    return success(data=[{"name": name, "count": count} for name, count in rows])


@router.post("/news/collect")
async def trigger_news_collect(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from app.tasks.news_collect import news_collect_task
    task = await _start_task(db, "news_collect", user.id, news_collect_task, [], requires_llm=False)
    return success(data={"task_id": task.id}, message="新闻采集任务已启动")


# ---------------------------------------------------------------------------
# US overnight research (F-11)
# ---------------------------------------------------------------------------

@router.get("/us-research/latest")
async def latest_us_research(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    row = db.query(UsResearchReport).filter(UsResearchReport.status == "success") \
            .order_by(UsResearchReport.trade_date.desc()).first()
    if not row:
        return success(data=None, message="暂无研报，请点击重新生成")
    content = dict(row.content or {})
    content["id"] = row.id
    content["created_at"] = row.created_at.isoformat() if row.created_at else None
    return success(data=content)


@router.get("/us-research/history")
async def us_research_history(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.query(UsResearchReport).order_by(UsResearchReport.trade_date.desc()).limit(30).all()
    return success(data=[{
        "id": r.id, "trade_date": r.trade_date, "status": r.status,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    } for r in rows])


@router.post("/us-research/generate")
async def generate_us_research(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from app.tasks.us_research import us_research_task
    from app.services.us_research_orchestrator import latest_us_trade_date
    trade_date = latest_us_trade_date()
    task = await _start_task(db, "us_research", user.id, us_research_task, [trade_date, user.id])
    return success(data={"task_id": task.id, "trade_date": trade_date}, message="研报生成任务已启动")


@router.get("/us-research/data-status")
async def us_research_data_status(user: User = Depends(get_current_user)):
    """Probe each data source with a short timeout and report ok/fail."""
    from app.services import us_research_orchestrator as uro
    probes = {
        "indices": uro.fetch_us_indices,
        "core_stocks": uro.fetch_us_core_stocks,
        "bond_yields": uro.fetch_us_bond_yields,
        "sectors": uro.fetch_us_sector_samples,
        "english_news": uro.fetch_english_news,
    }
    status = {}
    for name, fn in probes.items():
        try:
            fn()
            status[name] = "ok"
        except Exception as e:
            status[name] = f"failed: {str(e)[:120]}"
    return success(data=status)
