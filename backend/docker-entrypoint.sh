#!/bin/sh
set -e

if [ "$CONTAINER_ROLE" = "worker" ]; then
  # arq worker + APScheduler (scheduler starts via WorkerSettings.on_startup).
  # Migrations are owned by the api container — wait until they have run.
  python - <<'PY'
import sys, time
from sqlalchemy import create_engine, inspect
from app.core.config import settings
for _ in range(60):
    try:
        if "alembic_version" in inspect(create_engine(settings.DATABASE_URL)).get_table_names():
            sys.exit(0)
    except Exception:
        pass
    time.sleep(2)
sys.exit("database migrations not ready after 120s")
PY
  exec arq app.tasks.queue.WorkerSettings
fi

# Apply DB migrations on every boot — idempotent, keeps schema in sync
alembic upgrade head

exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2
