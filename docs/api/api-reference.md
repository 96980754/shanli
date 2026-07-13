# AI 知识库 API 文档

> **用途：** 持续记录后端 API 的当前实现、请求/响应格式、状态约定与后续变更。  
> **当前阶段：** 一期 MVP 骨架（内存版 + 数据库模式最小实现，真实 Milvus/Celery/LLM 尚未接入）  
> **更新规则：** 每新增/修改/删除 API，必须同步更新本文档，并在对应测试中覆盖。

---

## 1. 通用约定

### 1.1 Base URL

开发环境：

```text
http://localhost:8000
```

### 1.2 数据格式

- 请求体：`application/json`，文件上传接口除外
- 响应体：`application/json`
- 文件上传：`multipart/form-data`

### 1.3 通用错误格式

FastAPI 默认错误格式：

```json
{
  "detail": "错误说明"
}
```

常见状态码：

| 状态码 | 含义 |
|-------|------|
| 200 | 请求成功 |
| 404 | 资源不存在 |
| 422 | 参数校验失败 |
| 500 | 服务端错误 |

---

## 2. 健康检查

### GET `/health`

用于检查服务是否运行。

**响应：**

```json
{
  "status": "ok"
}
```

**测试覆盖：** `backend/tests/test_api.py::test_health_endpoint_returns_ok`

---

## 3. 知识库管理 API

### POST `/api/kb`

创建知识库。

**请求体：**

```json
{
  "name": "产品线知识库",
  "description": "产品文档",
  "visibility": "department"
}
```

字段说明：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `name` | string | 是 | 知识库名称 |
| `description` | string | 否 | 知识库描述，默认空字符串 |
| `visibility` | string | 否 | 可见范围，当前支持 `all` / `department` / `project` / `personal`，默认 `department` |

**响应：**

```json
{
  "id": "uuid",
  "name": "产品线知识库",
  "description": "产品文档",
  "visibility": "department",
  "doc_count": 0
}
```

**当前实现说明：**

- 默认 `create_app()` 在未配置环境变量时使用 `InMemoryKnowledgeBaseService` / `InMemoryDocumentService`
- 启动环境存在 `DATABASE_URL` 时，`create_app_from_env()` 会使用 `create_session_factory()` 创建数据库 session，并切换到数据库版服务
- 数据库模式需要配置 `DEFAULT_OWNER_ID`，用于创建知识库时写入 owner；后续会替换为登录态用户 ID 或初始化 seed
- 数据库版服务已具备最小能力：创建知识库、查询知识库、上传文档元数据、更新 `doc_count`

**测试覆盖：**

- `backend/tests/test_kb_api.py::test_create_knowledge_base_then_get_it_by_id`
- `backend/tests/test_db_kb_service.py`
- `backend/tests/test_service_provider.py::test_build_app_state_services_can_switch_to_database_services`
- `backend/tests/test_runtime_database_mode.py`

---

### GET `/api/kb`

列出当前用户可见知识库。

**请求头：**

```text
Authorization: Bearer <token>
```

**响应：**

```json
{
  "items": [
    {
      "id": "uuid",
      "name": "FAQ知识库",
      "description": "FAQ",
      "visibility": "all",
      "doc_count": 0
    }
  ]
}
```

**当前实现说明：**

- 当前已经接入知识库级 `can_view` 权限过滤
- 未被授予 `can_view` 的用户，看不到对应知识库
- 数据库模式与内存模式都通过知识库权限记录决定可见性

**测试覆盖：**

- `backend/tests/test_kb_crud_api.py::test_ungranted_user_cannot_see_knowledge_base_in_list`

---

### GET `/api/kb/{kb_id}`

获取知识库详情。

**路径参数：**

| 参数 | 类型 | 说明 |
|------|------|------|
| `kb_id` | string | 知识库 ID |

**请求头：**

```text
Authorization: Bearer <token>
```

**成功响应：**

```json
{
  "id": "uuid",
  "name": "产品线知识库",
  "description": "产品文档",
  "visibility": "department",
  "doc_count": 0
}
```

**失败响应：**

```json
{
  "detail": "Knowledge base not found"
}
```

或：

```json
{
  "detail": "Permission denied"
}
```

**当前实现说明：**

- 当前要求用户对该知识库拥有 `can_view`
- 若用户未被授权查看，则返回 `403 Permission denied`

**测试覆盖：**

- `backend/tests/test_kb_api.py::test_create_knowledge_base_then_get_it_by_id`

---

### PUT `/api/kb/{kb_id}`

更新知识库信息。

**路径参数：**

| 参数 | 类型 | 说明 |
|------|------|------|
| `kb_id` | string | 知识库 ID |

