# AI 知识库技术方案与代码对应文档

> **用途：** 说明技术设计中的模块如何落到当前代码结构，便于后续实现、审查、交接和持续维护。  
> **当前阶段：** 一期 MVP 骨架。当前实现为内存版最小可测代码，并已具备数据库模式下的知识库/文档服务、文件落盘、解析任务与 `.txt` 内容块入库骨架；后续会逐步替换为 PostgreSQL 运行态、Milvus、Celery、真实 LLM。  
> **维护规则：** 技术设计变更或代码模块迁移时，必须同步更新本文档。

---

## 1. 实现依据与总体对应关系

### 1.1 实现依据

当前实现同时对齐以下设计/计划文档：

| 文档 | 作用 | 实施优先级 |
|------|------|-----------|
| `docs/design/2025-06-23-kg-platform-bootstrap.md` | 知识图谱一期启动计划：强调 3 库基础设施、全量 schema、审核/冲突/闭环 API、管理界面空状态 | **高** |
| `docs/design/2026-06-22-ai-knowledge-base-technical-design.md` | AI 知识库技术设计：强调单引擎 + function calling、RAG 工具函数、API/数据流/部署架构 | **高** |
| `docs/api/api-reference.md` | 当前 API 行为记录 | 持续维护 |

### 1.2 关键统一决策

| 2025 启动计划写法 | 当前统一决策 | 原因 |
|------------------|-------------|------|
| Neo4j Enterprise | Neo4j Community 起步 | 降低授权成本；当前规模单机够用 |
| 中文图谱 Label（产品/品牌/技术参数） | 英文 Schema（Product/Feature/Parameter 等） | 便于代码、LightRAG、Neo4j 查询统一 |
| `product_text_vectors` / `product_table_vectors` | 一期统一 `knowledge_chunks`，二期再拆 `table_vectors`/`image_vectors` | 一期简单表格可作为 Markdown chunk 入库，降低复杂度 |
| `backend/routers`, `backend/db` | `backend/app/api`, `backend/app/core` | 保持当前 FastAPI 项目结构一致 |

### 1.3 总体对应关系

