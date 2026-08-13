"""ARQ adapter for portfolio-wide risk scanning."""

from app.core.database import SessionLocal
from app.models.portfolio_stock import PortfolioStock
from app.models.risk_warning import RiskWarning
from app.services.task_execution import TaskExecutionContext, TaskExecutionRunner, validate_snapshot_args


async def portfolio_risk_task(ctx, task_id: int, user_id: int):
    async def execute(execution_ctx: TaskExecutionContext):
        args = validate_snapshot_args(execution_ctx, {"user_id": user_id})
        selected_user_id = args.get("user_id", execution_ctx.user_id)
        db = SessionLocal()
        try:
            stocks = db.query(PortfolioStock).filter(PortfolioStock.user_id == selected_user_id).all()
            holdings = [
                {"stock_code": stock.stock_code, "stock_name": stock.stock_name}
                for stock in stocks
            ]
        finally:
            db.close()

        from app.services.risk_orchestrator import run_portfolio_risk_scan

        return await run_portfolio_risk_scan(holdings, selected_user_id, execution_ctx, None)

    def persist_result(db, task, result):
        for holding in result.get("holdings", []):
            for warning_data in holding.get("warnings", []):
                db.add(
                    RiskWarning(
                        user_id=task.user_id,
                        stock_code=holding["stock_code"],
                        stock_name=holding.get("stock_name", ""),
                        level=warning_data.get("level", "info"),
                        category=warning_data.get("category", ""),
                        message=warning_data.get("message", ""),
                        value=warning_data.get("value", ""),
                        days=60,
                    )
                )
        task.result_json = result.get("portfolio", {})

    return await TaskExecutionRunner(SessionLocal).run(task_id, execute, persist_result)
