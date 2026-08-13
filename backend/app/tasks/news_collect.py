"""ARQ adapter for the pure-data news collection task."""

from app.core.database import SessionLocal
from app.services.news_collector import fetch_news_candidates, persist_news
from app.services.task_execution import TaskExecutionContext, TaskExecutionRunner


async def news_collect_task(ctx, task_id: int):
    async def execute(execution_ctx: TaskExecutionContext):
        # Fetching, parsing and deterministic tagging occur without a DB
        # session.  The returned values are held only in this task callback.
        candidates, errors = fetch_news_candidates()
        return {"candidates": candidates, "errors": errors}

    def persist_result(db, task, result):
        stats = persist_news(
            db,
            result.get("candidates", []),
            result.get("errors", []),
        )
        task.result_json = stats

    return await TaskExecutionRunner(SessionLocal).run(task_id, execute, persist_result)