| 技术设计模块 | 当前代码位置 | 当前实现状态 | 后续替换/增强 |
|-------------|-------------|-------------|---------------|
| FastAPI 应用入口 | `backend/app/main.py` | ✅ 已实现 MVP | 已支持 `create_app_from_env()` 按 `DATABASE_URL` 切换内存/数据库服务，并托管 `/login`、`/admin` 和 `/static` |
| 健康检查 API | `backend/app/main.py` | ✅ 已实现 `/health` | 无 |
| 知识库管理 API | `backend/app/main.py` | ✅ 内存版实现 + 数据库版服务已接入 | 已支持 create/list/get/update/delete，并接入知识库级 `can_view/can_grant` 权限；后续迁移到 `app/api/kb.py` + 请求级 session 注入 |
| 文档上传 API | `backend/app/main.py` | ✅ 内存版元数据记录 + 数据库模式已接文件落盘/解析任务/`.txt`/`.docx`/基础 `.pdf` 内容块/文档 chunk | 已支持文档详情读取与文档元数据字段（department/product_line/visibility/security_level/tags）；后续接 Unstructured.io、OCR/VLM、Milvus 向量化 |
| 文档元数据底座 | `backend/app/models/document.py`, `backend/app/services/document_service.py`, `backend/app/services/db_document_service.py`, `backend/app/main.py` | ✅ 已实现阶段基础能力 | 已支持元数据落库、上传写入、详情回显和管理员文档详情展示；后续接用户知识视图规则与检索前元数据硬过滤 |
| 文档解析 | `backend/app/services/parser_service.py` | ✅ 已接 Unstructured adapter，`.docx`/`.pdf` 优先走 `partition()`，失败回退基础解析 | 后续增强 OCR/VLM/复杂表格解析 |
| 同步问答 API | `backend/app/main.py` | ✅ 已实现 `/api/qa/ask/sync` | 数据库模式下已写入问答会话和消息；后续补 `/api/qa/ask` SSE 流式接口 |
| 问答运营闭环 | `backend/app/services/qa_ops_service.py`, `backend/app/models/kg_ops.py`, `backend/app/main.py` | ✅ 已实现阶段 2 v1 | 已支持问答记录、会话消息、答案反馈、负反馈生成知识缺口、issue 状态更新；后续接用户聊天页、运营看板、自动补知识入口 |
| RAG 编排服务 | `backend/app/services/rag_service.py` | ✅ 已实现 tool-use 风格骨架 | 后续接真实 LLM SDK、会话存储 |
| 数据库检索桥接 | `backend/app/services/db_chunk_loader.py` | ✅ 已实现 `DocumentChunk` → retrieval chunk；问答前同步构建 BM25 索引 | 后续替换为 Milvus / hybrid search adapter |
| 工具函数集 | `backend/app/services/tools.py` | ✅ 已实现 retrieve/BM25/rerank/graph 占位 | 后续接 Milvus、真实 BM25 索引、LightRAG/Neo4j |
| 规则重排序 | `backend/app/services/reranker.py` | ✅ 已实现 | 二期可接 BGE-Reranker-v2-m3 |
| 测试体系 | `backend/tests/*` | ✅ 已覆盖核心行为 | 每新增 API/模块补测试 |
| API 文档 | `docs/api/api-reference.md` | ✅ 已建立 | 每次 API 变更同步维护 |
| 前端登录页/管理台壳 | `backend/app/static/login.html`, `backend/app/static/admin.html`, `backend/app/static/admin.js` | ✅ 已实现最小静态壳 | 管理员工作台已补齐知识库区、单文件上传区、文档列表区、文档详情区、权限区、知识缺口区、删除确认区与消息区；后续再做更强的前端行为测试 |
| 查询用户问答页 | `backend/app/static/qa.html`, `backend/app/static/qa.js`, `backend/app/main.py` | ✅ 已实现阶段 3 v1 | 已支持当前用户加载、可见知识库选择、同步提问、答案/来源展示、有用/无用反馈、会话历史侧栏和历史消息展示；后续接 Markdown、SSE 流式输出和来源跳转 |
| Docker Compose 三库基础设施 | `docker-compose.yml` | ✅ 已实现 | PostgreSQL + Redis + Milvus + Neo4j Community |
| PostgreSQL 全量 Schema | `backend/app/models/*` | ✅ 已实现骨架 | 下一步接真实数据库会话与迁移 |
| 数据库服务层 | `backend/app/services/db_kb_service.py`, `backend/app/services/db_document_service.py` | ✅ 已实现最小能力 | 后续接迁移工具与请求级 session 生命周期 |
| Service Provider | `backend/app/main.py::build_app_state_services`, `backend/app/main.py::create_app_from_env` | ✅ 已实现 | `DATABASE_URL` 存在时自动进入数据库模式；`DEFAULT_OWNER_ID` 提供临时 owner |
| Neo4j 约束与索引 | `backend/neo4j/*.cypher` | ❌ 未实现 | 使用英文 Schema 重写约束 |
| Milvus Collection 初始化 | `backend/app/core/milvus.py` | ❌ 未实现 | 一期 `knowledge_chunks` |
| 审核/冲突/闭环 API | `backend/app/main.py`（当前），后续 `backend/app/api/review.py` 等 | ✅ 已实现空端点 | 后续接 PostgreSQL 表与状态流转 |
| MergeEngine 规则归并 | `backend/app/services/merge_engine.py` | ✅ 已实现 | 后续接入 review/conflict 工作流 |

---

## 2. 代码结构说明

当前已实现的目录结构：

```text
backend/
├── app/
│   ├── __init__.py
│   ├── main.py
│   ├── core/
│   │   ├── __init__.py
│   │   └── db.py
│   ├── models/
│   │   ├── __init__.py
│   │   ├── user.py
│   │   ├── knowledge_base.py
│   │   ├── document.py
│   │   └── kg_ops.py
│   └── services/
│       ├── __init__.py
│       ├── auth_service.py
│       ├── session_store.py
│       ├── kb_service.py
│       ├── db_kb_service.py
│       ├── db_chunk_loader.py
│       ├── document_service.py
│       ├── db_document_service.py
│       ├── ingestion_service.py
│       ├── parser_service.py
│       ├── rag_service.py
│       ├── reranker.py
│       ├── tools.py
│       └── merge_engine.py
│   ├── static/
│   │   ├── login.html
│   │   ├── admin.html
│   │   └── admin.js
├── tests/
│   ├── __init__.py
│   ├── test_api.py
│   ├── test_bootstrap_endpoints.py
│   ├── test_db_document_service.py
│   ├── test_db_kb_service.py
│   ├── test_document_api.py
│   ├── test_document_service.py
│   ├── test_kb_api.py
│   ├── test_kb_service.py
│   ├── test_language_selection.py
│   ├── test_merge_engine.py
│   ├── test_models_schema.py
│   ├── test_rag_service.py
│   ├── test_runtime_database_mode.py
│   ├── test_service_provider.py
│   └── test_tools.py
├── pytest.ini
└── requirements.txt
```