**请求体：**

```json
{
  "name": "更新后的知识库",
  "description": "新的描述",
  "visibility": "department"
}
```

**成功响应：**

```json
{
  "id": "uuid",
  "name": "更新后的知识库",
  "description": "新的描述",
  "visibility": "department",
  "doc_count": 0
}
```

数据库模式下 `id` / `doc_count` 为整数，其余字段结构保持一致。

**失败响应：**

```json
{
  "detail": "Knowledge base not found"
}
```

**测试覆盖：**

- `backend/tests/test_kb_crud_api.py::test_update_knowledge_base_changes_name_description_and_visibility`
- `backend/tests/test_kb_crud_api.py::test_update_missing_knowledge_base_returns_404`

---

### DELETE `/api/kb/{kb_id}`

删除知识库。

**路径参数：**

| 参数 | 类型 | 说明 |
|------|------|------|
| `kb_id` | string | 知识库 ID |

**请求头：**

```text
Authorization: Bearer <token>
```

**成功响应：**

```json
{
  "deleted": true
}
```

**失败响应：**

```json
{
  "detail": "Knowledge base not found"
}
```

或：

```json
{
  "detail": "Permission denied"
}
```

**当前实现说明：**

- 当前要求用户对该知识库拥有 `can_grant`
- 阶段 1 中 `can_grant` 同时承担知识库删除权限

**测试覆盖：**

- `backend/tests/test_kb_crud_api.py::test_delete_knowledge_base_removes_it_from_list_and_detail`
- `backend/tests/test_delete_missing_knowledge_base_returns_404`

---

### GET `/api/kb/{kb_id}/permissions`

获取知识库权限列表。

**请求头：**

```text
Authorization: Bearer <token>
```

**成功响应：**

```json
{
  "items": [
    {
      "user_id": "1001",
      "username": "viewer",
      "can_view": true,
      "can_upload": false,
      "can_delete": false,
      "can_grant": false
    }
  ]
}
```

**失败响应：**

```json
{
  "detail": "Permission denied"
}
```

**当前实现说明：**

- 当前要求用户对该知识库拥有 `can_grant`
- 返回的是知识库内用户级可累加权限：`can_view/can_upload/can_delete/can_grant`

**测试覆盖：**

- `backend/tests/test_kb_permissions_api.py::test_grant_user_permissions_then_list_them`
- `backend/tests/test_kb_permissions_api.py::test_permission_interface_requires_can_grant`

---

### PUT `/api/kb/{kb_id}/permissions/{user_id}`

设置指定用户在知识库下的权限。

**请求头：**

```text
Authorization: Bearer <token>
```

**请求体：**

```json
{
  "can_view": true,
  "can_upload": true,
  "can_delete": false,
  "can_grant": false
}
```

**成功响应：**

```json
{
  "user_id": "1001",
  "username": "1001",
  "can_view": true,
  "can_upload": true,
  "can_delete": false,
  "can_grant": false
}
```

**当前实现说明：**

- 当前要求用户对该知识库拥有 `can_grant`
- 权限项支持多选可累加
- 新建知识库后，创建者默认拥有 4 项权限全 `true`

**测试覆盖：**

- `backend/tests/test_kb_permissions_api.py::test_grant_user_permissions_then_list_them`

---

### DELETE `/api/kb/{kb_id}/permissions/{user_id}`

移除指定用户在知识库下的权限记录。

**请求头：**

```text
Authorization: Bearer <token>
```

**成功响应：**

```json
{
  "deleted": true
}
```

**当前实现说明：**

- 当前要求用户对该知识库拥有 `can_grant`
- 删除后该用户将失去该知识库下的全部权限

**测试覆盖：**

- `backend/tests/test_kb_permissions_api.py::test_remove_user_permissions_deletes_permission_record`

---

### GET `/api/kb/{kb_id}/view-rules/{user_id}`

查询指定用户在知识库下的知识视图规则。

**权限要求：** 当前操作人必须拥有该知识库的 `can_grant`。

没有规则时返回：

```json
{
  "kb_id": 1,
  "user_id": 1001,
  "rule": null,
  "effective_scope": "all_documents"
}
```

有规则时返回允许部门、产品线、visibility、最大密级以及 `effective_scope=restricted`。

---

### PUT `/api/kb/{kb_id}/view-rules/{user_id}`

创建或整体覆盖指定用户的知识视图规则。

**权限要求：**

- 当前操作人拥有 `can_grant`；
- 目标用户必须已经拥有该知识库的 `can_view`。

**请求示例：**

```json
{
  "allowed_departments": ["售后", "交付"],
  "allowed_product_lines": ["P368"],
  "allowed_visibilities": ["public", "internal"],
  "max_security_level": 2
}
```

