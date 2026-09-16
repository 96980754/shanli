#!/usr/bin/env bash
# 知识库资产导入：把 export-kb-assets.sh 的产物恢复到本机 prod 栈（知识库 / 向量库 / 图谱 / 文件 / 本体）。
#
# 目标场景是「另一台机器 / 全新部署」，因此：
#   - 默认拒绝在已有知识库文件的机器上覆盖，确认要覆盖才加 --force
#   - 校验 SHA256SUMS，并比对 Milvus / Neo4j 等存储的镜像 tag（跨大版本数据格式不兼容）
#   - 建表 → 灌数据 → 逐表比对 manifest 行数，不一致就明确报错而不是静默放过
#
# 不导入 users / conversations / agents / skills，导入后的已知降级见 docker/backup/README.md。
#
# 用法:
#   ./docker/backup/import-kb-assets.sh <kb-assets-YYYYMMDD-HHMM 目录 或 同名 .tar 包> [--force] [--allow-version-mismatch]

set -uo pipefail
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
COMPOSE_FILE="${COMPOSE_DIR}/docker-compose.prod.yml"
VOLUMES_DIR="${COMPOSE_DIR}/docker/volumes"
COMPOSE=(docker compose -f "${COMPOSE_FILE}")
TOOL_IMAGE="postgres:16"
NEO4J_UID=7474

FORCE=0
ALLOW_VERSION_MISMATCH=0
INPUT=""
SRC=""

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }
die() { log "ERROR: $*"; exit 1; }

# 无论成败都要把服务栈拉起来，避免导入中断导致服务一直挂着
restart_stack() { "${COMPOSE[@]}" up -d; }
trap 'restart_stack || log "ERROR: 导入中断后服务栈拉起失败，请手动执行 docker compose -f docker-compose.prod.yml up -d"' EXIT

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

container_env() {
  docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$1" | sed -n "s/^$2=//p"
}

parse_args() {
  local arg
  for arg in "$@"; do
    case "${arg}" in
    --force) FORCE=1 ;;
    --allow-version-mismatch) ALLOW_VERSION_MISMATCH=1 ;;
    -h | --help)
      echo "用法: $(basename "${BASH_SOURCE[0]}") <kb-assets-YYYYMMDD-HHMM 目录 或 同名 .tar 包> [--force] [--allow-version-mismatch]"
      exit 0
      ;;
    -*) die "未知参数 ${arg}" ;;
    *)
      [ -z "${INPUT}" ] || die "只能指定一个来源路径"
      INPUT="${arg}"
      ;;
    esac
  done
  [ -n "${INPUT}" ] || die "请指定要导入的目录或 .tar 包"
}

# 传入 .tar 时先解包到同目录，已解好的目录直接复用（重跑省时间）
resolve_source() {
  [ -e "${INPUT}" ] || die "${INPUT} 不存在"
  if [ -d "${INPUT}" ]; then
    SRC="$(cd "${INPUT}" && pwd)"
  else
    local parent base
    parent="$(cd "$(dirname "${INPUT}")" && pwd)"
    base="$(basename "${INPUT}" .tar)"
    SRC="${parent}/${base}"
    mkdir -p "${SRC}" || die "无法创建 ${SRC}"
    log "解包 ${INPUT} ..."
    tar -xf "${INPUT}" -C "${parent}" || die "解包失败"
  fi
  [ -f "${SRC}/manifest.json" ] || die "${SRC} 里没有 manifest.json，不像本脚本的产物"
  [ -f "${SRC}/SHA256SUMS" ] || die "${SRC} 里没有 SHA256SUMS"
}

