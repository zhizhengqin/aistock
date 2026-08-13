"""ARQ adapter for full stock analysis."""

from app.core.database import SessionLocal
from app.models.analysis_report import AnalysisReport
from app.services.task_execution import (
    TaskExecutionContext,
    TaskExecutionRunner,
    validate_snapshot_args,
)


async def analyze_stock_task(ctx, task_id: int, stock_code: str, user_id: int):
    """Run one stock analysis through the durable execution runner."""

    async def execute(execution_ctx: TaskExecutionContext):
        args = validate_snapshot_args(
            execution_ctx,
            {"stock_code": stock_code, "user_id": user_id},
        )
        from app.services.analysis_orchestrator import run_full_analysis

        return await run_full_analysis(
            str(args["stock_code"]),
            args.get("user_id", execution_ctx.user_id),
            execution_ctx,
            None,
        )

    def persist_result(db, task, result):
        report = AnalysisReport(
            user_id=task.user_id,
            stock_code=str(result.get("stock_code", stock_code)),
            stock_name=result.get("stock_name", stock_code),
            rating=result.get("decision", {}).get("rating", ""),
            confidence=result.get("decision", {}).get("confidence", 0),
            report_json=result,
        )
        db.add(report)
        db.flush()
        task.ref_id = report.id
        task.result_json = {"report_id": report.id, "stock_code": report.stock_code}

    return await TaskExecutionRunner(SessionLocal).run(task_id, execute, persist_result)