空集合表示对应维度不限制。不同维度之间使用 AND，同一维度多个值使用 OR。

---

### DELETE `/api/kb/{kb_id}/view-rules/{user_id}`

删除指定用户的知识视图规则。删除后，若用户仍拥有 `can_view`，其有效范围恢复为 `all_documents`。

**权限要求：** 当前操作人必须拥有 `can_grant`。

**当前阶段说明：**

- 规则已支持持久化、配置和文档可见性逻辑判断；
- 数据库模式问答已在 `DbChunkLoader` 加载候选 Chunk 前应用有效文档 Filter，规则外来源不会进入向量候选集或 BM25 索引；
- `KnowledgeViewRule` 不能替代知识库 `can_view` 授权；
- 内存模式当前返回 `501 Knowledge view rules require database mode`。

---

### POST `/api/kb/{kb_id}/documents/upload`

上传文档到指定知识库。

**请求类型：** `multipart/form-data`

**路径参数：**

| 参数 | 类型 | 说明 |
|------|------|------|
| `kb_id` | string | 知识库 ID |

**请求头：**

```text
Authorization: Bearer <token>
```

**表单字段：**

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `department` | string | 否 | 文档所属部门，默认空字符串 |
| `product_line` | string | 否 | 文档所属产品线，默认空字符串 |
| `visibility` | string | 否 | 文档可见范围，默认 `internal` |
| `security_level` | integer | 否 | 文档密级，默认 `1` |
| `tags` | string | 否 | 文档标签，当前阶段使用逗号分隔字符串 |
| `scope` | string | 否 | 规范可见范围：`C`（客户）/`I`（内部）/`R`（受限），默认 `I` |
| `document_type` | string | 否 | 规范文档类型编码，默认 `OTH` |
| `product` | string | 否 | 规范产品编码，默认 `GEN` |
| `priority` | string | 否 | 运营优先级：`P0`/`P1`/`P2`，默认 `P2` |
| `file` | binary | 是 | 上传文件 |

**示例：**

```bash
curl -X POST \
  http://localhost:8000/api/kb/{kb_id}/documents/upload \
  -F "file=@P368用户手册.pdf"
```

**响应（内存模式）：**

```json
{
  "id": "uuid",
  "kb_id": "uuid",
  "title": "P368用户手册.pdf",
  "file_type": "pdf",
  "file_size": 5242880,
  "status": "pending"
}
```

**响应（数据库模式）：**

```json
{
  "id": 1,
  "kb_id": 1,
  "title": "P368用户手册.txt",
  "file_type": "txt",
  "status": "pending",
  "parse_task_id": 1,
  "staged_filename": "1-<uuid>-P368用户手册.txt",
  "block_count": 1,
  "chunk_count": 1
}
```

字段说明：

| 字段 | 说明 |
|------|------|
| `status=pending` | 文件已记录；数据库模式会同步创建解析任务 |
| `parse_task_id` | 数据库模式返回，表示 `parse_tasks` 中的新任务 ID |
| `staged_filename` | 数据库模式返回，表示已落盘的临时文件名 |
| `block_count` | 数据库模式返回；当前 `.txt` 可解析为 `content_blocks`，未支持格式返回 0 |
| `chunk_count` | 数据库模式返回；当前基于空行分段写入 `document_chunks`，未支持格式返回 0 |
| `department` | 文档所属部门；当前阶段为后续元数据硬过滤预留 |
| `product_line` | 文档所属产品线；当前阶段为后续元数据硬过滤预留 |
| `visibility` | 文档可见范围；默认 `internal` |
| `security_level` | 文档密级；默认 `1` |
| `tags` | 文档标签；当前阶段使用逗号分隔字符串 |
| `scope` | 统一权限范围编码：`C`/`I`/`R`；数据库检索时参与硬过滤 |
| `document_type` | 统一文档类型编码；参与检索策略重排 |
| `product` | 统一产品编码；默认参与产品匹配重排，存在产品授权范围时参与硬过滤 |
| `priority` | 运营优先级；参与检索策略重排 |
| `file_type` | 从文件名后缀提取 |
| `file_size` | 原始文件字节数 |
| `storage_key` | 仅数据库模式返回的内部相对存储键；不含服务器绝对路径 |
| `original_filename` | 原始上传文件名 |
| `content_type` | 上传时记录的 MIME 类型 |
| `download_available` | 原始文件当前是否存在且可下载 |

**当前实现说明：**

