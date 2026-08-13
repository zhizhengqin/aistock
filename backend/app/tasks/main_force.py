"""ARQ adapter for the main-force selection task."""

from datetime import datetime, timezone

from app.core.database import SessionLocal
from app.models.main_force_run import MainForceRun
from app.services.task_execution import TaskExecutionContext, TaskExecutionRunner, validate_snapshot_args


async def main_force_task(ctx, task_id: int, user_id: int):
    async def execute(execution_ctx: TaskExecutionContext):
        args = validate_snapshot_args(execution_ctx, {"user_id": user_id})
        from app.services.main_force_orchestrator import run_main_force_selection

        return await run_main_force_selection(
            args.get("user_id", execution_ctx.user_id),
            execution_ctx,
            None,
        )

    def persist_result(db, task, result):
        run = MainForceRun(
            user_id=task.user_id,
            run_date=result.get("run_date", datetime.now(timezone.utc).strftime("%Y-%m-%d")),
            candidates_count=result.get("skim_count", 0),
            filtered_count=result.get("filtered_count", 0),
            recommended_json=result.get("recommended", {}),
            excluded_json=result.get("excluded", []),
            token_total=0,
            task_id=task.id,
            analysis_json=result,
        )
        db.add(run)
        db.flush()
        task.ref_id = run.id
        task.result_json = {"run_id": run.id}

    return await TaskExecutionRunner(SessionLocal).run(task_id, execute, persist_result)