---

## 3. 技术路线到代码模块的映射

### 3.1 单引擎 + Function Calling 工具模式

**技术设计要求：**

- 不采用四智能体串行调用
- LLM 作为单个问答引擎
- 检索、BM25、重排、图谱查询都做成纯函数工具
- 避免 LLM 调 LLM 的递归延迟

**代码对应：**

| 设计元素 | 代码 |
|---------|------|
| 单引擎问答入口 | `backend/app/services/rag_service.py::RAGService`，当前会合并 `retrieve` + `bm25_search` sources |
| 工具定义 | `RAGService.tool_definitions` |
| 工具执行分发 | `RAGService._execute_tool()` |
| 语义检索工具 | `KnowledgeTools.retrieve()` |
| BM25 工具 | `KnowledgeTools.bm25_search()` |
| 重排工具 | `KnowledgeTools.rerank()` |
| 图检索工具（二期占位） | `KnowledgeTools.graph_search()` |

**当前测试：**

- `backend/tests/test_rag_service.py`
- `backend/tests/test_rag_service.py::test_rag_service_merges_retrieve_and_bm25_sources`
- `backend/tests/test_tools.py`
- `backend/tests/test_reranker.py`

---

### 3.2 知识库管理

**技术设计要求：**

- 支持知识库新建、列表、详情
- 支持后续扩展权限控制、可见范围、读写权限

**代码对应：**

| API | 当前代码 | 当前存储 |
|-----|---------|---------|
| `POST /api/kb` | `backend/app/main.py::create_knowledge_base()` | 内存模式：`InMemoryKnowledgeBaseService._items`；数据库模式：`knowledge_bases` |
| `GET /api/kb` | `backend/app/main.py::list_knowledge_bases()` | 内存模式：`InMemoryKnowledgeBaseService._items`；数据库模式：`knowledge_bases` |
| `GET /api/kb/{kb_id}` | `backend/app/main.py::get_knowledge_base()` | 内存模式：`InMemoryKnowledgeBaseService._items`；数据库模式：`knowledge_bases` |
| `PUT /api/kb/{kb_id}` | `backend/app/main.py::update_knowledge_base()` | 内存模式：`InMemoryKnowledgeBaseService._items`；数据库模式：`knowledge_bases` |
| `DELETE /api/kb/{kb_id}` | `backend/app/main.py::delete_knowledge_base()` | 内存模式：`InMemoryKnowledgeBaseService._items`；数据库模式：`knowledge_bases` |

**当前测试：**

- `backend/tests/test_kb_api.py`
- `backend/tests/test_kb_crud_api.py`

**后续迁移目标：**

```text
backend/app/api/kb.py
backend/app/services/kb_service.py
backend/app/models/knowledge_base.py
backend/app/schemas/kb.py
```

---

### 3.3 文档上传与入库

**技术设计要求：**

- 支持文档上传
- 上传后记录 pending 状态
- 后续触发异步解析、分块、向量化、写入 Milvus

**代码对应：**

| API | 当前代码 | 当前存储 |
|-----|---------|---------|
| `POST /api/kb/{kb_id}/documents/upload` | `backend/app/main.py::upload_document()` | 内存模式：`app.state.documents_by_kb`；数据库模式：`documents` + 文件落盘 + `parse_tasks` + `.txt` `content_blocks` + `document_chunks` |
| `GET /api/kb/{kb_id}/documents` | `backend/app/main.py::list_documents()` | 内存模式：`app.state.documents_by_kb`；数据库模式：`documents` |

**当前测试：**

- `backend/tests/test_document_api.py`
- `backend/tests/test_database_mode_api.py`
- `backend/tests/test_upload_ingest_flow.py`

**当前已实现链路：**

```text
数据库模式 UploadFile → DbDocumentService 写 documents → IngestionService 文件落盘 → ParseTask → parser_service(.txt) → ContentBlock → DocumentChunk
```

**当前限制：**

