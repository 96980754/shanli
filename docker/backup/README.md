# 知识库资产导出 / 导入

一对脚本，用于把**知识库资产**（知识库 / 向量库 / 图谱 / 文件 / 本体）从一台机器搬到另一台机器或全新部署。

| 脚本 | 作用 |
| --- | --- |
| `export-kb-assets.sh` | 整栈停机冷备，产出 `kb-assets-YYYYMMDD-HHMM/` 目录 + 同名 `.tar` 单包 |
| `import-kb-assets.sh` | 校验并恢复产物；面向「另一台机器 / 新部署」设计，可重复执行 |

与同目录 `backup-all.sh` 的区别：`backup-all.sh` 是**整栈灾备**，把整个 `docker/volumes` 打包
（含 users / conversations / agents / skills，`saves/agents` 单独就 954MB），只能在本机原地回滚、没有恢复脚本。
本对脚本是**定向迁移**，产物约 3.4GB，且带 manifest 指纹与校验和。

## 导出

```bash
./docker/backup/export-kb-assets.sh          # 环境变量 BACKUP_DIR 可改输出目录，默认 /home/hmy/backups
```

导出期间**整个 prod 栈会停机**（停机窗口包含全部打包时间，实测约 5 分钟）。脚本用 `trap` 保证无论成败都会
把栈拉回来，并在结束时校验 `api-prod` / `milvus` 已 healthy。

### 为什么停机，以及为什么是这个顺序

先 `stop -t 60` 整栈停机，再单独拉起 postgres 做逻辑导出，最后冷拷 Milvus / etcd / Neo4j / MinIO。
停机窗口内不存在任何写入者，所以 PG 快照与对象存储快照落在同一个「无写入区间」内，
不会出现「PG 里有行、对象存储里没有文件」的悬空。

`stop` 必须带 `-t 60`（默认 10s 会 SIGKILL）；脚本会逐个检查容器退出码，出现 137 就中止，
因为被强杀的进程没有机会干净落盘，一致性不再成立。

**唯一的例外是 `milvus`，它必然以 137 退出。** standalone 部署下 milvus 不可能优雅关闭：收到 SIGTERM 后
querynode 会把已加载的 segment「迁移」到集群里的另一个 querynode（日志里每秒一条 `migrate data...`），
standalone 没有第二个 querynode，这个等待永不结束，只能被强杀。把 `-t` 调到 300 只是把停机窗口拉长到
5 分钟，结果不变 —— 所以脚本容忍这一个 137，其余容器仍然中止。

这个 137 不构成数据风险：milvus 的持久化契约本来就是崩溃恢复（RocksMQ WAL 落 `rdb_data`、segment 写进
MinIO 后不可变、元信息走 etcd 事务），SIGKILL 与断电等价；而停机窗口内没有任何写入者，拷到的就是一个
崩溃一致快照。实测被强杀后重启，15 个集合 / 22112 条实体 / 向量检索（自身向量 `score=1.0000` 命中）全部完好。

### 产物结构

```
kb-assets-YYYYMMDD-HHMM/
├── manifest.json                   # 时间点、镜像 tag、逐表行数、kb_ids、图谱节点/关系数、Milvus 集合
├── SHA256SUMS
├── postgres/kb-assets.dump         # pg_dump -Fc --data-only，22 张表（~5MB）
├── neo4j/neo4j.dump                # neo4j-admin database dump（~16MB）
├── milvus/etcd.tar.zst             # Milvus 元信息
├── milvus/milvus-lib.tar.zst       # RocksMQ WAL
├── milvus/minio.tar                # 对象存储（文档 + Milvus 段文件），不压缩
├── milvus/minio_config.tar.zst
├── saves/yuxi-kb-saves.tar.zst     # config/ ontology_registry/ knowledge_base_data/
└── models.tar.zst                  # 知识库用的 best.pt
```

每个包的成员都从目录名开始（`etcd/...`、`minio/...`），导入侧解到对应父目录即可，不必再剥一层。
MinIO 里是 PDF/图片等已压缩内容，实测 zstd 只有 1.03x，所以直接裸 tar。

**Milvus 的状态分布在三处，必须取同一时点、缺一不可**：etcd 存 collection/schema/index/segment 元信息，
`/var/lib/milvus` 存 RocksMQ 消息队列，MinIO 的 `a-bucket` 存段文件。

### 导出范围

Postgres 22 张表 = 21 张知识库表 + `model_providers`。

`model_providers` 不是知识库表，但必须带上：知识库的 `embedding_model_spec` 形如
`siliconflow-cn:BAAI/bge-m3`，靠它解析出可用的 provider。目标机的内置模板只有模板定义、
**不含管理员配置的密钥与 base_url**，只留内置模板等于向量检索全部失败。

**不含**：users / departments / teams / conversations / messages / message_feedbacks / agent_runs /
tool_calls / knowledge_handoffs / operation_logs / tasks / api_keys / agents / skills / mcp_servers /
`knowledge_gaps`，以及 `saves/{logs,threads,agents,skills}`。