- 当前默认 `create_app()` 仍使用 `InMemoryDocumentService`
- 通过 `create_app(mode="database", session=...)` 可切换到数据库版上传链路
- 上传接口当前要求用户对该知识库拥有 `can_upload`
- 数据库版 `DbDocumentService` 已支持：写入 `documents` 元数据、按知识库查询文档、按知识库/文档 ID 读取单文档详情、同步更新 `KnowledgeBase.doc_count`
- 数据库模式上传会调用 `IngestionService.ingest_uploaded_document()`：文件落盘到 `app.state.upload_root`、创建 `parse_tasks`、对 `.txt` 文件写入 `content_blocks` 和 `document_chunks`
- 当前 `.txt` chunk 规则为按空行分段；后续会替换为面向 token 长度与语义边界的 splitter
- 当前解析器已接入 Unstructured adapter：`.docx` / `.pdf` 会优先调用 `unstructured.partition.auto.partition()`，失败时回退到基础解析器
- 当前基础 fallback 支持 `.txt`、基础 `.docx` 段落文本、基础 `.pdf` 文本流；复杂 PDF、扫描件、复杂表格和图片知识仍需 OCR/VLM/表格专项解析增强
- 当前仍未接 Celery、Milvus、真实生产 LLM；`SimpleLLM` 仅用于本地骨架联调

**测试覆盖：**

- `backend/tests/test_document_api.py::test_upload_document_records_pending_status_and_increments_doc_count`
- `backend/tests/test_db_document_service.py`
- `backend/tests/test_service_provider.py::test_build_app_state_services_can_switch_to_database_services`
- `backend/tests/test_database_mode_api.py::test_database_mode_upload_stages_file_and_returns_parse_task`
- `backend/tests/test_upload_ingest_flow.py`

---

### GET `/api/kb/{kb_id}/documents`

列出指定知识库下的文档。

**请求头：**

```text
Authorization: Bearer <token>
```

**响应（内存模式）：**

```json
{
  "items": [
    {
      "id": "uuid",
      "kb_id": "uuid",
      "title": "faq.pdf",
      "file_type": "pdf",
      "file_size": 3,
      "status": "pending"
    }
  ]
}
```

**响应（数据库模式）：**

```json
{
  "items": [
    {
      "id": 1,
      "kb_id": 1,
      "title": "faq.txt",
      "file_type": "txt",
      "status": "pending"
    }
  ]
}
```

**当前实现说明：**

- 当前要求用户对该知识库拥有 `can_view`
- 数据库模式还会将当前用户的有效文档 Filter 应用于列表：规则外文档不返回，避免泄露标题、ID 或元数据
- 文档详情采用相同 Filter；文档存在但不符合当前知识视图时返回 `403 Permission denied`
- 内存模式直接返回上传时记录的文档元数据
- 数据库模式从 `documents` 表读取文档列表，并返回 `original_filename`、`content_type`、`file_size` 与实时 `download_available`
- 历史文档的 `storage_key` 为空时仍可查看元数据，但 `download_available=false`
- 当前未实现分页、状态筛选和上传人筛选

**测试覆盖：**

- `backend/tests/test_document_api.py::test_list_documents_returns_uploaded_document`

---

### GET `/api/kb/{kb_id}/documents/{doc_id}`

获取指定知识库下的单文档详情与入库状态。

**路径参数：**

| 参数 | 类型 | 说明 |
|------|------|------|
| `kb_id` | string | 知识库 ID |
| `doc_id` | string | 文档 ID |

**请求头：**

```text
Authorization: Bearer <token>
```

**成功响应（数据库模式示例）：**

```json
{
  "id": 1,
  "kb_id": 1,
  "title": "manual.txt",
  "status": "pending",
  "file_type": "txt",
  "block_count": 1,
  "chunk_count": 1
}
```

**成功响应（内存模式示例）：**

```json
{
  "id": "uuid",
  "kb_id": "uuid",
  "title": "manual.txt",
  "status": "pending",
  "file_type": "txt",
  "block_count": 0,
  "chunk_count": 0
}
```

**失败响应：**

```json
{
  "detail": "Document not found"
}
```

**当前实现说明：**

- 当前要求用户对该知识库拥有 `can_view`
- 数据库模式会按 `kb_id + doc_id` 查询 `documents`
- `block_count` 来自该文档最近一次 `parse_task` 关联的 `content_blocks` 数量
- `chunk_count` 来自 `document_chunks` 中该文档的 chunk 数量
- 当前详情接口已返回文档元数据字段：`department`、`product_line`、`visibility`、`security_level`、`tags`、`scope`、`document_type`、`product`、`priority`、`original_filename`、`content_type`、`file_size`、`download_available`
- `scope`、部门、产品、密级和角色 ACL 在数据库问答链路中属于检索前硬过滤；`document_type`、问题产品匹配、`priority` 属于候选集内的策略重排
- 文档元数据是向量检索、后续图谱检索、后台与统计共享的唯一权限事实来源（SSOT）
- 内存模式当前仅返回基础文档元数据，`block_count` / `chunk_count` 固定为 0
- 若文档不存在，或文档属于其他知识库，都会返回 `404 Document not found`
- 若用户无查看权限，则返回 `403 Permission denied`

