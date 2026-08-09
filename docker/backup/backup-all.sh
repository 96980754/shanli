#!/usr/bin/env bash
# 整栈停机冷备份：把整个 docker/volumes 打包成 tar.gz，覆盖 PostgreSQL / Neo4j 图谱 /
# MinIO 文档 / Milvus 向量 全部数据，保证一致快照。
#
# 为什么停机：Neo4j 是 Community 版，`neo4j-admin database dump` 在运行中会报
# "The database is in use"，无法在线备份；postgres/milvus 热拷也有不一致风险。
# 停机时数据干净落盘，打包即一致快照。总数据约 2GB，停机约 1-3 分钟。
#
# 为什么用容器打包：postgres 数据目录是 postgres 用户 0700，宿主普通用户读不到；
# 用 postgres:16 容器以 root 只读挂载打包，无需宿主 sudo。
#
# 用法:
#   按需手动执行:  ./docker/backup/backup-all.sh
#   环境变量: BACKUP_DIR（默认 /home/hmy/backups）
#   每次执行生成独立 yuxi-full-YYYYMMDD-HHMM.tar.gz，不覆盖历史备份，永久留存。
#
# 恢复（整库回滚到某次备份）:
#   cd /home/hmy/demo/shanli/zhishiku
#   docker compose stop
#   docker run --rm \
#     -v "$(pwd)/docker:/dst" \
#     -v /home/hmy/backups:/backups:ro \
#     postgres:16 tar -xzf /backups/yuxi-full-XXXX.tar.gz -C /dst
#   docker compose up -d
#
#   必须用容器以 root 解压：tar 内 postgres 数据归 postgres(uid999)、neo4j 归 7474。
#   宿主普通用户 tar 解压无法 chown，数据会变成当前用户所有，postgres/neo4j 容器
#   将因 0700 权限读不到数据目录而无法启动（与本备份为何用容器打包同理）。

set -uo pipefail
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
COMPOSE_FILE="${COMPOSE_DIR}/docker-compose.yml"

BACKUP_ROOT="${BACKUP_DIR:-/home/hmy/backups}"
LOG_FILE="${BACKUP_ROOT}/backup.log"
TS="$(date +%Y%m%d-%H%M)"
TAR_FILE="${BACKUP_ROOT}/yuxi-full-${TS}.tar.gz"

mkdir -p "${BACKUP_ROOT}"
if [ ! -w "${BACKUP_ROOT}" ]; then
  # 备份目录不可写会导致 backup.log 静默丢失审计，且 docker 会在目录缺失时以 root 自动
  # 创建 bind 挂载源目录（宿主用户不可写）。快速失败，别在不可写的目录上继续。
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] ERROR: 备份目录 ${BACKUP_ROOT} 不可写，请检查所有权（chown 当前用户）" >&2
  exit 1
fi
log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "${LOG_FILE}"; }

# 无论脚本成败都必须把服务栈拉起来，避免停机备份失败导致服务一直挂着
restart_stack() { docker compose -f "${COMPOSE_FILE}" up -d; }
trap 'restart_stack || log "ERROR: 备份后重启失败"' EXIT

log "=== 整栈停机冷备份开始 (${TS}) ==="

# 1. 优雅停机（postgres 干净落盘、neo4j/milvus 一致快照）
if ! docker compose -f "${COMPOSE_FILE}" stop; then
  log "ERROR: 服务停机失败，备份一致性不保证，但仍尝试打包"
fi

# 2. 用临时容器以 root 只读打包 docker/volumes
if docker run --rm \
  -v "${COMPOSE_DIR}/docker:/src:ro" \
  -v "${BACKUP_ROOT}:/out" \
  postgres:16 tar -czf "/out/yuxi-full-${TS}.tar.gz" -C /src volumes; then
  log "备份完成: ${TAR_FILE} ($(du -h "${TAR_FILE}" 2>/dev/null | cut -f1))"
else
  log "ERROR: 打包失败，未生成 ${TAR_FILE}"
fi

log "=== 整栈停机冷备份结束 (${TS}) ==="