## 导入

```bash
./docker/backup/import-kb-assets.sh /home/hmy/backups/kb-assets-20260914-0936
./docker/backup/import-kb-assets.sh /home/hmy/backups/kb-assets-20260914-0936.tar
```

| 参数 | 说明 |
| --- | --- |
| `--force` | 本机已有知识库文件时也覆盖（默认拒绝） |
| `--allow-version-mismatch` | 忽略 Milvus / Neo4j 与导出机的镜像 tag 不一致 |

导入流程：解包 → 校验 SHA256SUMS → 比对存储镜像 tag → 停机 → 还原 Milvus/etcd/MinIO/saves/models
→ `neo4j-admin database load` → 分层启动基础设施（postgres/redis/etcd/minio → milvus）→ 建表
→ 灌数据 → 校验 Postgres 逐表行数 → 拉起服务栈 → 校验图谱节点/关系数与 Milvus 集合列表。

最后一组校验（图谱与向量库）放在服务栈拉起之后：这两个存储这次是真恢复，只比对 Postgres 行数的话，
「文件还原了但库是空的」会被当成导入成功。任何一项对不上都明确报错，不静默放过。

脚本**可重复执行**：已解包过的目录会复用；两张会被启动 seed 抢占的表先清空再灌；校验不通过会明确报错。

### 三个必须知道的坑

1. **必须先灌数据、再起 api。** api 首启的 lifespan 会 seed `model_providers` 与
   `knowledge_base_categories`，跑在恢复之前就会与源机数据撞唯一键，让整个 `pg_restore` 回滚。
   脚本里 `TRUNCATE` 这两张表后再灌，两个方向都安全。

2. **建表必须 `import server.routers`。** `create_tables()` 只能建「已被导入的模块」注册的表，
   裸导入 `pg_manager` 会漏掉 `curated_qa_pairs`（它只被 `repositories/` 下的模块引用），
   建表看着成功，直到 `pg_restore` 报 `relation does not exist` 才暴露。

3. **`--disable-triggers` 是必需的，不是保险。** `knowledge_files` 存在环形外键，`pg_dump` 对此有明确告警；
   且 `curated_qa_pairs.source_message_id / source_feedback_id` 指向对话侧表，本次不迁对话，
   源机非空的值在目标库必然悬空。脚本靠 `--disable-triggers` 把它们灌进来后**立刻置 NULL** ——
   不置 NULL 的话，日后任何 UPDATE 都会重新校验外键并 500。

### 导入后的已知降级

- **没有 Agent、技能、业务线配置**，需要在新环境自行配置后才能对话。
- `knowledge_base_permissions` 里 `subject_type='team'` 之类的授权在新环境**静默失效**（teams/users 未随行）。
- `knowledge_bases.created_by` 是源机用户名，新环境无此人。
- 图谱里含已删除知识库的孤儿节点（源机 22 个 distinct kb_id vs 库里 8 个 live）。
  不做按 kb_id 过滤：没有 APOC，手写 Cypher 导 15069 节点 / 39048 关系既慢又极易丢属性。

## 故障排查

- **宿主 `du` 会谎报数据卷体积**（`docker/volumes/postgresql` 宿主显示 4.0K，实际 136MB），
  该目录是 0700 且属主不是当前用户。体积与挂载判断都要走 `docker exec` / 容器内 `du`。
  脚本的 preflight 会做这件事，低于量级下限就拒绝导出。
- **bind 挂载静默退化为 tmpfs**：Docker Desktop 下宿主目录权限被外部进程改动后，容器启动会回退到内存，
  此时打包出来的是「数据没了」的假备份。preflight 会检查挂载 fstype 必须是 ext4。
- **etcd 起来但提交不了提案**：本机 WSL 的 FUSE 层曾出现 flock 幽灵锁，症状是 milvus 反复重启。
  导入脚本在启动 milvus 之前会先探 etcd 可写性；真的撞上就参考
  `docker-compose.override.etcd-namedvolume.yml` 改用命名卷。
- **`graph` 恒定 unhealthy，是既有配置缺陷、与数据无关**：`docker-compose.prod.yml` 里 neo4j 的
  healthcheck 写的是 `curl -f http://localhost:7474`，但 neo4j 镜像里只有 `wget`、没有 `curl`，
  该检查永远退出 1（`docker inspect` 的 Health.Log 里能看到 `/bin/sh: 1: curl: not found`），
  而 `cypher-shell` 查询一切正常。导入脚本因此**不拿 `Health.Status` 当就绪判据**，改为探
  `cypher-shell` 能否应答 —— 否则一个恒定不 healthy 的容器会让每次导入都误判失败。
  要修就把它改成 `wget -q --spider http://localhost:7474`，但那会重建 graph 容器，未在本次改动中处理。
- **api 镜像里没有 `pg_dump` / `psql`**，所有 PG 操作都走 `postgres:16` 容器（与服务端同版本）。
- 恢复后如需重建 api/worker/web，记得 `docker exec web-prod nginx -s reload`——nginx 会缓存旧的上游 IP。
