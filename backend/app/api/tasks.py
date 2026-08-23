from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from sqlalchemy import desc
from app.core.database import get_db
from app.core.config import settings
from app.core.deps import get_current_user
from app.core.response import success
from app.models.user import User
from app.models.task_record import TaskRecord
from app.models.llm_execution import LlmCallAttempt
from app.core.logger import logger
from app.services.task_submission import (
    TaskSubmission,
    TaskSubmissionResult,
    TaskSubmissionService,
    schedule_inline_after_commit,
)
from pydantic import BaseModel

router = APIRouter()


_ANALYSIS_STEPS = (
    ("technical", "技术面分析师", "stock.technical.v1"),
    ("fundamental", "基本面分析师", "stock.fundamental.v1"),
    ("capital", "资金面分析师", "stock.capital.v1"),
    ("news", "消息面分析师", "stock.news.v1"),
    ("sentiment", "情绪面分析师", "stock.sentiment.v1"),
    ("risk", "风险分析师", "stock.risk.v1"),
    ("chief", "首席分析师", "stock.chief.v1"),
)

_SAFE_TASK_ERROR_MESSAGES = {
    "not_configured": "数据源尚未配置",
    "authentication_failed": "数据源鉴权失败",
    "permission_denied": "数据源访问权限不足",
    "rate_limited": "数据源请求过于频繁，请稍后重试",
    "ip_blocked": "数据源访问受到限制",
    "timeout": "数据源请求超时，请稍后重试",
    "empty_invalid": "数据源返回空数据",
    "schema_changed": "数据源字段发生变化",
    "stale_invalid": "数据源数据已过期",
    "unsupported": "当前数据源不支持该能力",
    "internal": "数据源暂时不可用",
    "conflict": "数据源配置发生冲突，请刷新后重试",
    "validation": "数据源参数校验失败",
    "llm_config_missing": "任务绑定的大模型配置不存在",
    "llm_credential_error": "模型密钥不可用，请管理员检查加密密钥配置",
    "llm_failed_unknown": "模型调用结果未知，任务未自动重试",
    "task_input_mismatch": "任务参数与持久化快照不一致",
    "llm_schema_invalid": "大模型返回内容不符合任务结构",
}


def _latest_attempts(db: Session, task_id: int) -> dict[str, LlmCallAttempt]:
    attempts = (
        db.query(LlmCallAttempt)
        .filter(LlmCallAttempt.task_id == task_id)
        .order_by(desc(LlmCallAttempt.attempt_no), desc(LlmCallAttempt.created_at))
        .all()
    )
    latest: dict[str, LlmCallAttempt] = {}
    for attempt in attempts:
        latest.setdefault(attempt.step_key, attempt)
    return latest


def _step_view(attempt: LlmCallAttempt | None, key: str, label: str, step_key: str) -> dict:
    if attempt is None:
        return {"key": key, "label": label, "status": "waiting", "result": None, "error": None}
    if attempt.status == "success" and attempt.result_schema_version:
        return {"key": key, "label": label, "status": "completed", "result": attempt.result_json, "error": None}
    if attempt.status in {"started", "success"}:
        return {"key": key, "label": label, "status": "analyzing", "result": None, "error": None}
    if attempt.status == "failed":
        return {"key": key, "label": label, "status": "failed", "result": None, "error": "本步骤分析失败，请稍后重试"}
    if attempt.status == "failed_unknown":
        return {"key": key, "label": label, "status": "unknown", "result": None, "error": "模型调用结果未知，未自动重试"}
    return {"key": key, "label": label, "status": "waiting", "result": None, "error": None}


def _safe_task_error(task: TaskRecord) -> str | None:
    raw = task.error
    if not raw:
        return None
    code = str(raw).split(":", 1)[0].strip()
    safe_message = _SAFE_TASK_ERROR_MESSAGES.get(code)
    if safe_message:
        return f"{code}: {safe_message}"
    return "task_execution_failed: 任务执行失败，请稍后重试"


def _task_phase(task: TaskRecord, steps: list[dict]) -> str:
    if task.status == "success":
        return "completed"
    if task.status in {"failed", "failed_unknown"}:
        return "failed"
    statuses = {step["key"]: step["status"] for step in steps}
    if not any(status != "waiting" for status in statuses.values()):
        return "preparing"
    analyst_keys = {key for key, _label, _step_key in _ANALYSIS_STEPS if key != "chief"}
    if analyst_keys and all(statuses.get(key) == "completed" for key in analyst_keys):
        return "meeting"
    return "analyzing"


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
            prompt_version="stock-analysis-v2",
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
    attempts = _latest_attempts(db, task.id)
    steps = [_step_view(attempts.get(step_key), key, label, step_key) for key, label, step_key in _ANALYSIS_STEPS]
    return success(data={
        "id": task.id,
        "task_type": task.task_type,
        "status": task.status,
        "progress": task.progress,
        "error": _safe_task_error(task),
        "result": task.result_json,
        "phase": _task_phase(task, steps),
        "steps": steps,
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
    from app.datahub.consumer import get_stock_info, get_stock_kline, kline_dataframe
    from app.datahub.errors import DataHubError
    from app.datasource.indicators import compute_all
    try:
        info = (await get_stock_info(code)).data.model_dump(mode="json")
        kline_df = kline_dataframe(await get_stock_kline(code, 120))
    except DataHubError as exc:
        return JSONResponse(status_code=exc.status_code, content=exc.to_response())
    financial = None
    warnings: list[str] = []
    try:
        financial = (await get_stock_financial_summary(code)).data.model_dump(mode="json")
    except DataHubError:
        # Financials enrich the snapshot but are not required to render the
        # quote/K-line view.  Keep the degradation explicit instead of
        # manufacturing placeholder values.
        warnings.append("财务数据暂不可用，已跳过财务指标")
    indicators = compute_all(kline_df) if not kline_df.empty else {"ma": {}, "macd": {}, "rsi": {}, "kdj": {}, "boll": {}}
    kline_data = []
    if not kline_df.empty:
        kline_data = kline_df.tail(60).to_dict("records")
    return success(data={
        "stock_code": code,
        "info": info,
        "indicators": indicators,
        "kline": kline_data,
        "financial": financial,
        "warnings": warnings,
    })