**测试覆盖：**

- `backend/tests/test_document_detail_api.py::test_document_detail_returns_parse_and_chunk_counts`
- `backend/tests/test_document_detail_api.py::test_document_detail_returns_404_for_missing_document`
- `backend/tests/test_document_detail_api.py::test_document_detail_returns_404_when_document_belongs_to_other_kb`

---

### GET `/api/kb/{kb_id}/documents/{doc_id}/download`

下载数据库模式中已保存的原始文件。

**请求头：** `Authorization: Bearer <token>`

**成功响应：** `200` 附件流，使用上传时的 `content_type` 与 `original_filename`，并设置 `Content-Disposition: attachment`。服务端不会暴露绝对存储路径。

**授权与审计：**

- 需要知识库 `can_view`，并与列表、详情共用 V2 `EffectiveDocumentFilter`；规则外文档返回 `403 Permission denied`。
- `can_grant` 用户可绕过用户级知识视图规则。
- 仅当授权和文件存在检查均成功时，写入 `audit_log`：`action=download_document`、目标文档、知识库及文件名。
- 历史文档无 `storage_key`，或原始文件已不存在时返回 `404 File not found`。
- 内存模式不提供伪下载，返回 `404 File not found`。

**测试覆盖：** `backend/tests/test_document_download_api.py`

---

## 5. 检索策略 API

### GET `/api/retrieval-policy`

读取当前生效的只读检索策略，要求 Bearer 登录态；不提供在线写入。策略来自 `backend/config/retrieval_policy.yaml`，每次检索会重新读取，因此调整权重不需要重新上传或解析文档。

**响应：**

```json
{
  "type_weight": {"WP": 1.0, "OTH": 0.3},
  "product_weight": {"MC": 1.0, "GEN": 0.8},
  "priority_boost": {"P0": 1.2, "P2": 0.8},
  "formula": {
    "similarity_ratio": 0.75,
    "type_ratio": 0.10,
    "product_ratio": 0.10,
    "priority_ratio": 0.05
  },
  "top_k": {"initial": 100, "after_rerank": 20, "final": 10}
}
```

---

## 6. 问答 API

### POST `/api/qa/ask/sync`

同步问答接口。当前用于 MVP 骨架测试，后续会增加流式接口 `/api/qa/ask`。

**请求体：**

```json
{
  "question": "SOS 报警怎么关闭",
  "kb_id": "kb-1",
  "conversation_id": null
}
```

字段说明：

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `question` | string | 是 | 用户问题 |
| `kb_id` | string | 是 | 查询的知识库 ID |
| `conversation_id` | string/null | 否 | 会话 ID，后续用于续接上下文 |

**响应：**

```json
{
  "answer": "SOS 报警可以在设置中关闭。",
  "sources": [
    {
      "chunk_id": "c1",
      "content": "SOS 报警可以在设置中的报警设置里关闭。",
      "score": 0.9,
      "doc_title": "P368用户手册"
    }
  ]
}
```

**当前实现说明：**

- 当前使用 `SimpleLLM` 模拟 LLM tool-use 行为
- 当前使用 `RAGService` + `KnowledgeTools` 完成单引擎工具调用流程
- 内存模式检索数据来自 `app.state.rag_service.tools.vector_chunks_by_kb`
- 数据库模式下，`/api/qa/ask/sync` 会通过 `DbChunkLoader` 读取当前知识库的 `document_chunks`，转换为临时检索 chunk 后交给 `KnowledgeTools.retrieve()`
- 数据库模式问答前会同步调用 `KnowledgeTools.build_bm25_index()`，让 `bm25_search` 工具也能基于已上传文档工作
- `RAGService` 当前会在 `retrieve` 后同步补跑 `bm25_search`，合并并去重 sources，形成本地混合检索骨架
- 后续应接入真实 LLM、Milvus、BM25 索引和会话存储；数据库 chunk 直连检索只是 Milvus 前的本地闭环

**测试覆盖：**

- `backend/tests/test_api.py::test_ask_sync_returns_answer_and_sources`
- `backend/tests/test_rag_service.py::test_rag_service_uses_tool_results_for_answer_sources`
- `backend/tests/test_database_mode_api.py::test_database_mode_qa_uses_uploaded_document_chunks`
- `backend/tests/test_db_chunk_loader.py`
- `backend/tests/test_qa_ops_api.py::test_ask_sync_persists_conversation_and_message_in_database_mode`
- `backend/tests/test_qa_ops_api.py::test_ask_sync_appends_message_to_existing_conversation`

