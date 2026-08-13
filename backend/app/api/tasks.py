from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.core.database import get_db
from app.core.config import settings
from app.core.deps import get_current_user
from app.core.response import success
from app.models.user import User
from app.models.task_record import TaskRecord
from app.core.logger import logger
from app.services.task_submission import (
    TaskSubmission,
    TaskSubmissionResult,
    TaskSubmissionService,
    schedule_inline_after_commit,
)
from pydantic import BaseModel

router = APIRouter()


class AnalyzeRequest(BaseModel):
    stock_codes: list[str]


@router.post("/stocks/analyze")
async def submit_analysis(req: AnalyzeRequest, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not req.stock_codes or len(req.stock_codes) > 50:
        raise HTTPException(status_code=400, detail="股票代码数量需在1-50之间")

    submissions = [
        TaskSubmission(
            task_type="stock_analysis",
            user_id=user.id,
            feature="stock_analysis",
            feature_cost=1,
            args={"stock_code": code.strip(), "user_id": user.id},
            input_snapshot={"stock_code": code.strip()},
            prompt_version="stock-analysis-v1",
        )
        for code in req.stock_codes
    ]
    result = TaskSubmissionService(db).submit_batch(submissions)
    if settings.TASK_INLINE:
        from app.tasks.analysis import analyze_stock_task
        for task, outbox, submission in zip(result.tasks, result.outboxes, submissions):
            await schedule_inline_after_commit(
                db,
                TaskSubmissionResult([task], [outbox]),
                analyze_stock_task,
                (submission.args["stock_code"], user.id),
            )
            logger.info(f"Inline task {task.id} for stock {submission.args['stock_code']}")
    tasks = [
        {"task_id": task.id, "stock_code": code.strip()}
        for task, code in zip(result.tasks, req.stock_codes)
    ]

    return success(data={"tasks": tasks}, message="分析任务已提交")


@router.get("/tasks/{task_id}")
async def get_task_status(task_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    task = db.query(TaskRecord).filter(TaskRecord.id == task_id, TaskRecord.user_id == user.id).first()
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return success(data={
        "id": task.id,
        "task_type": task.task_type,
        "status": task.status,
        "progress": task.progress,
        "error": task.error,
        "result": task.result_json,
        "started_at": task.started_at.isoformat() if task.started_at else None,
        "finished_at": task.finished_at.isoformat() if task.finished_at else None,
    })


@router.get("/stocks/user/results")
async def list_analysis_history(
    page: int = 1, page_size: int = 20,
    user: User = Depends(get_current_user), db: Session = Depends(get_db),
):
    from app.models.analysis_report import AnalysisReport
    from sqlalchemy import func
    total = db.query(func.count(AnalysisReport.id)).filter(AnalysisReport.user_id == user.id).scalar() or 0
    reports = (
        db.query(AnalysisReport)
        .filter(AnalysisReport.user_id == user.id)
        .order_by(AnalysisReport.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return success(data={
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [
            {
                "id": r.id,
                "stock_code": r.stock_code,
                "stock_name": r.stock_name,
                "rating": r.rating,
                "confidence": r.confidence,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
            for r in reports
        ],
    })


@router.get("/stocks/user/results/{report_id}")
async def get_analysis_detail(report_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from app.models.analysis_report import AnalysisReport
    report = db.query(AnalysisReport).filter(
        AnalysisReport.id == report_id, AnalysisReport.user_id == user.id
    ).first()
    if not report:
        raise HTTPException(status_code=404, detail="报告不存在")
    return success(data={
        "id": report.id,
        "stock_code": report.stock_code,
        "stock_name": report.stock_name,
        "rating": report.rating,
        "confidence": report.confidence,
        "report": report.report_json,
        "created_at": report.created_at.isoformat() if report.created_at else None,
    })


@router.get("/stocks/user/results/{report_id}/pdf")
async def download_analysis_pdf(report_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """F-03-06: download an analysis report as PDF."""
    from fastapi.responses import Response
    from app.models.analysis_report import AnalysisReport
    from app.services.report_pdf import build_analysis_pdf

    report = db.query(AnalysisReport).filter(
        AnalysisReport.id == report_id, AnalysisReport.user_id == user.id
    ).first()
    if not report:
        raise HTTPException(status_code=404, detail="报告不存在")

    pdf = build_analysis_pdf(report.report_json or {})
    filename = f"report_{report.stock_code}_{report.id}.pdf"
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/stocks/{code}/snapshot")
async def stock_snapshot(code: str, user: User = Depends(get_current_user)):
    """Return real-time snapshot + indicators for a stock (no AI call)."""
    from app.datasource.akshare_client import get_stock_info, get_stock_kline
    from app.datasource.indicators import compute_all
    info = get_stock_info(code)
    kline_df = get_stock_kline(code, 120)
    indicators = compute_all(kline_df) if not kline_df.empty else {"ma": {}, "macd": {}, "rsi": {}, "kdj": {}, "boll": {}}
    kline_data = []
    if not kline_df.empty:
        kline_data = kline_df.tail(60).to_dict("records")
    return success(data={
        "stock_code": code,
        "info": info,
        "indicators": indicators,
        "kline": kline_data,
    })
