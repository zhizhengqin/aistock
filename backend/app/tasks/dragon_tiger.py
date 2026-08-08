from datetime import datetime, timezone
from app.core.database import SessionLocal
from app.core.logger import logger
from app.models.task_record import TaskRecord
from app.models.dragon_tiger_report import DragonTigerReport


async def dragon_tiger_task(ctx, task_id: int, period_days: int, user_id: int):
    """Arq task: run dragon-tiger board analysis."""
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

        from app.services.dragon_tiger_orchestrator import run_dragon_tiger_analysis
        result = await run_dragon_tiger_analysis(period_days, user_id, task, db)

        report = DragonTigerReport(
            user_id=user_id,
            period_days=period_days,
            stats_json=result.get("stats", {}),
            top_stocks_json=result.get("top_stocks", []),
            institutions_json=result.get("institutions", []),
            analysis_text=result.get("analysis", {}).get("summary", ""),
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
        logger.info(f"Dragon tiger task {task_id} completed: report_id={report.id}")

    except Exception as e:
        logger.error(f"Dragon tiger task {task_id} failed: {e}")
        task = db.query(TaskRecord).filter(TaskRecord.id == task_id).first()
        if task:
            task.status = "failed"
            task.error = str(e)
            task.finished_at = datetime.now(timezone.utc)
            db.commit()
    finally:
        db.close()