---

### GET `/api/qa/conversations?kb_id={kb_id}`

返回当前用户在指定知识库下的问答会话列表。

**请求头：**

```text
Authorization: Bearer <token>
```

**权限要求：** 当前用户必须拥有该知识库的 `can_view`。

**响应：**

```json
{
  "items": [
    {
      "id": 1,
      "kb_id": 1,
      "title": "SOS 报警怎么关闭",
      "created_at": "2026-07-08T10:00:00",
      "updated_at": "2026-07-08T10:05:00"
    }
  ]
}
```

**当前实现说明：**

- 数据库模式下由 `QaOpsService` 查询 `conversations` 表。
- 只返回当前用户自己的会话。
- 内存模式返回空列表。

**测试覆盖：**

- `backend/tests/test_qa_ops_api.py::test_ask_sync_persists_conversation_and_message_in_database_mode`

---

### GET `/api/qa/conversations/{conversation_id}/messages`

返回当前用户某个会话下的问答消息列表。

**请求头：**

```text
Authorization: Bearer <token>
```

**权限要求：** 当前用户必须是会话所属用户，且仍拥有该知识库的 `can_view`。

**响应：**

```json
{
  "items": [
    {
      "id": 12,
      "question": "SOS 报警怎么关闭",
      "answer": "SOS 报警可以在设置中关闭。",
      "sources": [],
      "created_at": "2026-07-08T10:00:00"
    }
  ]
}
```

**测试覆盖：**

- `backend/tests/test_qa_ops_api.py::test_ask_sync_appends_message_to_existing_conversation`

---

### POST `/api/qa/feedback`

保存用户对答案的反馈。

**请求头：**

```text
Authorization: Bearer <token>
```

**请求体：**

```json
{
  "message_id": 12,
  "is_helpful": false,
  "feedback_text": "回答没有说清楚具体菜单路径"
}
```

**响应：**

```json
{
  "saved": true,
  "issue_id": 5
}
```

当 `is_helpful=true` 时，`issue_id` 为 `null`。

**当前实现说明：**

- 当前用户必须是该消息的提问者。
- 当前用户必须拥有消息所属知识库的 `can_view`。
- 正反馈只保存反馈。
- 负反馈会创建或更新一条 `knowledge_issues`，状态为 `open`，原因 `reason=negative_feedback`。

**测试覆盖：**

- `backend/tests/test_qa_ops_api.py::test_positive_feedback_does_not_create_issue`
- `backend/tests/test_qa_ops_api.py::test_negative_feedback_creates_open_issue`
- `backend/tests/test_qa_ops_api.py::test_feedback_requires_message_owner`

---

## 6. 启动平台空端点 API

这些端点来自 `docs/design/2025-06-23-kg-platform-bootstrap.md` 的“所有 API 端点先打通”原则。当前为内存空状态实现，后续接 PostgreSQL 表。

### GET `/api/review`

审核队列列表。

**响应：**

```json
{
  "items": []
}
```

**当前实现说明：** 当前读取 `app.state.review_queue`。

**测试覆盖：** `backend/tests/test_bootstrap_endpoints.py::test_review_endpoint_returns_empty_items_initially`

---

### GET `/api/conflicts`

冲突列表。

**响应：**

```json
{
  "items": []
}
```

**当前实现说明：** 当前读取 `app.state.conflict_log`。

**测试覆盖：** `backend/tests/test_bootstrap_endpoints.py::test_conflicts_endpoint_returns_empty_items_initially`

---

### GET `/api/dashboard/summary`

运营看板概要统计。

**响应：**

```json
{
  "pending_review": 0,
  "pending_conflicts": 0,
  "pending_tasks": 0,
  "open_issues": 0
}
```

**当前实现说明：** 当前基于内存队列计数，后续接 `review_queue`、`conflict_log`、`parse_tasks`、`knowledge_issues` 表。

**测试覆盖：** `backend/tests/test_bootstrap_endpoints.py::test_dashboard_summary_returns_zero_counts_initially`

---

### GET `/api/issues?kb_id={kb_id}&status=open`

知识问题闭环列表。数据库模式下返回指定知识库的知识缺口，内存模式下仍返回当前内存空状态。

**请求头（数据库模式）：**

```text
Authorization: Bearer <token>
```

**权限要求：** 当前用户必须拥有该知识库的 `can_grant`。

**响应：**

