"""ARQ adapter for portfolio diagnosis."""

from app.core.database import SessionLocal
from app.models.portfolio_report import PortfolioReport
from app.models.portfolio_stock import PortfolioStock
from app.services.task_execution import TaskExecutionContext, TaskExecutionRunner, validate_snapshot_args


async def portfolio_diagnosis_task(ctx, task_id: int, user_id: int):
    async def execute(execution_ctx: TaskExecutionContext):
        args = validate_snapshot_args(execution_ctx, {"user_id": user_id})
        selected_user_id = args["user_id"]
        db = SessionLocal()
        try:
            stocks = db.query(PortfolioStock).filter(PortfolioStock.user_id == selected_user_id).all()
            holdings = [
                {
                    "stock_code": stock.stock_code,
                    "stock_name": stock.stock_name,
                    "shares": stock.shares,
                    "cost_price": stock.cost_price,
                    "industry": stock.industry,
                }
                for stock in stocks
            ]
        finally:
            db.close()

        from app.services.portfolio_orchestrator import run_portfolio_diagnosis

        return await run_portfolio_diagnosis(holdings, selected_user_id, execution_ctx, None)

    def persist_result(db, task, result):
        report = PortfolioReport(
            user_id=task.user_id,
            health_score=result.get("health_score", 0),
            diagnosis_json=result,
            task_id=task.id,
        )
        db.add(report)
        db.flush()
        task.ref_id = report.id
        task.result_json = {"report_id": report.id}

    return await TaskExecutionRunner(SessionLocal).run(task_id, execute, persist_result)
