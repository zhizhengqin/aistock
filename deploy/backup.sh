#!/bin/bash
# 睿见投研 每日数据库备份 — pg_dump + 保留 14 天
# 服务器 crontab（crontab -e 添加）：
#   30 3 * * * /opt/aistock/deploy/backup.sh >> /data/aistock/backups/cron.log 2>&1

set -euo pipefail

BACKUP_DIR=/data/aistock/backups
KEEP_DAYS=14
STAMP=$(date +%Y%m%d_%H%M%S)
FILE="$BACKUP_DIR/aistock_$STAMP.sql.gz"

mkdir -p "$BACKUP_DIR"

docker exec aistock-pg pg_dump -U aistock aistock | gzip > "$FILE"
echo "[$(date '+%F %T')] backup written: $FILE ($(du -h "$FILE" | cut -f1))"

# prune old backups
find "$BACKUP_DIR" -name 'aistock_*.sql.gz' -mtime +$KEEP_DAYS -delete
echo "[$(date '+%F %T')] backups older than $KEEP_DAYS days pruned"