```json
{
  "items": [
    {
      "id": 5,
      "kb_id": 1,
      "message_id": 12,
      "question": "SOS 报警怎么关闭",
      "reason": "negative_feedback",
      "feedback_text": "回答没有说清楚具体菜单路径",
      "status": "open",
      "created_at": "2026-07-08T10:00:00"
    }
  ]
}
```

**当前实现说明：** 数据库模式由 `QaOpsService.list_issues()` 读取 `knowledge_issues`；内存模式读取 `app.state.knowledge_issues`。

**测试覆盖：**

- `backend/tests/test_bootstrap_endpoints.py::test_issues_endpoint_returns_empty_items_initially`
- `backend/tests/test_qa_ops_api.py::test_admin_can_list_open_issues_and_resolve_one`
- `backend/tests/test_qa_ops_api.py::test_user_without_can_grant_cannot_list_issues`

---

### PUT `/api/issues/{issue_id}`

更新知识缺口状态。

**请求头：**

```text
Authorization: Bearer <token>
```

**请求体：**

```json
{
  "status": "resolved"
}
```

允许状态：

- `open`
- `resolved`
- `ignored`

**响应：**

```json
{
  "id": 5,
  "status": "resolved"
}
```

**权限要求：** 当前用户必须拥有 issue 所属知识库的 `can_grant`。

**测试覆盖：**

- `backend/tests/test_qa_ops_api.py::test_admin_can_list_open_issues_and_resolve_one`
- `backend/tests/test_qa_ops_api.py::test_admin_can_ignore_issue`

---

## 7. 内部工具函数（非 HTTP API）

### `KnowledgeTools.retrieve(query, kb_id, top_k=30)`

语义检索工具，后续对接 Milvus + BGE/M3E。

当前行为：
- 从内存 `vector_chunks_by_kb` 获取片段
- 根据 query 与 content 的 token overlap 计算分数
- 英文占比 >30% 时 `select_embedding_model()` 返回 `BAAI/m3e-base`
- 否则返回 `BAAI/bge-large-zh-v1.5`

测试覆盖：
- `backend/tests/test_language_selection.py`

### `KnowledgeTools.bm25_search(query, kb_id, top_k=20)`

关键词精确检索工具，当前实现轻量 BM25 评分。

测试覆盖：
- `backend/tests/test_tools.py::test_bm25_search_returns_exact_model_match_first`

### `RuleReranker.rerank(question, chunks, top_k=10)`

一期规则精排工具。

当前评分因素：
- 原始 score
- query/content 词重叠
- query/heading 词重叠
- chunk_index 位置加权

测试覆盖：
- `backend/tests/test_reranker.py::test_reranker_prefers_chunks_with_query_terms_in_content_and_heading`

---

## 8. 已实现的认证与 CRUD 增量

### POST `/api/auth/login`

最小登录接口，当前仅接受默认账号：`admin / admin`。

**请求体：**

```json
{
  "username": "admin",
  "password": "admin"
}
```

**成功响应：**

```json
{
  "token": "session-token"
}
```

**失败响应：**

```json
{
  "detail": "Invalid credentials"
}
```

**测试覆盖：**

- `backend/tests/test_auth_api.py::test_login_returns_session_token_for_default_admin`

---

### GET `/api/auth/me`

获取当前会话对应的用户信息。

**请求头：**

```text
Authorization: Bearer <token>
```

**成功响应：**

```json
{
  "user_id": "admin",
  "username": "admin"
}
```

**失败响应：**

```json
{
  "detail": "Missing authorization header"
}
```

或：

```json
{
  "detail": "Invalid authorization header"
}
```

或：

```json
{
  "detail": "Invalid session token"
}
```

**测试覆盖：**

- `backend/tests/test_auth_api.py::test_login_then_me_returns_default_admin_profile`
- `backend/tests/test_auth_api.py::test_me_requires_valid_session_token`
- `backend/tests/test_auth_api.py::test_me_rejects_invalid_session_token`
- `backend/tests/test_auth_api.py::test_me_rejects_invalid_authorization_header_format`

---

## 9. 前端页面入口

### GET `/login`

返回最小登录页。

**当前实现说明：**

- 页面文件：`backend/app/static/login.html`
- 通过 `backend/app/main.py::login_page()` 读取 HTML 并返回
- 页面加载 `/static/admin.js`，提交表单后调用 `POST /api/auth/login`，成功后将 token 写入 `localStorage` 并跳转 `/admin`

**测试覆盖：**

- `backend/tests/test_frontend_shell.py::test_login_page_is_served`

---

### GET `/admin`

返回最小知识库管理台壳。

**当前实现说明：**

