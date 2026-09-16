#!/usr/bin/env bash
# 知识库资产导出：只打包知识库相关数据（知识库 / 向量库 / 图谱 / 文件 / 本体），
# 供跨机器迁移或新部署导入，配 import-kb-assets.sh 使用。产物约 3.4GB。
#
# 与 backup-all.sh 的区别：
#   backup-all.sh  整栈灾备，把整个 docker/volumes 打包（含 users/conversations/agents/skills），
#                  只能在本机原地回滚，没有恢复脚本。
#   本脚本          定向迁移，Postgres 走逻辑导出（22 张表），三个存储走冷拷，
#                  产物带 manifest 指纹与 SHA256SUMS，可在另一台机器导入。
#
# 一致性策略：先整栈停机（窗口内不存在任何写入者），再单独拉起 postgres 做逻辑导出，
# 最后冷拷 Milvus/etcd/Neo4j/MinIO。这样 PG 快照与对象存储快照落在同一个「无写入区间」内，
# 不会出现「PG 里有行、对象存储里没有文件」的悬空。代价是停机时间包含整个打包过程。
#
# 为什么用 root 容器打包：postgres 数据是 0700、neo4j 归 uid 7474、volumes 归 root，
# 宿主普通用户读不到；用 postgres:16 容器以 root 只读挂载打包，无需宿主 sudo。
# （postgres 自身数据不进包——走逻辑导出，因此恢复不依赖源机 PG 数据目录格式。）
#
# 用法:
#   ./docker/backup/export-kb-assets.sh
#   环境变量: BACKUP_DIR（默认 /home/hmy/backups）
#   每次生成独立 kb-assets-YYYYMMDD-HHMM/ 目录 + 同名 .tar 单包，不覆盖历史导出。

set -uo pipefail
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
COMPOSE_FILE="${COMPOSE_DIR}/docker-compose.prod.yml"
VOLUMES_DIR="${COMPOSE_DIR}/docker/volumes"
COMPOSE=(docker compose -f "${COMPOSE_FILE}")
# 与目标机 postgres 服务端同版本，且自带 zstd + tar（api 镜像里没有 pg_dump/psql）
TOOL_IMAGE="postgres:16"

BACKUP_ROOT="${BACKUP_DIR:-/home/hmy/backups}"
TS="$(date +%Y%m%d-%H%M)"
STAGE="${BACKUP_ROOT}/kb-assets-${TS}"
TAR_FILE="${BACKUP_ROOT}/kb-assets-${TS}.tar"
LOG_FILE="${BACKUP_ROOT}/kb-assets.log"

# 知识库资产 = 21 张知识库表 + model_providers。
# model_providers 必须带上：知识库的 embedding_model_spec 形如 siliconflow-cn:BAAI/bge-m3，
# 靠它解析出可用的 provider；目标机的内置模板只有模板定义、不含管理员配的密钥与 base_url，
# 只留内置模板等于向量检索全部失败。
PG_TABLES=(
  knowledge_base_categories knowledge_bases knowledge_base_permissions
  knowledge_files knowledge_chunks knowledge_assertions
  knowledge_graph_entities knowledge_graph_entity_mentions
  knowledge_graph_triples knowledge_graph_triple_mentions
  entity_link_candidates knowledge_conflicts knowledge_conflict_publish_tasks
  knowledge_validation_reports knowledge_validation_items
  evaluation_datasets evaluation_dataset_items evaluation_runs evaluation_run_items
  document_qa_pairs curated_qa_pairs
  model_providers
)

# 各存储的数据目录标记，缺任何一个都说明 bind 挂载指错了地方
DATA_MARKERS=(
  milvus/etcd/member milvus/milvus/rdb_data
  milvus/minio/.minio.sys milvus/minio/knowledgebases
  neo4j/data/databases yuxi/ontology_registry
)

mkdir -p "${BACKUP_ROOT}"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "${LOG_FILE}"; }
die() { log "ERROR: $*"; exit 1; }

# 无论成败都要把服务栈拉起来，避免导出失败导致服务一直挂着
restart_stack() { "${COMPOSE[@]}" up -d; }
trap 'restart_stack || log "ERROR: 导出中断后服务栈拉起失败，请手动执行 docker compose -f docker-compose.prod.yml up -d"' EXIT

# 容器命令一律加 timeout：本机 Docker Desktop(WSL) 出现过容器运行时状态失同步，
# docker run 永久挂起而 dockerd 仍报 Up。脚本挂住时 EXIT trap 不会触发，
# 生产就会一直停在停机状态 —— 宁可超时失败，让 trap 把栈拉回来。

