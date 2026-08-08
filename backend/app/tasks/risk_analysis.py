from datetime import datetime, timezone
from app.core.database import SessionLocal
from app.core.logger import logger
from app.models.task_record import TaskRecord
from app.models.risk_warning import RiskWarning


async def stock_risk_task(ctx, task_id: int, stock_code: str, days: int, user_id: int):
    """Arq task: run stock risk analysis."""
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

        from app.services.risk_orchestrator import run_stock_risk_analysis
        result = await run_stock_risk_analysis(stock_code, days, user_id, task, db)

        # Save warnings to risk_warnings table
        for w in result.get("warnings", []):
            warning = RiskWarning(
                user_id=user_id,
                stock_code=stock_code,
                stock_name=result.get("stock_name", ""),
                level=w.get("level", "info"),
                category=w.get("category", ""),
                message=w.get("message", ""),
                value=w.get("value", ""),
                days=days,
            )
            db.add(warning)

        task.status = "success"
        task.progress = 100
        task.result_json = {"stock_code": stock_code, "warnings": len(result.get("warnings", [])),
                            "ai": result.get("ai_analysis", {})}
        task.finished_at = datetime.now(timezone.utc)
        db.commit()
        logger.info(f"Stock risk task {task_id} completed: {len(result.get('warnings', []))} warnings")

    except Exception as e:
        logger.error(f"Stock risk task {task_id} failed: {e}")
        task = db.query(TaskRecord).filter(TaskRecord.id == task_id).first()
        if task:
            task.status = "failed"
            task.error = str(e)
            task.finished_at = datetime.now(timezone.utc)
            db.commit()
    finally:
        db.close()