- 页面文件：`backend/app/static/admin.html`
- 通过 `backend/app/main.py::admin_page()` 读取 HTML 并返回
- 当前页面已包含：
  - 知识库区 `#kb-list`
  - 文档上传区 `#document-upload`
  - 文档列表区 `#document-list`
  - 文档详情区 `#document-detail`
  - 权限列表区 `#permission-list`
  - 权限编辑区 `#permission-editor`
  - 删除确认区 `#delete-panel`
  - 页面消息区 `#admin-message`
- 页面加载 `/static/admin.js`，当前前端已支持：
  - 加载当前用户信息与可见知识库列表
  - 切换知识库
  - 保存知识库权限
  - 单文件上传文档
  - 上传成功后自动刷新列表并加载新文档详情
  - 点击文档查看详情
  - 删除当前选中文档后刷新列表并清空详情区
  - 删除知识库后刷新知识库列表

**测试覆盖：**

- `backend/tests/test_frontend_shell.py::test_admin_shell_is_served`
- `backend/tests/test_frontend_shell.py::test_admin_shell_contains_kb_document_permission_and_delete_regions`
- `backend/tests/test_frontend_shell.py::test_admin_shell_contains_permission_editor_and_feedback_regions`
- `backend/tests/test_frontend_shell.py::test_admin_shell_contains_document_and_kb_action_controls`
- `backend/tests/test_frontend_shell.py::test_admin_shell_contains_workspace_message_and_kb_delete_button`
- `backend/tests/test_frontend_shell.py::test_admin_shell_contains_upload_and_document_detail_regions`
- `backend/tests/test_frontend_shell.py::test_admin_shell_contains_upload_feedback_and_selection_state_hooks`
- `backend/tests/test_frontend_shell.py::test_admin_shell_contains_document_detail_fields_and_delete_feedback`

---

### GET `/qa`

返回知识查询用户问答页面。

**当前实现说明：**

- 页面文件：`backend/app/static/qa.html`
- 交互脚本：`backend/app/static/qa.js`
- 通过 `backend/app/main.py::qa_page()` 读取 HTML 并返回
- 页面加载后会：
  - 读取 `localStorage.session_token`
  - 调用 `/api/auth/me` 加载当前用户
  - 调用 `/api/kb` 加载当前用户可见知识库
  - 调用 `/api/qa/conversations?kb_id=...` 加载当前知识库下的历史会话
  - 点击历史会话时调用 `/api/qa/conversations/{conversation_id}/messages` 展示历史问答
  - 调用 `/api/qa/ask/sync` 提交问题
  - 展示答案和来源
  - 调用 `/api/qa/feedback` 提交有用 / 无用反馈
  - 在历史会话中继续提问时复用当前 `conversation_id`

**测试覆盖：**

- `backend/tests/test_frontend_shell.py::test_qa_page_is_served`
- `backend/tests/test_frontend_shell.py::test_qa_page_contains_minimal_query_regions`
- `backend/tests/test_frontend_shell.py::test_qa_static_js_contains_auth_and_kb_loading_hooks`
- `backend/tests/test_frontend_shell.py::test_qa_static_js_contains_question_submission_hooks`
- `backend/tests/test_frontend_shell.py::test_qa_static_js_contains_feedback_hooks`

---

### GET `/static/{path}`

静态资源入口，用于托管 `backend/app/static` 下的前端资源。

---

## 10. 后续 API 待实现清单

| API | 阶段 | 说明 |
|-----|------|------|
| `GET /api/kb/{kb_id}/permissions` | P0 | 查看知识库权限 |
| `PUT /api/kb/{kb_id}/permissions` | P0 | 设置知识库权限 |
| `DELETE /api/kb/{kb_id}/documents/{doc_id}` | P0 | 删除文档 |
| `GET /api/kb/{kb_id}/search` | P0 | 知识库搜索 |
| `POST /api/qa/ask/sync` | P0 | 同步问答；数据库模式下会写入会话和消息记录 |
| `GET /api/qa/conversations` | P0 | 当前用户的问答会话历史 |
| `GET /api/qa/conversations/{conversation_id}/messages` | P0 | 当前用户某个会话下的问答消息 |
| `POST /api/qa/feedback` | P1 | 答案评价；负反馈会生成知识缺口 |
| `POST /api/qa/human-transfer` | P1 | 转人工 |

---

## 9. 维护规则

1. 新增接口必须同步新增测试。
2. 修改请求/响应字段必须同步更新本文档和测试。
3. 删除接口必须说明迁移路径。
4. API 文档中的示例必须能与测试中的行为对应。
5. 每次实现一个模块后，应在本文档记录：路径、请求、响应、状态码、当前实现限制、后续替换点。