wait_healthy() {
  local container="$1" timeout="${2:-120}" waited=0 status
  while [ "${waited}" -lt "${timeout}" ]; do
    status="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "${container}" 2>/dev/null)"
    case "${status}" in healthy | running) return 0 ;; esac
    sleep 3
    waited=$((waited + 3))
  done
  return 1
}

# 从运行中的容器读环境变量，避免在脚本里重复 compose 的默认值（如 NEO4J_AUTH、POSTGRES_USER）
container_env() {
  docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$1" | sed -n "s/^$2=//p"
}

# 记录各服务用的镜像引用（如 neo4j:5.26）。
# 这里必须记 tag 而不是镜像 ID：镜像是本机构建的，ID 不可复现，
# 换一台机器重新构建后必然不同，拿 ID 做跨机版本守卫会永远误报。
image_ref_of() {
  local container
  container="$("${COMPOSE[@]}" ps -aq "$1" | head -1)"
  [ -n "${container}" ] || return 0
  docker inspect -f '{{.Config.Image}}' "${container}" 2>/dev/null
}

# 前置检查：目录可写、磁盘够用、bind 挂载真落在 ext4（未退化为 tmpfs）、数据量级正常
preflight() {
  mkdir -p "${STAGE}/postgres" "${STAGE}/neo4j" "${STAGE}/milvus" "${STAGE}/saves" || die "无法创建 ${STAGE}"
  [ -w "${BACKUP_ROOT}" ] || die "备份目录 ${BACKUP_ROOT} 不可写，请检查所有权"

  local free_mb
  free_mb="$(df -Pm "${BACKUP_ROOT}" | awk 'NR==2{print $4}')"
  # 打包过程同时存在 staging 目录与最终单包，两者各约 3.4GB
  [ "${free_mb:-0}" -ge 8000 ] || die "${BACKUP_ROOT} 可用空间仅 ${free_mb}MB，不足 8GB"

  local probe fstype markers
  probe="$(timeout 120 docker run --rm -v "${VOLUMES_DIR}:/v:ro" "${TOOL_IMAGE}" sh -c '
    grep " /v " /proc/mounts | cut -d" " -f3
    for p in '"${DATA_MARKERS[*]}"'; do
      [ -e "/v/$p" ] && echo found || echo missing
    done')" || die "无法探测数据卷（docker 无响应或超时）"
  fstype="$(echo "${probe}" | sed -n 1p)"
  # Docker Desktop 曾在宿主目录权限被外部进程改动后，让 bind 静默退化为 tmpfs：
  # 容器内看到的是内存里的空目录，此时打包出来的是「数据没了」的假备份。
  [ "${fstype}" = "ext4" ] || die "${VOLUMES_DIR} 实际落在 ${fstype:-未知} 而非 ext4，bind 挂载疑似退化，拒绝导出"

  markers=()
  while read -r line; do markers+=("${line}"); done <<<"$(echo "${probe}" | sed -n '2,$p')"
  local i=0 marker
  for marker in "${DATA_MARKERS[@]}"; do
    [ "${markers[$i]:-}" = "found" ] || die "数据目录标记 ${marker} 不存在，bind 挂载可能指向了错误的位置"
    i=$((i + 1))
  done
  log "前置检查通过（ext4 挂载、空间 ${free_mb}MB、数据目录标记齐全）"
}

# 停机前采集指纹，写进 manifest 供导入侧比对
collect_fingerprints() {
  local sql="" t
  for t in "${PG_TABLES[@]}"; do
    sql+="SELECT '${t}', count(*) FROM ${t} UNION ALL "
  done
  sql="${sql% UNION ALL }"
  PG_COUNTS="$(timeout 120 docker exec postgres psql -U "${PG_USER}" -d "${PG_DB}" -At -F'|' -c "${sql}")" ||
    die "采集 Postgres 行数失败"
  KB_IDS="$(timeout 120 docker exec postgres psql -U "${PG_USER}" -d "${PG_DB}" -At -c \
    "SELECT string_agg(kb_id, ',' ORDER BY kb_id) FROM knowledge_bases")" || die "采集知识库 ID 失败"

  NEO4J_NODES="$(timeout 120 docker exec graph cypher-shell -u "${NEO4J_USER}" -p "${NEO4J_PASS}" \
    --format plain "MATCH (n) RETURN count(n) AS c" | tail -1 | tr -d '[:space:]')" || die "采集图谱节点数失败"
  NEO4J_RELS="$(timeout 120 docker exec graph cypher-shell -u "${NEO4J_USER}" -p "${NEO4J_PASS}" \
    --format plain "MATCH ()-[r]->() RETURN count(r) AS c" | tail -1 | tr -d '[:space:]')" || die "采集图谱关系数失败"

  # 端口没映射到宿主，借用 api 容器里的 pymilvus 走内网查询
  MILVUS_COLLECTIONS="$(timeout 120 docker exec api-prod python -c '
import json, os
from pymilvus import MilvusClient
client = MilvusClient(uri=os.environ["MILVUS_URI"], db_name=os.environ.get("MILVUS_DB_NAME", "default"))
print(json.dumps(sorted(client.list_collections())))')" || die "采集 Milvus 集合列表失败"

  log "指纹采集完成：Postgres $(echo "${PG_COUNTS}" | wc -l) 张表、图谱 ${NEO4J_NODES} 节点/${NEO4J_RELS} 关系"
  log "Milvus 集合: ${MILVUS_COLLECTIONS}"
}

