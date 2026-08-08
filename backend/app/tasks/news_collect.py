from datetime import datetime, timezone
from app.core.database import SessionLocal
from app.core.logger import logger
from app.models.task_record import TaskRecord


async def news_collect_task(ctx, task_id: int):
    """Arq task: collect news from all sources, dedupe and tag."""
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

        from app.services.news_collector import collect_news
        stats = collect_news(db)

        task.status = "success"
        task.progress = 100
        task.result_json = stats
        task.finished_at = datetime.now(timezone.utc)
        db.commit()
        logger.info(f"News collect task {task_id} done: {stats}")
    except Exception as e:
        logger.error(f"News collect task {task_id} failed: {e}")
        task = db.query(TaskRecord).filter(TaskRecord.id == task_id).first()
        if task:
            task.status = "failed"
            task.error = str(e)
            task.finished_at = datetime.now(timezone.utc)
            db.commit()
    finally:
        db.close()
