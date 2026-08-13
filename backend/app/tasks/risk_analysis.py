"""ARQ adapter for single-stock risk analysis."""

from app.core.database import SessionLocal
from app.models.risk_warning import RiskWarning
from app.services.task_execution import TaskExecutionContext, TaskExecutionRunner, validate_snapshot_args


async def stock_risk_task(ctx, task_id: int, stock_code: str, days: int, user_id: int):
    async def execute(execution_ctx: TaskExecutionContext):
        args = validate_snapshot_args(
            execution_ctx,
            {"stock_code": stock_code, "days": days, "user_id": user_id},
        )
        from app.services.risk_orchestrator import run_stock_risk_analysis

        return await run_stock_risk_analysis(
            str(args["stock_code"]),
            int(args["days"]),
            args["user_id"],
            execution_ctx,
            None,
        )

    def persist_result(db, task, result):
        durable_args = (task.input_snapshot or {}).get("_args") or {}
        selected_stock = str(durable_args["stock_code"])
        selected_days = int(durable_args["days"])
        for warning_data in result.get("warnings", []):
            db.add(
                RiskWarning(
                    user_id=task.user_id,
                    stock_code=selected_stock,
                    stock_name=result.get("stock_name", ""),
                    level=warning_data.get("level", "info"),
                    category=warning_data.get("category", ""),
                    message=warning_data.get("message", ""),
                    value=warning_data.get("value", ""),
                    days=selected_days,
                )
            )
        task.result_json = {
            "stock_code": selected_stock,
            "warnings": len(result.get("warnings", [])),
            "ai": result.get("ai_analysis", {}),
        }

    return await TaskExecutionRunner(SessionLocal).run(task_id, execute, persist_result)