# 停机后检查退出码：137 表示被 SIGKILL，进程没机会落盘，一致性不再成立。
# 唯一的例外是 milvus，且这个例外是结构性的、不是放宽标准：standalone 部署下 milvus 不可能
# 优雅退出 —— 收到 SIGTERM 后 querynode 会把已加载的 segment「迁移」到集群里的另一个 querynode
# （日志里每秒一条 "migrate data..."，实测 60s 内无任何进展），而 standalone 没有第二个
# querynode，这个等待永不结束，只能被强杀；把 -t 调到 300 也只是把停机窗口拉到 5 分钟，结果不变。
# 这个 137 不构成数据风险：milvus 的持久化契约本来就是崩溃恢复（RocksMQ WAL 落 rdb_data、
# segment 写进 MinIO 后不可变、元信息走 etcd 事务），SIGKILL 与断电等价；停机窗口内没有任何
# 写入者，拷到的就是崩溃一致快照。实测被强杀后重启，15 个集合 / 22112 条实体 / 向量检索
# （自身向量 score=1.0000 命中）全部完好。其余容器出现 137 仍然中止。
check_no_forced_kill() {
  local id name code
  for id in $("${COMPOSE[@]}" ps -aq); do
    name="$(docker inspect -f '{{.Name}}' "${id}" | tr -d '/')"
    code="$(docker inspect -f '{{.State.ExitCode}}' "${id}")"
    [ "${code}" = "137" ] || continue
    if [ "${name}" = "milvus" ]; then
      log "提示：milvus 以 SIGKILL(137) 退出（standalone 无法优雅关闭，属预期行为）"
      continue
    fi
    die "容器 ${name} 被 SIGKILL(137) 强杀，数据可能未干净落盘，已中止导出"
  done
}

dump_postgres() {
  # 整栈已停，窗口内没有写入者；只把 postgres 单独拉起来服务这次逻辑导出
  "${COMPOSE[@]}" start postgres >/dev/null || die "启动 postgres 失败"
  wait_healthy postgres 60 || die "postgres 未在 60s 内就绪"

  local table_args=()
  local t
  for t in "${PG_TABLES[@]}"; do table_args+=(-t "${t}"); done
  timeout 300 docker exec postgres pg_dump -U "${PG_USER}" -d "${PG_DB}" -Fc --data-only \
    --no-owner --no-privileges "${table_args[@]}" >"${STAGE}/postgres/kb-assets.dump" ||
    die "pg_dump 失败"
  [ -s "${STAGE}/postgres/kb-assets.dump" ] || die "pg_dump 产物为空"

  "${COMPOSE[@]}" stop -t 60 postgres >/dev/null
  log "Postgres 逻辑导出完成（$(du -h "${STAGE}/postgres/kb-assets.dump" | cut -f1)）"
}

dump_neo4j() {
  # Community 版必须离线 dump（运行中会报 "The database is in use"）。
  # neo4j 镜像 entrypoint 会 su-exec 到 uid 7474，所以输出目录要放得开权限。
  chmod 777 "${STAGE}/neo4j"
  timeout 600 "${COMPOSE[@]}" run --rm --no-deps -v "${STAGE}/neo4j:/dumps" graph \
    neo4j-admin database dump neo4j --to-path=/dumps --overwrite-destination=true ||
    die "neo4j dump 失败"
  [ -s "${STAGE}/neo4j/neo4j.dump" ] || die "neo4j dump 产物为空"
  log "Neo4j dump 完成（$(du -h "${STAGE}/neo4j/neo4j.dump" | cut -f1)）"
}

