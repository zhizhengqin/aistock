from datetime import datetime, timezone
from app.core.database import SessionLocal
from app.core.logger import logger
from app.models.task_record import TaskRecord
from app.models.portfolio_report import PortfolioReport
from app.models.portfolio_stock import PortfolioStock


async def portfolio_diagnosis_task(ctx, task_id: int, user_id: int):
    """Arq task: run AI portfolio diagnosis."""
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

        from app.services.portfolio_orchestrator import run_portfolio_diagnosis
        stocks = db.query(PortfolioStock).filter(PortfolioStock.user_id == user_id).all()
        holdings = [{"stock_code": s.stock_code, "stock_name": s.stock_name,
                     "shares": s.shares, "cost_price": s.cost_price,
                     "industry": s.industry} for s in stocks]

        result = await run_portfolio_diagnosis(holdings, user_id, task, db)

        report = PortfolioReport(
            user_id=user_id,
            health_score=result.get("health_score", 0),
            diagnosis_json=result,
            task_id=task_id,
        )
        db.add(report)
        db.commit()
        db.refresh(report)

        task.status = "success"
        task.progress = 100
        task.ref_id = report.id
        task.result_json = {"report_id": report.id}
        task.finished_at = datetime.now(timezone.utc)
        db.commit()
        logger.info(f"Portfolio diagnosis task {task_id} completed: report_id={report.id}")

    except Exception as e:
        logger.error(f"Portfolio diagnosis task {task_id} failed: {e}")
        task = db.query(TaskRecord).filter(TaskRecord.id == task_id).first()
        if task:
            task.status = "failed"
            task.error = str(e)
            task.finished_at = datetime.now(timezone.utc)
            db.commit()
    finally:
        db.close()
