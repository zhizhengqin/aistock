from datetime import datetime, timezone
from app.core.database import SessionLocal
from app.core.logger import logger
from app.models.task_record import TaskRecord
from app.models.analysis_report import AnalysisReport


async def analyze_stock_task(ctx, task_id: int, stock_code: str, user_id: int):
    """Arq task: orchestrate full stock analysis."""
    db = SessionLocal()
    try:
        task = db.query(TaskRecord).filter(TaskRecord.id == task_id).first()
        if not task:
            logger.error(f"Task {task_id} not found")
            return
        task.status = "running"
        task.started_at = datetime.now(timezone.utc)
        task.progress = 10
        db.commit()

        # Import here to avoid circular imports
        from app.services.analysis_orchestrator import run_full_analysis
        result = await run_full_analysis(stock_code, user_id, task, db)

        # Save report
        report = AnalysisReport(
            user_id=user_id,
            stock_code=stock_code,
            stock_name=result.get("stock_name", stock_code),
            rating=result.get("decision", {}).get("rating", ""),
            confidence=result.get("decision", {}).get("confidence", 0),
            report_json=result,
        )
        db.add(report)
        db.commit()
        db.refresh(report)

        task.status = "success"
        task.progress = 100
        task.ref_id = report.id
        task.result_json = {"report_id": report.id, "stock_code": stock_code}
        task.finished_at = datetime.now(timezone.utc)
        db.commit()
        logger.info(f"Task {task_id} completed: report_id={report.id}")

    except Exception as e:
        logger.error(f"Task {task_id} failed: {e}")
        task = db.query(TaskRecord).filter(TaskRecord.id == task_id).first()
        if task:
            task.status = "failed"
            task.error = str(e)
            task.finished_at = datetime.now(timezone.utc)
            db.commit()
    finally:
        db.close()