preflight() {
  local missing=""
  local c
  for c in docker jq sha256sum tar; do command -v "${c}" >/dev/null || missing+=" ${c}"; done
  [ -z "${missing}" ] || die "缺少命令:${missing}"
  [ -f "${COMPOSE_FILE}" ] || die "找不到 ${COMPOSE_FILE}"
  docker image inspect "${TOOL_IMAGE}" >/dev/null 2>&1 ||
    die "本机没有 ${TOOL_IMAGE} 镜像（导入/建表都要用它，api 镜像里没有 pg_dump/psql）"

  local version
  version="$(jq -r '.format_version' "${SRC}/manifest.json")"
  [ "${version}" = "1" ] || die "manifest format_version=${version}，本脚本只认 1"

  # 存储格式跨大版本不兼容：Milvus 的段文件、Neo4j 的 store 都不能跨版本读
  local svc want have mismatch=0
  for svc in graph milvus; do
    want="$(jq -r --arg s "${svc}" '.images[$s] // empty' "${SRC}/manifest.json")"
    [ -n "${want}" ] || continue
    have="$(docker inspect -f '{{.Config.Image}}' "$("${COMPOSE[@]}" ps -aq "${svc}" | head -1)" 2>/dev/null)"
    if [ "${want}" != "${have}" ]; then
      log "WARN: ${svc} 镜像不匹配，导出机 ${want} vs 本机 ${have:-未部署}"
      mismatch=1
    fi
  done
  if [ "${mismatch}" = "1" ] && [ "${ALLOW_VERSION_MISMATCH}" != "1" ]; then
    die "存储镜像版本不一致，跨版本导入可能读不出数据；确认要试就加 --allow-version-mismatch"
  fi
  log "前置检查通过（来源 ${SRC}）"
}

verify_checksums() {
  log "校验产物完整性..."
  (cd "${SRC}" && sha256sum -c --quiet SHA256SUMS) || die "SHA256SUMS 校验失败，产物已损坏或不完整"
}

# 已有知识库文件说明这台机器上跑着真实数据，默认不覆盖
guard_existing_data() {
  [ "${FORCE}" = "1" ] && {
    log "WARN: --force 已指定，若本机已有知识库数据将被覆盖"
    return 0
  }
  local existing
  existing="$(timeout 120 docker run --rm -v "${VOLUMES_DIR}:/v:ro" "${TOOL_IMAGE}" sh -c \
    'ls -A /v/milvus/minio/knowledgebases 2>/dev/null | head -5')"
  [ -z "${existing}" ] ||
    die "本机 minio/knowledgebases 下已有知识库文件，导入会覆盖它们；确认请加 --force"
}

restore_volumes() {
  # 包内成员从目录名开始（etcd/... 、minio/...），解到对应父目录即可。
  # 必须用 root 容器：既要删掉 root/999/7474 属主的旧目录，也要让 tar 原样还原属主。
  timeout 1800 docker run --rm \
    -v "${VOLUMES_DIR}:/dst" \
    -v "${SRC}/milvus:/src:ro" \
    -v "${SRC}/saves:/saves:ro" \
    "${TOOL_IMAGE}" sh -c '
    set -e
    mkdir -p /dst/milvus /dst/yuxi
    rm -rf /dst/milvus/etcd /dst/milvus/milvus /dst/milvus/minio /dst/milvus/minio_config
    tar --zstd -xf /src/etcd.tar.zst         -C /dst/milvus
    tar --zstd -xf /src/milvus-lib.tar.zst   -C /dst/milvus
    tar --zstd -xf /src/minio_config.tar.zst -C /dst/milvus
    tar -xf       /src/minio.tar             -C /dst/milvus
    # 只替换这三个知识库相关子目录，本机原有的 agents/threads/skills/logs 保持不动
    rm -rf /dst/yuxi/config /dst/yuxi/ontology_registry /dst/yuxi/knowledge_base_data
    tar --zstd -xf /saves/yuxi-kb-saves.tar.zst -C /dst/yuxi' ||
    die "存储卷解包失败"

  timeout 600 docker run --rm -v "${VOLUMES_DIR}:/dst" -v "${SRC}:/src:ro" "${TOOL_IMAGE}" \
    tar --zstd -xf /src/models.tar.zst -C /dst || die "models 解包失败"
  log "Milvus / etcd / MinIO / saves / models 已还原"
}

