from datetime import datetime, timezone
from app.core.database import SessionLocal
from app.core.logger import logger
from app.models.task_record import TaskRecord
from app.models.main_force_run import MainForceRun


async def main_force_task(ctx, task_id: int, user_id: int):
    """Arq task: run main-force stock selection."""
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

        from app.services.main_force_orchestrator import run_main_force_selection
        result = await run_main_force_selection(user_id, task, db)

        run = MainForceRun(
            user_id=user_id,
            run_date=result.get("run_date", datetime.now(timezone.utc).strftime("%Y-%m-%d")),
            candidates_count=result.get("skim_count", 0),
            filtered_count=result.get("filtered_count", 0),
            recommended_json=result.get("recommended", {}),
            excluded_json=result.get("excluded", []),
            token_total=0,
            task_id=task_id,
            analysis_json=result,
        )
        db.add(run)
        db.commit()
        db.refresh(run)

        task.status = "success"
        task.progress = 100
        task.ref_id = run.id
        task.result_json = {"run_id": run.id}
        task.finished_at = datetime.now(timezone.utc)
        db.commit()
        logger.info(f"Main force task {task_id} completed: run_id={run.id}")

    except Exception as e:
        logger.error(f"Main force task {task_id} failed: {e}")
        task = db.query(TaskRecord).filter(TaskRecord.id == task_id).first()
        if task:
            task.status = "failed"
            task.error = str(e)
            task.finished_at = datetime.now(timezone.utc)
            db.commit()
    finally:
        db.close()
