#!/bin/sh
set -eu

container_role="${CONTAINER_ROLE:-api}"

case "$container_role" in
  migrator)
    # Exactly one short-lived Compose service owns schema upgrades.
    exec python -m app.cli.database migrate
    ;;
  worker)
    # Workers never run bootstrap or migrations.  They only start after every
    # Alembic head is present, including databases with multiple heads.
    python -m app.cli.database wait-for-head
    exec arq app.tasks.queue.WorkerSettings
    ;;
  api)
    # Bootstrap is idempotent and exits zero after an upstream probe failure so
    # the API can start; programming/encryption failures remain fatal.
    python -m app.cli.llm_config bootstrap
    exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2
    ;;
  *)
    echo "未知容器角色：$container_role" >&2
    exit 64
    ;;
esac
