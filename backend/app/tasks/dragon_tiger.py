"""ARQ adapter for Dragon-Tiger analysis."""

from app.core.database import SessionLocal
from app.models.dragon_tiger_report import DragonTigerReport
from app.services.task_execution import TaskExecutionContext, TaskExecutionRunner, validate_snapshot_args


async def dragon_tiger_task(ctx, task_id: int, period_days: int, user_id: int):
    async def execute(execution_ctx: TaskExecutionContext):
        args = validate_snapshot_args(
            execution_ctx,
            {"period_days": period_days, "user_id": user_id},
        )
        from app.services.dragon_tiger_orchestrator import run_dragon_tiger_analysis

        return await run_dragon_tiger_analysis(
            int(args.get("period_days", period_days)),
            args.get("user_id", execution_ctx.user_id),
            execution_ctx,
            None,
        )

    def persist_result(db, task, result):
        report = DragonTigerReport(
            user_id=task.user_id,
            period_days=int(result.get("period_days", period_days)),
            stats_json=result.get("stats", {}),
            top_stocks_json=result.get("top_stocks", []),
            institutions_json=result.get("institutions", []),
            analysis_text=result.get("analysis", {}).get("summary", ""),
            task_id=task.id,
        )
        db.add(report)
        db.flush()
        task.ref_id = report.id
        task.result_json = {"report_id": report.id}

    return await TaskExecutionRunner(SessionLocal).run(task_id, execute, persist_result)
