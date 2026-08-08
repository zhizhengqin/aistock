from datetime import datetime, timezone
from app.core.database import SessionLocal
from app.core.logger import logger
from app.models.task_record import TaskRecord


async def us_research_task(ctx, task_id: int, trade_date: str, user_id: int = 0):
    """Arq task: build and save the US overnight research report."""
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

        from app.services.us_research_orchestrator import build_report, save_report
        report = await build_report(trade_date, user_id=user_id)
        task.progress = 80
        db.commit()

        row = save_report(db, report, data_status=report.get("data_status"))

        task.status = "success"
        task.progress = 100
        task.ref_id = row.id
        task.result_json = {"report_id": row.id, "trade_date": trade_date}
        task.finished_at = datetime.now(timezone.utc)
        db.commit()
        logger.info(f"US research task {task_id} done: report_id={row.id}")
    except Exception as e:
        logger.error(f"US research task {task_id} failed: {e}")
        task = db.query(TaskRecord).filter(TaskRecord.id == task_id).first()
        if task:
            task.status = "failed"
            task.error = str(e)
            task.finished_at = datetime.now(timezone.utc)
            db.commit()
    finally:
        db.close()
