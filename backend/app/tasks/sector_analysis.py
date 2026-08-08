from datetime import datetime, timezone
from app.core.database import SessionLocal
from app.core.logger import logger
from app.models.task_record import TaskRecord
from app.models.sector_report import SectorReport


async def sector_analysis_task(ctx, task_id: int, user_id: int = 0):
    """Arq task: run sector analysis with 4 AI agents."""
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

        from app.services.sector_orchestrator import run_sector_analysis
        result = await run_sector_analysis(user_id, task, db)

        decision = result.get("decision", {})
        report = SectorReport(
            report_date=result.get("report_date", datetime.now(timezone.utc).strftime("%Y-%m-%d")),
            bull_json=decision.get("bull_sectors", []),
            bear_json=decision.get("bear_sectors", []),
            neutral_json=decision.get("neutral_sectors", []),
            rotation_json=decision.get("operation_advice", ""),
            summary_json=result.get("agents", {}),
            agents_json=result.get("agents", {}),
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
        logger.info(f"Sector analysis task {task_id} completed: report_id={report.id}")

    except Exception as e:
        logger.error(f"Sector analysis task {task_id} failed: {e}")
        task = db.query(TaskRecord).filter(TaskRecord.id == task_id).first()
        if task:
            task.status = "failed"
            task.error = str(e)
            task.finished_at = datetime.now(timezone.utc)
            db.commit()
    finally:
        db.close()