restore_neo4j() {
  # 不冷拷 /data（那会连源机的 515MB 事务日志和 dbms/auth 密码一起搬过来），改用 dump 装载。
  # neo4j 镜像 entrypoint 会 su-exec 到 uid 7474，全新机器的 data 目录是 docker 以 root 建的，
  # 不放开属主装载会 permission denied。
  timeout 120 docker run --rm -v "${VOLUMES_DIR}/neo4j/data:/data" "${TOOL_IMAGE}" sh -c \
    "mkdir -p /data && chown ${NEO4J_UID}:${NEO4J_UID} /data" || die "准备 neo4j data 目录失败"

  timeout 900 "${COMPOSE[@]}" run --rm --no-deps -v "${SRC}/neo4j:/dumps:ro" graph \
    neo4j-admin database load neo4j --from-path=/dumps --overwrite-destination=true ||
    die "neo4j 装载失败"
  log "Neo4j 图谱已还原"
}

start_infra() {
  # 分层启动：milvus 依赖 etcd 与 minio，一起拉起来会撞健康检查
  "${COMPOSE[@]}" up -d postgres redis etcd minio || die "基础设施启动失败"
  wait_healthy postgres 120 || die "postgres 未就绪"
  wait_healthy milvus-etcd 120 || die "etcd 未就绪"
  wait_healthy minio 120 || die "minio 未就绪"

  # etcd 数据落回 bind 后要确认能写：本机曾在 WSL 的 FUSE 层遇到 flock 幽灵锁，
  # etcd 起来但提交不了提案，症状是 milvus 反复重启而不是报错，所以这里先探一下。
  if ! timeout 60 docker exec milvus-etcd sh -c 'etcdctl put __import_probe 1 && etcdctl del __import_probe' >/dev/null 2>&1; then
    die "etcd 无法写入（bind 挂载 flock 异常）。参考 docker-compose.override.etcd-namedvolume.yml 改用命名卷"
  fi

  "${COMPOSE[@]}" up -d milvus || die "milvus 启动失败"
  wait_healthy milvus 300 || die "milvus 未在 300s 内就绪"
  log "postgres / redis / etcd / minio / milvus 已就绪，etcd 可写"
}

migrate_schema() {
  # 只跑迁移、不起 api 进程，调用序照抄 backend/server/utils/lifespan.py 的启动引导。
  # 必须 import server.routers：create_tables() 只能建「已被导入的模块」注册的表，
  # 裸导入 pg_manager 会漏掉 curated_qa_pairs（它只被 repositories 下的模块引用），
  # 建表看着成功，直到 pg_restore 报 relation does not exist 才暴露。
  timeout 600 "${COMPOSE[@]}" run --rm --no-deps api python -c '
import asyncio

import server.routers
from yuxi.storage.postgres.manager import pg_manager


async def migrate():
    pg_manager.initialize()
    await pg_manager.create_tables()
    await pg_manager.ensure_business_schema()
    await pg_manager.ensure_knowledge_schema()


asyncio.run(migrate())
' || die "建表 / 迁移失败"
  log "目标库表结构就绪"
}

