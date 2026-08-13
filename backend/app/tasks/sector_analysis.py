"""ARQ adapter for sector analysis."""

from datetime import datetime, timezone

from app.core.database import SessionLocal
from app.models.sector_report import SectorReport
from app.services.task_execution import TaskExecutionContext, TaskExecutionRunner, validate_snapshot_args


async def sector_analysis_task(ctx, task_id: int, user_id: int = 0):
    async def execute(execution_ctx: TaskExecutionContext):
        args = validate_snapshot_args(execution_ctx, {"user_id": user_id})
        from app.services.sector_orchestrator import run_sector_analysis

        return await run_sector_analysis(
            args.get("user_id", execution_ctx.user_id),
            execution_ctx,
            None,
        )

    def persist_result(db, task, result):
        decision = result.get("decision", {})
        report = SectorReport(
            report_date=result.get("report_date", datetime.now(timezone.utc).strftime("%Y-%m-%d")),
            bull_json=decision.get("bull_sectors", []),
            bear_json=decision.get("bear_sectors", []),
            neutral_json=decision.get("neutral_sectors", []),
            rotation_json=decision.get("operation_advice", ""),
            summary_json=result.get("agents", {}),
            agents_json=result.get("agents", {}),
            task_id=task.id,
        )
        db.add(report)
        db.flush()
        task.ref_id = report.id
        task.result_json = {"report_id": report.id}

    return await TaskExecutionRunner(SessionLocal).run(task_id, execute, persist_result)