tar_volumes() {
  # 每个包的成员都从目录名开始（如 etcd/...），导入侧解到对应父目录即可，不必再剥一层。
  # MinIO 里是 PDF/图片等已压缩内容，实测 zstd 只有 1.03x，压了白费时间，直接裸 tar。
  timeout 1800 docker run --rm -v "${VOLUMES_DIR}:/src:ro" -v "${STAGE}:/out" "${TOOL_IMAGE}" sh -c '
    set -e
    tar -c --zstd -f /out/milvus/etcd.tar.zst         -C /src/milvus etcd
    tar -c --zstd -f /out/milvus/milvus-lib.tar.zst   -C /src/milvus milvus
    tar -c --zstd -f /out/milvus/minio_config.tar.zst -C /src/milvus minio_config
    tar -cf       /out/milvus/minio.tar               -C /src/milvus minio
    tar -c --zstd -f /out/saves/yuxi-kb-saves.tar.zst -C /src/yuxi config ontology_registry knowledge_base_data
    tar -c --zstd -f /out/models.tar.zst              -C /src models' ||
    die "存储卷打包失败"
  log "存储卷打包完成（$(du -sh "${STAGE}/milvus" "${STAGE}/saves" "${STAGE}/models.tar.zst" | tr '\n' ' ')）"
}

write_manifest() {
  # 打包用的是 root 容器，产物归 root；统一放开读权限，宿主用户才能算校验和打最终包
  timeout 300 docker run --rm -v "${STAGE}:/s" "${TOOL_IMAGE}" chmod -R a+rX /s ||
    die "规范化产物权限失败"

  {
    printf '{\n'
    printf '  "format_version": 1,\n'
    printf '  "exported_at": "%s",\n' "$(date -Iseconds)"
    printf '  "milvus_db_name": "default",\n'
    printf '  "pg_database": "%s",\n' "${PG_DB}"
    printf '  "kb_ids": "%s",\n' "${KB_IDS}"
    printf '  "neo4j": {"nodes": %s, "relationships": %s},\n' "${NEO4J_NODES}" "${NEO4J_RELS}"
    printf '  "milvus_collections": %s,\n' "${MILVUS_COLLECTIONS}"
    printf '  "images": {\n'
    local first=1 svc image_ref
    for svc in api worker web graph milvus minio etcd postgres redis; do
      image_ref="$(image_ref_of "${svc}")"
      [ -n "${image_ref}" ] || continue
      [ "${first}" -eq 1 ] || printf ',\n'
      first=0
      printf '    "%s": "%s"' "${svc}" "${image_ref}"
    done
    printf '\n  },\n'
    printf '  "pg_counts": {\n'
    first=1
    while IFS='|' read -r t n; do
      [ -n "${t}" ] || continue
      [ "${first}" -eq 1 ] || printf ',\n'
      first=0
      printf '    "%s": %s' "${t}" "${n}"
    done <<<"${PG_COUNTS}"
    printf '\n  }\n'
    printf '}\n'
  } >"${STAGE}/manifest.json" || die "生成 manifest.json 失败"

  (cd "${STAGE}" && find . -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum >SHA256SUMS) ||
    die "生成 SHA256SUMS 失败"
  log "manifest.json 与 SHA256SUMS 生成完成（$(wc -l <"${STAGE}/SHA256SUMS") 个文件）"
}

main() {
  log "=== 知识库资产导出开始 (${TS}) ==="
  preflight

  PG_USER="$(container_env postgres POSTGRES_USER)"
  PG_DB="$(container_env postgres POSTGRES_DB)"
  PG_USER="${PG_USER:-postgres}"
  PG_DB="${PG_DB:-yuxi}"
  NEO4J_USER="$(container_env graph NEO4J_USERNAME)"
  NEO4J_USER="${NEO4J_USER:-neo4j}"
  # NEO4J_AUTH 形如 user/password，只取密码，不落日志
  NEO4J_PASS="$(container_env graph NEO4J_AUTH | cut -d/ -f2-)"

  collect_fingerprints

  log "整栈停机（最长等待 60s 优雅退出；milvus 必然超时被强杀，见 check_no_forced_kill 说明）..."
  "${COMPOSE[@]}" stop -t 60 || die "服务停机失败"
  check_no_forced_kill

  # 顺序要紧：PG 逻辑导出必须在停机之后、冷拷之前，快照才与对象存储同在无写入区间
  dump_postgres
  dump_neo4j
  tar_volumes
  write_manifest

  log "拉起服务栈..."
  restart_stack || die "服务栈拉起失败"
  wait_healthy api-prod 180 || log "WARN: api-prod 未在 180s 内 healthy，请检查 docker logs api-prod"
  wait_healthy milvus 300 || log "WARN: milvus 未在 300s 内 healthy，请检查 docker logs milvus"
  trap - EXIT

  tar -C "${BACKUP_ROOT}" -cf "${TAR_FILE}" "$(basename "${STAGE}")" || die "打单包失败"
  log "=== 导出完成 ==="
  log "目录: ${STAGE}"
  log "单包: ${TAR_FILE} ($(du -h "${TAR_FILE}" | cut -f1))"
  log "校验: cd ${STAGE} && sha256sum -c SHA256SUMS"
}

main "$@"