restore_postgres_data() {
  # 先清空两张「启动时会被 seed 抢占」的表，再灌源机数据，保证源机的 ID 与配置胜出：
  #   knowledge_base_categories —— api 首启会插入一行「其他」占掉 id=1，与 dump 里的 id=1
  #     撞主键和两条唯一索引。pg_restore 只报这一行错、其余照灌，很容易被当成无关警告跳过，
  #     实际结果是整张分类表没进去。
  #   model_providers —— api 首启的 ensure_builtin_model_providers_in_db 会补内置模板
  #     （provider_id 与源机相同），撞唯一键会让整个 restore 失败。而且内置模板不含管理员
  #     配的密钥/base_url，知识库的 embedding_model_spec 靠它解析，留内置模板等于向量检索全废。
  timeout 120 docker exec postgres psql -U "${PG_USER}" -d "${PG_DB}" -q -c \
    "TRUNCATE knowledge_base_categories RESTART IDENTITY CASCADE;
     TRUNCATE model_providers RESTART IDENTITY CASCADE;" || die "清空 seed 表失败"

  # --disable-triggers 必需：knowledge_files 存在环形外键，pg_dump 对此有明确告警
  timeout 600 docker exec -i postgres pg_restore -U "${PG_USER}" -d "${PG_DB}" \
    --data-only --disable-triggers --no-owner --no-privileges \
    --single-transaction --exit-on-error <"${SRC}/postgres/kb-assets.dump" ||
    die "pg_restore 失败（--single-transaction 已整体回滚，库内仍是导入前状态）"
  log "Postgres 数据已灌入"

  # curated_qa_pairs 的两列指向对话侧表，本次不迁对话，所以源机非空的值在目标库必然是悬空的。
  # 靠 --disable-triggers 它们能灌进来，但日后任何 UPDATE 都会重新校验外键并 500，必须置 NULL。
  timeout 120 docker exec postgres psql -U "${PG_USER}" -d "${PG_DB}" -q -c "
    UPDATE curated_qa_pairs q SET source_message_id = NULL
     WHERE q.source_message_id IS NOT NULL
       AND NOT EXISTS (SELECT 1 FROM messages m WHERE m.id = q.source_message_id);
    UPDATE curated_qa_pairs q SET source_feedback_id = NULL
     WHERE q.source_feedback_id IS NOT NULL
       AND NOT EXISTS (SELECT 1 FROM message_feedbacks f WHERE f.id = q.source_feedback_id);" ||
    die "清理悬空外键失败"
}

verify() {
  local tables expected actual
  tables="$(jq -r '.pg_counts | keys[]' "${SRC}/manifest.json")"

  local sql="" t
  for t in ${tables}; do sql+="SELECT '${t}', count(*) FROM ${t} UNION ALL "; done
  sql="${sql% UNION ALL }"
  expected="$(jq -r '.pg_counts | to_entries[] | "\(.key)|\(.value)"' "${SRC}/manifest.json" | sort)"
  actual="$(timeout 120 docker exec postgres psql -U "${PG_USER}" -d "${PG_DB}" -At -F'|' -c "${sql}" | sort)"
  if ! diff <(echo "${expected}") <(echo "${actual}"); then
    die "行数与 manifest 不一致（左=导出机，右=本机），数据没有完整导入"
  fi
  log "行数比对通过：$(echo "${tables}" | wc -l) 张表与 manifest 全等，知识库 $(jq -r '.kb_ids // ""' "${SRC}/manifest.json" | tr ',' '\n' | grep -c .) 个"

  # 默认分类必须恰好一行：多条会让「兜底分类」的语义失效，零条则新建知识库没有归属
  local defaults
  defaults="$(timeout 120 docker exec postgres psql -U "${PG_USER}" -d "${PG_DB}" -At -c \
    "SELECT count(*) FROM knowledge_base_categories WHERE is_default")"
  [ "${defaults}" = "1" ] || die "knowledge_base_categories 的 is_default 有 ${defaults} 行，应为 1 行"

  local dangling
  dangling="$(timeout 120 docker exec postgres psql -U "${PG_USER}" -d "${PG_DB}" -At -c \
    "SELECT count(*) FROM curated_qa_pairs q
      WHERE (q.source_message_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM messages m WHERE m.id = q.source_message_id))
         OR (q.source_feedback_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM message_feedbacks f WHERE f.id = q.source_feedback_id))")"
  [ "${dangling}" = "0" ] || die "curated_qa_pairs 仍有 ${dangling} 行悬空外键"
  log "分类表与悬空外键检查通过"
}

# graph 的 Health.Status 不可用（原因见 main 里的说明），就绪判据只能是「cypher-shell 能应答」
wait_neo4j() {
  local waited=0
  while [ "${waited}" -lt 120 ]; do
    timeout 30 docker exec graph cypher-shell -u "${NEO4J_USER}" -p "${NEO4J_PASS}" \
      "RETURN 1" >/dev/null 2>&1 && return 0
    sleep 3
    waited=$((waited + 3))
  done
  return 1
}

