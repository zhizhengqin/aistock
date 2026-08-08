def maybe_set_progress(task, db, pct):
    """Update task progress safely. No-op if task/db is None."""
    if task is not None and db is not None:
        task.progress = pct
        db.commit()
