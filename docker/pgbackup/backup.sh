#!/bin/sh
# PostgreSQL 定时备份：启动时立即备份一次，之后每天凌晨 02:00 执行一次，
# 保留最近 BACKUP_RETENTION_DAYS 天的备份文件。
# 备份目录通过 docker-compose 挂载到 /backups。

set -eu

BACKUP_DIR="${BACKUP_DIR:-/backups}"
DB_HOST="${POSTGRES_HOST:-postgres}"
DB_USER="${POSTGRES_USER:-postgres}"
DB_PASSWORD="${POSTGRES_PASSWORD:-postgres}"
DB_NAME="${POSTGRES_DB:-yuxi}"
RETENTION_DAYS="${BACKUP_RETENTION_DAYS:-7}"

backup() {
  local ts
  ts="$(date +%Y%m%d-%H%M)"
  PGPASSWORD="${DB_PASSWORD}" pg_dump -h "${DB_HOST}" -U "${DB_USER}" \
    -Fc "${DB_NAME}" > "${BACKUP_DIR}/yuxi-${ts}.dump"
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] backup done: yuxi-${ts}.dump"
  find "${BACKUP_DIR}" -name 'yuxi-*.dump' -mtime "+${RETENTION_DAYS}" -delete
}

backup

while true; do
  # 计算距下一个凌晨 02:00 的秒数并 sleep，重启容器后也能对齐到固定时刻
  now="$(date +%s)"
  next="$(date -d "02:00 today" +%s)"
  if [ "${next}" -le "${now}" ]; then
    next="$(date -d "02:00 tomorrow" +%s)"
  fi
  sleep "$(( next - now ))"
  backup
done