# 图谱与向量库这一次是真恢复（冷拷卷 + neo4j-admin load），必须确认不是「文件在位、库却是空的」。
# 不能并进 verify()：那一步在 api 启动之前跑，此时只有基础设施在跑，graph 还没起来。
verify_stores() {
  local nodes rels want_nodes want_rels
  want_nodes="$(jq -r '.neo4j.nodes' "${SRC}/manifest.json")"
  want_rels="$(jq -r '.neo4j.relationships' "${SRC}/manifest.json")"
  nodes="$(timeout 120 docker exec graph cypher-shell -u "${NEO4J_USER}" -p "${NEO4J_PASS}" \
    --format plain "MATCH (n) RETURN count(n) AS c" | tail -1 | tr -d '[:space:]')" ||
    die "读取图谱节点数失败"
  rels="$(timeout 120 docker exec graph cypher-shell -u "${NEO4J_USER}" -p "${NEO4J_PASS}" \
    --format plain "MATCH ()-[r]->() RETURN count(r) AS c" | tail -1 | tr -d '[:space:]')" ||
    die "读取图谱关系数失败"
  [ "${nodes}" = "${want_nodes}" ] && [ "${rels}" = "${want_rels}" ] ||
    die "图谱节点/关系数不符（导出机 ${want_nodes}/${want_rels}，本机 ${nodes}/${rels}）"

  local want_cols have_cols
  want_cols="$(jq -r '.milvus_collections | sort | join(",")' "${SRC}/manifest.json")"
  have_cols="$(timeout 120 docker exec api-prod python -c '
import os
from pymilvus import MilvusClient
client = MilvusClient(uri=os.environ["MILVUS_URI"], db_name=os.environ.get("MILVUS_DB_NAME", "default"))
print(",".join(sorted(client.list_collections())))')" || die "读取 Milvus 集合列表失败"
  [ "${have_cols}" = "${want_cols}" ] ||
    die "Milvus 集合不符（导出机 ${want_cols}，本机 ${have_cols}）"

  log "图谱 ${nodes} 节点/${rels} 关系、Milvus $(echo "${want_cols}" | tr ',' '\n' | grep -c .) 个集合均与 manifest 一致"
}

main() {
  parse_args "$@"
  log "=== 知识库资产导入开始 (${INPUT}) ==="
  resolve_source
  preflight
  verify_checksums

  PG_USER="$(container_env postgres POSTGRES_USER)"
  PG_DB="$(container_env postgres POSTGRES_DB)"
  PG_USER="${PG_USER:-postgres}"
  PG_DB="${PG_DB:-yuxi}"
  jq -e '.pg_counts' "${SRC}/manifest.json" >/dev/null || die "manifest 里没有 pg_counts"

  guard_existing_data

  log "整栈停机（最长等待 60s 优雅退出）..."
  "${COMPOSE[@]}" stop -t 60 || die "服务停机失败"

  restore_volumes
  restore_neo4j
  start_infra
  # 顺序要紧：先把数据灌进去，再起 api。若 api 先启动，lifespan 会 seed
  # model_providers / knowledge_base_categories，与源机数据撞唯一键导致 restore 失败。
  migrate_schema
  restore_postgres_data
  verify

  log "拉起服务栈..."
  restart_stack || die "服务栈拉起失败"
  wait_healthy api-prod 180 || log "WARN: api-prod 未在 180s 内 healthy，请检查 docker logs api-prod"
  # neo4j 镜像里没有 curl（compose 的 healthcheck 写的是 `curl -f http://localhost:7474`，
  # 该镜像只有 wget），graph 因此恒定 unhealthy —— Health.Status 不能当就绪判据，
  # 也不能因为它「不 healthy」就判定导入失败。改为功能探活：cypher-shell 能应答才算就绪。
  NEO4J_USER="$(container_env graph NEO4J_USERNAME)"
  NEO4J_USER="${NEO4J_USER:-neo4j}"
  # NEO4J_AUTH 形如 user/password，只取密码，不落日志
  NEO4J_PASS="$(container_env graph NEO4J_AUTH | cut -d/ -f2-)"
  wait_neo4j || die "Neo4j 未在 120s 内应答 Cypher，请检查 docker logs graph"
  verify_stores
  trap - EXIT

  log "=== 导入完成 ==="
  log "请访问前端确认知识库列表、文档、图谱与向量检索结果是否符合预期"
}

main "$@"
