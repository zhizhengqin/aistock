"""ARQ adapter for the US overnight research report."""

from app.core.database import SessionLocal
from app.models.us_research_report import UsResearchReport
from app.services.task_execution import TaskExecutionContext, TaskExecutionRunner, validate_snapshot_args


async def us_research_task(ctx, task_id: int, trade_date: str, user_id: int = 0):
    async def execute(execution_ctx: TaskExecutionContext):
        args = validate_snapshot_args(
            execution_ctx,
            {"trade_date": trade_date, "user_id": user_id},
        )
        from app.services.us_research_orchestrator import build_report

        return await build_report(
            str(args["trade_date"]),
            user_id=args["user_id"],
            execution_ctx=execution_ctx,
        )

    def persist_result(db, task, result):
        durable_args = (task.input_snapshot or {}).get("_args") or {}
        selected_trade_date = str(durable_args["trade_date"])
        report = db.query(UsResearchReport).filter(
            UsResearchReport.trade_date == selected_trade_date
        ).first()
        if report is None:
            report = UsResearchReport(trade_date=selected_trade_date)
            db.add(report)
        report.status = "success"
        report.content = result
        report.data_status = result.get("data_status", {})
        report.error = ""
        db.flush()
        task.ref_id = report.id
        task.result_json = {"report_id": report.id, "trade_date": selected_trade_date}

    return await TaskExecutionRunner(SessionLocal).run(task_id, execute, persist_result)