- `create_app()` 默认仍是内存模式，数据库模式主要通过测试和显式参数启用。
- `parser_service.py` 已接入 Unstructured adapter：`.docx` / `.pdf` 优先调用 `unstructured.partition.auto.partition()`。
- Unstructured 失败或依赖缺失时，会回退到基础 `.docx` 段落解析和基础 `.pdf` 文本流解析。
- `.txt`/`.docx`/基础 `.pdf` 目前按空行分段为 `document_chunks`，后续需替换为 token-aware splitter。
- 复杂 PDF、扫描件、复杂表格和图片知识当前仍需要 OCR、VLM 或表格专项解析。
- Celery、Embedding、Milvus 尚未接入。

**后续迁移目标：**

```text
backend/app/api/document.py
backend/app/services/document_service.py
backend/app/services/ingestion_service.py
backend/worker/tasks/ingest.py
backend/worker/parsers/unstructured_parser.py
backend/app/models/document.py
```

---

### 3.4 检索与重排序

**技术设计要求：**

一期：

- BGE-Large-ZH + Milvus 语义检索
- rank_bm25 稀疏检索
- 规则重排序

二期：

- BGE-Reranker-v2-m3
- LightRAG + Neo4j 图检索

**当前代码对应：**

| 能力 | 当前代码 | 当前实现 |
|------|---------|----------|
| 语义检索 | `KnowledgeTools.retrieve()` + `backend/app/main.py::ask_sync()` | 内存片段 + 数据库模式 `document_chunks` 临时加载 + token overlap 模拟；RAGService 会合并 BM25 sources |
| BM25 检索 | `KnowledgeTools.bm25_search()` | 轻量 BM25 实现 |
| BM25 索引构建 | `KnowledgeTools.build_bm25_index()` | 内存倒排统计 |
| 重排序 | `RuleReranker.rerank()` | 原始 score + 内容重叠 + 标题重叠 + 位置加权 |
| 图检索 | `KnowledgeTools.graph_search()` | 二期占位，当前返回空列表 |

**当前测试：**

- `backend/tests/test_tools.py`
- `backend/tests/test_reranker.py`
- `backend/tests/test_database_mode_api.py::test_database_mode_qa_uses_uploaded_document_chunks`
- `backend/tests/test_database_mode_api.py::test_database_mode_qa_builds_bm25_index_from_uploaded_chunks`

**当前数据库检索桥接：**

```text
/api/qa/ask/sync → DbChunkLoader 查询当前 kb_id 的 document_chunks → 填充 KnowledgeTools.vector_chunks_by_kb → build_bm25_index → RAGService tool-use 检索
```

该桥接只用于 Milvus 接入前形成上传到问答的本地闭环，不替代最终向量库检索。

**后续替换点：**

| 当前实现 | 后续实现 |
|---------|---------|
| 内存片段检索 | Milvus `knowledge_chunks` collection |
| 轻量 BM25 内存索引 | 可先保留，后续按知识库构建持久索引 |
| 规则 Reranker | BGE-Reranker-v2-m3 |
| 空 graph_search | LightRAG + Neo4j local/hybrid query |

---

### 3.5 中英文 Embedding 切换

**技术设计要求：**

- 中文为主：BGE-Large-ZH
- 英文占比 >30%：切换 M3E

**代码对应：**

| 能力 | 代码 |
|------|------|
| 语言比例判断 | `KnowledgeTools.select_embedding_model()` |
| 查询时记录模型 | `KnowledgeTools.retrieve()` 中设置 `current_embedding_model` |

**当前测试：**

- `backend/tests/test_language_selection.py`

**后续注意：**

真实接入 Milvus 后，不同 embedding 模型的向量维度可能不同。若 BGE 与 M3E 维度不一致，需要：

1. 使用两个 Milvus collection；或
2. 固定统一多语言 embedding 模型；或
3. 只在英文知识库开启 M3E。

这一点实现前必须确认。

---

### 3.6 LLM 调用封装

**技术设计要求：**

- 支持通义千问 / GPT-4 / Claude
- 支持 function calling / tool use
- 私有化或数据不出境时，优先通义千问或私有化 Qwen

**当前代码对应：**

| 能力 | 当前代码 | 当前状态 |
|------|---------|----------|
| LLM tool-use 模拟 | `backend/app/main.py::SimpleLLM` | 测试用假实现 |
| RAGService 依赖注入 LLM | `RAGService.__init__(llm=...)` | ✅ 已支持 |

