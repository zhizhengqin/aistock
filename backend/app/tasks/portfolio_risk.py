from datetime import datetime, timezone
from app.core.database import SessionLocal
from app.core.logger import logger
from app.models.task_record import TaskRecord
from app.models.portfolio_stock import PortfolioStock
from app.models.risk_warning import RiskWarning


async def portfolio_risk_task(ctx, task_id: int, user_id: int):
    """Arq task: scan all portfolio stocks for risk warnings."""
    db = SessionLocal()
    try:
        task = db.query(TaskRecord).filter(TaskRecord.id == task_id).first()
        if not task:
            logger.error(f"Task {task_id} not found")
            return
        task.status = "running"
        task.started_at = datetime.now(timezone.utc)
        task.progress = 5
        db.commit()

        from app.services.risk_orchestrator import run_portfolio_risk_scan
        stocks = db.query(PortfolioStock).filter(PortfolioStock.user_id == user_id).all()
        holdings = [{"stock_code": s.stock_code, "stock_name": s.stock_name} for s in stocks]

        result = await run_portfolio_risk_scan(holdings, user_id, task, db)

        # Save warnings
        for h in result.get("holdings", []):
            for w in h.get("warnings", []):
                warning = RiskWarning(
                    user_id=user_id,
                    stock_code=h["stock_code"],
                    stock_name=h.get("stock_name", ""),
                    level=w.get("level", "info"),
                    category=w.get("category", ""),
                    message=w.get("message", ""),
                    value=w.get("value", ""),
                    days=60,
                )
                db.add(warning)

        task.status = "success"
        task.progress = 100
        task.result_json = result.get("portfolio", {})
        task.finished_at = datetime.now(timezone.utc)
        db.commit()
        logger.info(f"Portfolio risk task {task_id} completed")

    except Exception as e:
        logger.error(f"Portfolio risk task {task_id} failed: {e}")
        task = db.query(TaskRecord).filter(TaskRecord.id == task_id).first()
        if task:
            task.status = "failed"
            task.error = str(e)
            task.finished_at = datetime.now(timezone.utc)
            db.commit()
    finally:
        db.close()