**后续迁移目标：**

```text
backend/app/services/llm_service.py
```

建议接口保持：

```python
async def generate_with_tools(
    messages: list[dict],
    tools: list[dict] | None = None,
    tool_choice: str = "auto",
    stream: bool = False,
) -> dict:
    ...
```

---

## 4. 测试与行为对应关系

| 测试文件 | 覆盖内容 |
|---------|---------|
| `test_api.py` | 健康检查、同步问答 API |
| `test_kb_api.py` | 知识库创建、列表、详情 |
| `test_document_api.py` | 文档上传、文档列表、doc_count 更新 |
| `test_rag_service.py` | RAGService 使用工具结果生成答案和来源 |
| `test_tools.py` | BM25 精确匹配行为 |
| `test_reranker.py` | 规则重排序偏好相关片段 |
| `test_language_selection.py` | 中英文 embedding 模型切换 |
| `test_db_chunk_loader.py` | DbChunkLoader 将 PostgreSQL `document_chunks` 转换为检索 chunk |
| `test_database_mode_api.py` | 数据库模式 API：知识库持久化、文档上传、文件落盘、解析任务返回、问答读取 document_chunks |
| `test_runtime_database_mode.py` | 环境变量驱动的数据库模式启动、session_factory、DEFAULT_OWNER_ID |
| `test_upload_ingest_flow.py` | IngestionService：文件落盘、parse_task 创建、`.txt` 解析为 content_blocks |
| `test_qa_ops_api.py` | 问答记录、会话消息、答案反馈、知识缺口和权限边界 |
| `test_frontend_shell.py` | 登录页、管理台壳、查询用户问答页静态结构和 JS 关键交互钩子 |

---

## 5. 后续实施优先级

### P0-1：基础设施与 2025 启动计划对齐

先补齐 `docs/design/2025-06-23-kg-platform-bootstrap.md` 要求的启动基础设施：

```text
docker-compose.yml
PostgreSQL
Redis
Milvus + etcd + MinIO
Neo4j Community
```

### P0-2：PostgreSQL 全量 Schema

将内存存储替换为 PostgreSQL，并补齐 2025 启动计划要求的运营/审核表：

```text
users
roles
knowledge_bases
kb_permissions
documents
document_chunks
parse_tasks
content_blocks
review_queue
conflict_log
knowledge_issues
audit_log
conversations
messages
```

### P0-3：代码结构拆分

当前 `main.py` 承担路由和内存存储，后续应拆为：

```text
app/api/kb.py
app/api/document.py
app/api/qa.py
app/services/kb_service.py
app/services/document_service.py
app/schemas/*.py
```

### P0-4：持久化迁移

将内存存储替换为 PostgreSQL：

```text
users
knowledge_bases
kb_permissions
documents
document_chunks
conversations
messages
```

### P0-5：文档入库

接入：

```text
UploadFile → 文件存储 → Celery ingest task → Unstructured.io → Splitter → Embedding → Milvus
```

### P0-6：真实检索

替换：

```text
KnowledgeTools.retrieve() 内存 overlap → Milvus search
KnowledgeTools.bm25_search() 内存索引 → 按知识库预构建 BM25 索引
```

### P0-7：审核/冲突/闭环 API 空端点

对齐 2025 启动计划，先打通端点，允许返回空列表或 0 统计：

```text
GET /api/review
GET /api/conflicts
GET /api/dashboard/summary
GET /api/issues
```

### P0-8：MergeEngine 规则归并

按 TDD 实现：

```text
backend/app/services/merge_engine.py
```

覆盖：
- 标量字段一致去重
- 标量字段冲突标记
- 列表字段取并集
- 标识字段冲突检测

### P0-9：真实 LLM

将 `SimpleLLM` 替换为 `LLMService`，支持：

- 通义千问 function calling
- OpenAI-compatible function calling
- Claude tool use（如合规允许）

---

## 6. 维护规则

1. 技术设计文档中每个新增模块，都必须在本文档中说明对应代码位置。
2. 代码迁移或重命名后，必须同步更新本文档。
3. 新增测试文件时，必须在「测试与行为对应关系」表中登记。
4. 如果某项技术选型被替换（如 Milvus → Qdrant），必须写清迁移影响和被替换代码。
5. 每个阶段结束时，应补充「当前实现状态」和「后续替换点」。
