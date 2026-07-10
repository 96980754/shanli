# AI 知识库系统 — 技术设计文档

> **版本：** v1.0  
> **适用阶段：** 一期（MVP，P0）— 二期/三期标注在对应模块  
> **最后更新：** 2026-06-22

---

## 目录

1. [项目结构](#1-项目结构)
2. [系统架构](#2-系统架构)
3. [数据模型设计](#3-数据模型设计)
4. [API 设计](#4-api-设计)
5. [文档处理管道](#5-文档处理管道)
6. [检索问答管道](#6-检索问答管道)
7. [前端设计](#7-前端设计)
8. [部署架构](#8-部署架构)

---

## 1. 项目结构

```
zhishiku/
├── backend/
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py                         # FastAPI 入口
│   │   ├── config.py                       # 全局配置（LLM/DB/向量库参数）
│   │   ├── dependencies.py                 # 依赖注入（DB session、当前用户）
│   │   │
│   │   ├── api/                            # 路由层
│   │   │   ├── __init__.py
│   │   │   ├── kb.py                       # 知识库 CRUD
│   │   │   ├── document.py                 # 文档上传/管理
│   │   │   ├── qa.py                       # 问答接口
│   │   │   ├── search.py                   # 知识库搜索
│   │   │   ├── auth.py                     # 登录/认证
│   │   │   └── admin.py                    # 管理后台接口
│   │   │
│   │   ├── models/                         # SQLAlchemy ORM 模型
│   │   │   ├── __init__.py
│   │   │   ├── user.py                     # User, Role
│   │   │   ├── knowledge_base.py           # KnowledgeBase
│   │   │   ├── document.py                 # Document, DocumentChunk
│   │   │   ├── conversation.py             # Conversation, Message
│   │   │   └── feedback.py                 # Feedback
│   │   │
│   │   ├── schemas/                        # Pydantic 请求/响应模型
│   │   │   ├── __init__.py
│   │   │   ├── kb.py
│   │   │   ├── document.py
│   │   │   ├── qa.py
│   │   │   └── user.py
│   │   │
│   │   ├── services/                       # 业务逻辑层
│   │   │   ├── __init__.py
│   │   │   ├── kb_service.py              # 知识库管理逻辑
│   │   │   ├── document_service.py        # 文档存储/版本管理
│   │   │   ├── ingestion_service.py       # 文档解析 + 分块 + 向量化编排
│   │   │   ├── rag_service.py             # 检索 + 重排序 + 生成入口
│   │   │   ├── retriever.py               # 多路召回（向量+稀疏）
│   │   │   ├── reranker.py                # 重排序
│   │   │   └── llm_service.py            # LLM 调用封装
│   │   │
│   │   ├── core/                           # 基础设施
│   │   │   ├── __init__.py
│   │   │   ├── security.py                # JWT/密码哈希
│   │   │   ├── db.py                      # PostgreSQL 会话
│   │   │   ├── milvus.py                  # Milvus 客户端封装
│   │   │   ├── neo4j_client.py            # Neo4j 客户端（二期）
│   │   │   └── redis.py                   # Redis 客户端
│   │   │
│   │   └── utils/
│   │       ├── __init__.py
│   │       ├── text_splitter.py           # 文本分块策略
│   │       └── file_parser.py             # 文件类型检测
│   │
│   ├── worker/                             # Celery 异步任务
│   │   ├── __init__.py
│   │   ├── celery_app.py                  # Celery 实例
│   │   ├── tasks/
│   │   │   ├── __init__.py
│   │   │   ├── ingest.py                  # 文档入库任务
│   │   │   └── maintenance.py             # 定时维护任务（三期）
│   │   └── parsers/                        # 文档解析器
│   │       ├── __init__.py
│   │       ├── base.py                    # 解析器抽象基类
│   │       ├── unstructured_parser.py     # Unstructured.io 封装
│   │       ├── table_parser.py            # 表格解析（二期多模态LLM）
│   │       └── image_parser.py            # 图片解析（二期）
│   │
│   ├── tests/
│   │   ├── conftest.py
│   │   ├── test_retriever.py
│   │   ├── test_rag_service.py
│   │   ├── test_ingestion.py
│   │   └── test_api/
│   │
│   ├── requirements.txt
│   ├── Dockerfile
│   └── alembic/                            # 数据库迁移
│
├── frontend/
│   ├── src/
│   │   ├── App.vue
│   │   ├── main.ts
│   │   ├── api/                           # API 请求封装
│   │   │   ├── client.ts                  # axios 实例
│   │   │   ├── qa.ts                      # 问答接口
│   │   │   ├── kb.ts                      # 知识库接口
│   │   │   └── auth.ts                    # 认证接口
│   │   ├── views/
│   │   │   ├── LoginView.vue
│   │   │   ├── ChatView.vue              # 问答对话框（Web）
│   │   │   ├── KbBrowseView.vue          # 知识库浏览
│   │   │   ├── KbDetailView.vue          # 知识库详情/文档列表
│   │   │   ├── AdminUsersView.vue        # 用户管理
│   │   │   ├── AdminKbView.vue           # 知识库配置管理
│   │   │   └── AdminStatsView.vue        # 数据统计（P1）
│   │   ├── components/
│   │   │   ├── ChatMessage.vue           # 单条消息组件
│   │   │   ├── SourceCard.vue            # 来源引用卡片
│   │   │   ├── KbCard.vue               # 知识库卡片
│   │   │   ├── DocumentList.vue          # 文档列表
│   │   │   └── FeedbackButtons.vue       # 评价按钮
│   │   ├── router/
│   │   │   └── index.ts
│   │   ├── store/
│   │   │   ├── user.ts
│   │   │   └── chat.ts
│   │   └── styles/
│   ├── package.json
│   ├── vite.config.ts
│   └── Dockerfile
│
├── docker-compose.yml                     # Milvus + Redis + PostgreSQL + Neo4j(预留)
├── .env.example
└── README.md
```

---

## 2. 系统架构

### 2.1 分层职责

| 层 | 职责 | 关键技术 | 一期/二期 |
|----|------|---------|----------|
| **API 层** | 接收请求、参数校验、权限校验、响应 | FastAPI + Pydantic | P0 |
| **Service 层** | 业务编排、多步操作协调 | Python | P0 |
| **Retrieval 层** | 多路召回 + 重排序 | Milvus + rank_bm25 + BGE | P0/P1 |
| **KG 层** | 实体关系存储、图检索、图推理 | Neo4j + LightRAG | P1 |
| **LLM 层** | 答案生成、实体抽取、意图识别 | 通义千问 / GPT-4 / Claude | P0 |
| **Ingestion 层** | 文档解析、分块、向量化入库 | Unstructured.io + Celery | P0 |
| **存储层** | 结构化数据 + 向量 + 缓存 + 文件 | PostgreSQL + Milvus + Redis + 对象存储 | P0 |

### 2.2 核心数据流

#### 文档入库流

```
用户上传文档 → API (/api/documents/upload)
  → 存入文件存储
  → 发送 Celery 任务 (ingest_document)
  → Unstructured.io 解析（标题/段落/表格）
  → 文本分块 (chunk_size=512, overlap=64)
  → BGE-Large-ZH 向量化
  → 写入 Milvus collection
  → 写入 PostgreSQL (Document + DocumentChunk 记录)
  → 更新知识库文档计数
  → WebSocket 通知前端处理完成
```

#### 问答流

```
用户提问 → API (/api/qa/ask)
  → 构造 tool-use request（LLM 一次调用，带工具定义）
  → LLM 自动决定调用哪个/哪些工具：
      ├─ retrieve(query, kb_id)         → Milvus 语义检索 Top-30     <100ms
      ├─ bm25_search(query, kb_id)      → BM25 精确匹配 Top-20       <50ms
      ├─ graph_search(entities)         → Neo4j 图检索（二期）       <200ms
      └─ rerank(results)                → 规则精排 Top-10            <10ms
  → 工具以纯函数执行（无LLM参与，<50ms），结果返回 LLM 上下文
  → LLM 单次流式生成答案 + 溯源
  → 流式返回给前端（SSE / WebSocket）
  → 会话记录写入 PostgreSQL
                                                首 token ≤ 1.5s ✅
```

---

## 3. 数据模型设计

### 3.1 PostgreSQL 模型

#### User / Role（用户与角色）

```sql
CREATE TABLE users (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    username      VARCHAR(64) NOT NULL UNIQUE,
    password_hash VARCHAR(256) NOT NULL,
    display_name  VARCHAR(128),
    email         VARCHAR(256),
    is_active     BOOLEAN DEFAULT TRUE,
    role          VARCHAR(16) NOT NULL DEFAULT 'viewer',  -- admin / kb_admin / viewer
    department    VARCHAR(128),                            -- 部门
    created_at    TIMESTAMP DEFAULT NOW(),
    updated_at    TIMESTAMP DEFAULT NOW()
);

CREATE TABLE kb_permissions (  -- 知识库-用户权限关联
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    kb_id         UUID NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
    user_id       UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    permission    VARCHAR(16) NOT NULL DEFAULT 'read',    -- read / write
    UNIQUE(kb_id, user_id)
);
```

#### KnowledgeBase（知识库）

```sql
CREATE TABLE knowledge_bases (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name        VARCHAR(128) NOT NULL,
    description TEXT,
    owner_id    UUID NOT NULL REFERENCES users(id),
    visibility  VARCHAR(16) NOT NULL DEFAULT 'department',  -- all / department / project / personal
    doc_count   INTEGER DEFAULT 0,                          -- 文档数（冗余，避免频繁 COUNT）
    is_active   BOOLEAN DEFAULT TRUE,
    created_at  TIMESTAMP DEFAULT NOW(),
    updated_at  TIMESTAMP DEFAULT NOW()
);
```

#### Document / DocumentChunk（文档与分片）

```sql
CREATE TABLE documents (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    kb_id           UUID NOT NULL REFERENCES knowledge_bases(id) ON DELETE CASCADE,
    title           VARCHAR(512),
    file_type       VARCHAR(16) NOT NULL,     -- pdf / docx / xlsx / pptx / image / video
    file_path       VARCHAR(1024),            -- 存储路径
    file_size       BIGINT,
    page_count      INTEGER,
    doc_hash        VARCHAR(64),              -- 文件 hash（去重用）
    status          VARCHAR(16) DEFAULT 'pending',  -- pending / processing / ready / failed
    chunk_count     INTEGER DEFAULT 0,
    uploaded_by     UUID REFERENCES users(id),
    uploaded_at     TIMESTAMP DEFAULT NOW(),
    updated_at      TIMESTAMP DEFAULT NOW()
);

CREATE TABLE document_chunks (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    doc_id          UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    chunk_index     INTEGER NOT NULL,
    content         TEXT NOT NULL,
    heading         VARCHAR(256),              -- 所属标题（用于溯源）
    page_number     INTEGER,
    token_count     INTEGER,
    milvus_id       VARCHAR(64),               -- 对应 Milvus 中的 ID
    created_at      TIMESTAMP DEFAULT NOW()
);
```

#### Conversation / Message（问答记录）

```sql
CREATE TABLE conversations (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL REFERENCES users(id),
    kb_id       UUID REFERENCES knowledge_bases(id),  -- 可选：限定知识库
    title       VARCHAR(256),                         -- 自动从首条消息生成
    message_count INTEGER DEFAULT 0,
    created_at  TIMESTAMP DEFAULT NOW(),
    updated_at  TIMESTAMP DEFAULT NOW()
);

CREATE TABLE messages (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role            VARCHAR(16) NOT NULL,  -- user / assistant / system
    content         TEXT NOT NULL,
    sources         JSONB,                 -- [{chunk_id, doc_id, title, score, url}]
    feedback        VARCHAR(16),           -- helpful / unhelpful / null
    feedback_reason TEXT,
    token_count     INTEGER,
    latency_ms      INTEGER,
    created_at      TIMESTAMP DEFAULT NOW()
);
```

### 3.2 Milvus Collection 设计

```python
collection_name = "knowledge_chunks"

schema = {
    "fields": [
        {"name": "id",         "type": DataType.VARCHAR, "max_length": 64, "is_primary": True},
        {"name": "chunk_id",   "type": DataType.VARCHAR, "max_length": 64},   # PostgreSQL chunk.id
        {"name": "doc_id",     "type": DataType.VARCHAR, "max_length": 64},   # 关联文档
        {"name": "kb_id",      "type": DataType.VARCHAR, "max_length": 64},   # 知识库过滤
        {"name": "embedding",  "type": DataType.FLOAT_VECTOR, "dim": 1024},   # BGE-Large-ZH 1024维
        {"name": "page_number","type": DataType.INT32},
    ],
    "index": {
        "field_name": "embedding",
        "index_type": "IVF_FLAT",
        "metric_type": "IP",         # 内积，配合 BGE 的归一化向量等价于余弦
        "params": {"nlist": 1024}
    }
}
```

**说明**：一期用 IVF_FLAT 索引，1024 nlist；当文档量超过 10 万片段时切换 IVF_SQ8 或 HNSW。

### 3.3 Neo4j Schema（二期启用，LightRAG 自动管理）

```
(:Product {id, name, model, brand, ...})
(:Feature {id, name, category, ...})
(:Parameter {id, name, value, unit, ...})
(:Document {id, title, version, updated_at, ...})
(:Solution {id, name, industry, ...})
(:Scenario {id, name, ...})

(:Product)-[:has_feature {confidence}]->(:Feature)
(:Product)-[:has_parameter {confidence}]->(:Parameter)
(:Product)-[:belongs_to]->(:Solution)
(:Document)-[:describes]->(:Product)
(:Document)-[:references]->(:Solution)
(:Solution)-[:applies_to]->(:Scenario)
(:Feature)-[:depends_on]->(:Feature)
(:Product)-[:alternative_to {type}]->(:Product)  -- 替代型号（三期）
```

---

## 4. API 设计

### 4.1 认证

| 方法 | 路径 | 描述 | 阶段 |
|------|------|------|------|
| POST | `/api/auth/login` | 用户名密码登录，返回 JWT | P0 |
| POST | `/api/auth/refresh` | 刷新 token | P0 |
| GET  | `/api/auth/me` | 获取当前用户信息 | P0 |

**请求/响应示例**：

```json
POST /api/auth/login
Request:  {"username": "admin", "password": "xxx"}
Response: {"access_token": "eyJ...", "token_type": "bearer", "expires_in": 86400}
```

### 4.2 知识库管理

| 方法 | 路径 | 描述 | 阶段 |
|------|------|------|------|
| GET    | `/api/kb` | 列出用户可见的知识库 | P0 |
| POST   | `/api/kb` | 新建知识库 | P0 |
| GET    | `/api/kb/{kb_id}` | 知识库详情 | P0 |
| PUT    | `/api/kb/{kb_id}` | 更新知识库 | P0 |
| DELETE | `/api/kb/{kb_id}` | 删除知识库 | P0 |
| GET    | `/api/kb/{kb_id}/permissions` | 查看权限 | P0 |
| PUT    | `/api/kb/{kb_id}/permissions` | 设置权限 | P0 |

```json
POST /api/kb
Request: {
  "name": "XX产品线知识库",
  "description": "包含XX系列所有产品文档",
  "visibility": "department"
}
Response: {"id": "uuid", "name": "...", "doc_count": 0, "created_at": "..."}
```

### 4.3 文档管理

| 方法 | 路径 | 描述 | 阶段 |
|------|------|------|------|
| POST    | `/api/kb/{kb_id}/documents/upload` | 上传文档（multipart） | P0 |
| GET     | `/api/kb/{kb_id}/documents` | 文档列表（分页） | P0 |
| GET     | `/api/kb/{kb_id}/documents/{doc_id}` | 文档详情 | P0 |
| DELETE  | `/api/kb/{kb_id}/documents/{doc_id}` | 删除文档 | P0 |
| GET     | `/api/kb/{kb_id}/documents/{doc_id}/chunks` | 查看文档分片（调试用） | P0 |

```json
POST /api/kb/{kb_id}/documents/upload
Request: multipart/form-data { file: <binary>, title?: "可选标题" }
Response: {
  "id": "uuid",
  "title": "P368用户手册_V2.3.pdf",
  "status": "pending",    // 异步处理中
  "file_type": "pdf",
  "file_size": 5242880
}
```

### 4.4 问答

| 方法 | 路径 | 描述 | 阶段 |
|------|------|------|------|
| POST   | `/api/qa/ask` | 提问（流式返回） | P0 |
| POST   | `/api/qa/ask/sync` | 提问（非流式，返回完整结果） | P0 |
| GET    | `/api/qa/conversations` | 会话历史列表 | P0 |
| GET    | `/api/qa/conversations/{conv_id}` | 单条会话的消息记录 | P0 |
| DELETE | `/api/qa/conversations/{conv_id}` | 删除会话 | P0 |
| POST   | `/api/qa/feedback` | 提交答案评价 | P0 |
| POST   | `/api/qa/human-transfer` | 转人工（P1） | P1 |

```json
POST /api/qa/ask
Request: {
  "question": "如何关闭SOS报警信息？",
  "kb_id": "uuid",                  // 可选：限定知识库
  "conversation_id": "uuid",        // 可选：续接历史会话
  "stream": true                    // 是否流式
}
Response (SSE stream):
  data: {"type": "delta", "content": "SOS"}
  data: {"type": "delta", "content": "报警关闭步骤如下：\n\n"}
  data: {"type": "delta", "content": "1. 进入设备设置菜单"}
  data: {"type": "done", "sources": [
    {"doc_id": "uuid", "title": "P368用户手册 V2.3", "chunk": "..."},
  ]}
```

**流式协议**：使用 Server-Sent Events (SSE)，Content-Type: `text/event-stream`，每个 event 为 JSON。

### 4.5 知识库搜索

| 方法 | 路径 | 描述 | 阶段 |
|------|------|------|------|
| GET | `/api/kb/{kb_id}/search?q=关键词` | 全文+语义搜索文档 | P0 |
| GET | `/api/search?q=关键词` | 跨知识库搜索 | P0 |

```json
GET /api/kb/{kb_id}/search?q=电子围栏&page=1&size=20
Response: {
  "results": [
    {"doc_id": "uuid", "title": "P368功能说明.pdf", "snippet": "...电子围栏最多可以创建...", "score": 0.92, "url": "/api/documents/uuid"}
  ],
  "total": 5,
  "page": 1
}
```

### 4.6 管理后台

| 方法 | 路径 | 描述 | 阶段 |
|------|------|------|------|
| GET    | `/api/admin/users` | 用户列表 | P0 |
| POST   | `/api/admin/users` | 创建用户 | P0 |
| PUT    | `/api/admin/users/{id}` | 编辑用户 | P0 |
| DELETE | `/api/admin/users/{id}` | 删除用户 | P0 |
| GET    | `/api/admin/stats/overview` | 概览统计（问答数/用户数） | P1 |
| GET    | `/api/admin/stats/qa-quality` | 问答质量分析（准确率/拒答率） | P1 |

---

## 5. 文档处理管道

### 5.1 管道流程

```
Upload → FileTypeDetect → Store → Celery Task
                                         ↓
                              ┌─────────────────────┐
                              │  Unstructured.io     │
                              │  - partition_pdf()   │
                              │  - partition_docx()  │
                              │  - partition_pptx()  │
                              │  - partition_xlsx()  │
                              │  → 标题/段落/表格    │
                              └─────────┬───────────┘
                                        ↓
                              ┌─────────────────────┐
                              │  Text Splitter       │
                              │  - RecursiveCharacter │
                              │  - chunk_size=512    │
                              │  - chunk_overlap=64  │
                              │  - 按标题层级保留    │
                              └─────────┬───────────┘
                                        ↓
                              ┌─────────────────────┐
                              │  Embedding (BGE)     │
                              │  → 1024-dim vector   │
                              └─────────┬───────────┘
                                        ↓
                              ┌─────────────────────┐
                              │  Write to Milvus     │
                              │  + PostgreSQL        │
                              └─────────┬───────────┘
                                        ↓
                              Mark document status = "ready"
                              WebSocket notify frontend
```

### 5.2 文本分块策略

```python
# text_splitter.py

from langchain_text_splitters import RecursiveCharacterTextSplitter

def get_text_splitter() -> RecursiveCharacterTextSplitter:
    return RecursiveCharacterTextSplitter(
        chunk_size=512,          # 按 token 计（约 700 中文字符）
        chunk_overlap=64,        # 保留上下文连续性
        separators=["\n\n", "\n", "。", "；", "，", " ", ""],
        length_function=num_tokens  # 使用 tiktoken 或简单中文字符计数
    )

def split_document(elements: list[dict]) -> list[dict]:
    """
    输入：Unstructured.io 解析后的 elements 列表
    输出：chunks 列表，每个包含 content、heading、page_number

    规则：
    - 每个 element 可能是 Title / NarrativeText / Table / ListItem
    - Title 作为 heading 传给后续的 chunks
    - Table element 直接转为 Markdown 表格字符串，单独成 chunk
    - NarrativeText / ListItem 按 RecursiveCharacter 拆分
    """
    chunks = []
    current_heading = ""
    for el in elements:
        if el.category == "Title":
            current_heading = el.text
        elif el.category == "Table":
            chunks.append({
                "content": el.metadata.text_as_html or el.text,
                "heading": current_heading,
                "page_number": el.metadata.page_number,
                "type": "table"
            })
        else:
            # 文本内容走分块器
            sub_chunks = splitter.split_text(el.text)
            for i, sc in enumerate(sub_chunks):
                chunks.append({
                    "content": sc,
                    "heading": current_heading,
                    "page_number": el.metadata.page_number,
                    "type": "text"
                })
    return chunks
```

### 5.3 表格解析（一期）

一期简单表格通过 Unstructured.io 的 `partition_xlsx()` 和表格提取能力处理。输出格式为 HTML table 字符串，保留基本行列结构。

```python
# unstructured_parser.py
from unstructured.partition.auto import partition

def parse_document(file_path: str) -> list[dict]:
    elements = partition(
        filename=file_path,
        strategy="auto",           # 自动选择解析策略
        include_page_breaks=True,
        languages=["zh"]           # 中文优先
    )
    return elements
```

### 5.4 表格解析（二期，复杂表格）

二期复杂表格采用**双阶段管道**，分工明确：

```
表格图片
    ↓
第一阶段：PP-StructureV2（百度 PaddleOCR 表格识别模块）
  → 职责：表格结构识别
  → 输出：行列框架、合并单元格位置、单元格坐标
  → 不做内容识别（不读文字）
    ↓
第二阶段：Qwen2.5-VL / Claude 多模态 LLM
  → 职责：语义理解与内容校正
  → 接收：结构化表格图片 + PP-StructureV2 的行列框架提示
  → 输出：结构化 JSON，如 [{"型号":"P368","参数":"频率","值":"400-470MHz"}]
  → 修正：OCR 错字修复、合并单元格层级理解、跨行跨列关系还原
```

两个角色不可互换：PP-StructureV2 做不了语义理解，多模态 LLM 直接读原始图片做结构识别的准确率也不如专用模型。详见 [附录 B](#b-表格解析路线对比二期决策依据)。

### 5.4 向量化写入

```python
# ingestion_service.py

class IngestionService:
    def __init__(self):
        self.embedding_model = BGEEmbedding(model_name="BAAI/bge-large-zh-v1.5")
        self.milvus = MilvusClient(...)

    def process_document(self, doc_id: str, file_path: str):
        # 1. 解析
        elements = parse_document(file_path)
        # 2. 分块
        chunks = split_document(elements)
        # 3. 向量化 + 写入 Milvus + 写入 PostgreSQL
        milvus_rows = []
        pg_chunks = []
        for i, chunk in enumerate(chunks):
            vector = self.embedding_model.encode(chunk["content"])
            chunk_id = f"{doc_id}_{i}"
            milvus_rows.append({
                "id": chunk_id,
                "chunk_id": chunk_id,
                "doc_id": doc_id,
                "kb_id": "...",
                "embedding": vector.tolist(),
            })
            pg_chunks.append(DocumentChunk(
                id=chunk_id, doc_id=doc_id, chunk_index=i,
                content=chunk["content"], heading=chunk.get("heading"),
                page_number=chunk.get("page_number"),
            ))
        # 4. 批量写入
        self.milvus.insert(collection_name="knowledge_chunks", data=milvus_rows)
        bulk_insert_chunks(pg_chunks)
        # 5. 标记完成
        mark_document_ready(doc_id)
```

---

## 6. 检索问答管道

### 6.1 Retriever 实现

```python
# retriever.py

class Retriever:
    """
    多路召回器。一期实现向量 + BM25 两路。
    二期叠加 LightRAG 图检索通道。
    """

    def __init__(self):
        self.embedding_model = BGEEmbedding(model_name="BAAI/bge-large-zh-v1.5")
        self.milvus = MilvusClient(...)
        self.bm25_index = None    # rank_bm25 索引，按知识库构建

    def build_bm25_index(self, kb_id: str):
        """为指定知识库构建 BM25 倒排索引"""
        chunks = get_chunks_by_kb(kb_id)
        tokenized = [self._tokenize(c.content) for c in chunks]
        self.bm25_index = BM25Okapi(tokenized)
        self._bm25_chunks = chunks  # 保持引用对齐

    async def retrieve(
        self,
        question: str,
        kb_id: str,
        top_k: int = 30,
        use_bm25: bool = True,
    ) -> list[ScoredChunk]:
        """
        多路召回，合并去重后按加权分数排序。
        """
        # 1. 向量通道
        q_vector = self.embedding_model.encode(question)
        vector_results = self.milvus.search(
            collection_name="knowledge_chunks",
            data=[q_vector.tolist()],
            anns_field="embedding",
            param={"metric_type": "IP", "params": {"nprobe": 16}},
            limit=top_k,
            expr=f'kb_id == "{kb_id}"',   # 按知识库过滤
        )
        vector_map = {hit["id"]: hit["score"] for hit in vector_results[0]}

        # 2. 稀疏通道（BM25）
        bm25_scores = {}
        if use_bm25 and self.bm25_index:
            tokenized_q = self._tokenize(question)
            scores = self.bm25_index.get_scores(tokenized_q)
            # 取 Top-20
            top_bm25 = sorted(enumerate(scores), key=lambda x: -x[1])[:20]
            for idx, score in top_bm25:
                chunk_id = self._bm25_chunks[idx].milvus_id
                bm25_scores[chunk_id] = score

        # 3. 合并去重
        all_ids = set(vector_map.keys()) | set(bm25_scores.keys())
        merged = []
        for cid in all_ids:
            dense_score = vector_map.get(cid, 0.0)
            sparse_score = bm25_scores.get(cid, 0.0)
            # 归一化+加权融合：dense 0.6, sparse 0.4
            final_score = 0.6 * dense_score + 0.4 * sparse_score
            merged.append((cid, final_score))

        # 4. 排序取 Top-K
        merged.sort(key=lambda x: -x[1])
        return merged[:top_k]
```

### 6.2 Reranker（一期：规则版）

```python
# reranker.py

class RuleReranker:
    """
    一期重排序器：基于查询-文档词重叠率 + 位置加权。
    零延迟，无需模型加载。
    """

    def rerank(self, question: str, chunks: list[ScoredChunk], top_k: int = 10) -> list[ScoredChunk]:
        q_tokens = set(self._tokenize(question))
        for c in chunks:
            c_tokens = set(self._tokenize(c.content))
            overlap = len(q_tokens & c_tokens) / max(len(q_tokens), 1)

            # 位置加权：越早出现（chunk_index 越小）的片段加分
            position_boost = 1.0 + max(0, 1.0 - c.chunk_index / 100) * 0.1

            # 标题匹配加权：如果查询词出现在 heading 中，加 0.2
            heading_boost = 1.2 if any(t in c.heading for t in q_tokens) else 1.0

            c.rerank_score = c.score * 0.7 + overlap * 0.3
            c.rerank_score *= position_boost * heading_boost

        chunks.sort(key=lambda x: -x.rerank_score)
        return chunks[:top_k]

    def _tokenize(self, text: str) -> list[str]:
        """简单中文分词：按字符 + 空格分词"""
        return list(jieba.cut(text))
```

### 6.3 工具函数定义（Skills / Tools）

本方案不采用多智能体串行框架，而是将检索/搜索等操作定义为**纯函数工具**，通过 LLM 的 function calling / tool use 机制统一调用。所有工具无需 LLM 参与执行，延迟<50ms。

```python
# tools.py — 所有工具为纯函数，无 LLM 参与

class KnowledgeTools:
    """
    注册给 LLM 的知识检索工具集。
    每个工具都是纯函数：输入 → 执行 → 输出，无 LLM 调用。
    """

    @tool_schema(
        name="retrieve",
        description="从知识库中检索与问题语义相关的文档片段",
        parameters={
            "query": {"type": "string", "description": "查询文本"},
            "kb_id": {"type": "string", "description": "知识库 ID"},
            "top_k": {"type": "integer", "default": 30},
        }
    )
    async def retrieve(self, query: str, kb_id: str, top_k: int = 30) -> list[dict]:
        """语义向量检索（Milvus + BGE），自动切换双语模型"""
        # 检测查询语言：英文占比 >30% 时自动切换 M3E 多语言模型
        eng_ratio = sum(1 for c in query if 'a' <= c <= 'z' or 'A' <= c <= 'Z') / max(len(query), 1)
        model = "BAAI/m3e-base" if eng_ratio > 0.3 else "BAAI/bge-large-zh-v1.5"
        if model != getattr(self, '_current_embedding_model', None):
            self.embedding_model = BGEEmbedding(model_name=model)
            self._current_embedding_model = model
        q_vector = self.embedding_model.encode(query)
        results = self.milvus.search(
            collection_name="knowledge_chunks",
            data=[q_vector.tolist()],
            limit=top_k,
            expr=f'kb_id == "{kb_id}"',
        )
        return [{"chunk_id": h["id"], "content": "...", "score": h["score"]} for h in results[0]]
        # 延迟：<100ms

    @tool_schema(
        name="bm25_search",
        description="通过关键词精确匹配搜索知识库，适合型号/参数等精确查询",
        parameters={
            "query": {"type": "string", "description": "关键词"},
            "kb_id": {"type": "string", "description": "知识库 ID"},
            "top_k": {"type": "integer", "default": 20},
        }
    )
    async def bm25_search(self, query: str, kb_id: str, top_k: int = 20) -> list[dict]:
        """BM25 稀疏检索，补充精确匹配"""
        tokenized_q = self._tokenize(query)
        scores = self.bm25_index.get_scores(tokenized_q)
        top_indices = sorted(enumerate(scores), key=lambda x: -x[1])[:top_k]
        return [{"chunk_id": self._chunks[i].milvus_id, "content": "...", "score": s}
                for i, s in top_indices]
        # 延迟：<50ms

    @tool_schema(
        name="rerank",
        description="对检索结果进行重排序，返回最相关的前 N 个结果",
        parameters={
            "query": {"type": "string"},
            "results": {"type": "array", "items": {"type": "object"}},
            "top_k": {"type": "integer", "default": 10},
        }
    )
    async def rerank(self, query: str, results: list[dict], top_k: int = 10) -> list[dict]:
        """规则重排序（一期：词重叠）或 BGE-Reranker（二期）"""
        return sorted(results, key=lambda r: self._overlap_score(query, r["content"]), reverse=True)[:top_k]
        # 延迟：一期<10ms / 二期<100ms

    @tool_schema(
        name="graph_search",
        description="【二期启用】在知识图谱中检索与实体相关的关联信息",
        parameters={
            "entities": {"type": "array", "items": {"type": "string"}, "description": "实体名称列表"},
            "relation_types": {"type": "array", "items": {"type": "string"}, "default": None},
        }
    )
    async def graph_search(self, entities: list[str], relation_types: list[str] = None) -> list[dict]:
        """基于 LightRAG 的图检索"""
        results = await self.lightrag.query(entities, mode="local", top_k=10)
        return results
        # 延迟：<200ms
```

### 6.4 RAG Service（问答入口 — 基于 function calling）

```python
# rag_service.py

class RAGService:
    """
    问答核心服务。
    采用单次 LLM 调用 + tool use（function calling）模式。
    LLM 只调一次，需要查知识时由 LLM 自动调用注册的工具函数，
    工具是纯函数（无LLM参与），结果返回 LLM 上下文后继续生成。
    """

    def __init__(self):
        self.tools = KnowledgeTools()
        self.llm = LLMService(model="qwen-max")
        # 注册工具定义（function calling schema）
        self.tool_definitions = [
            {
                "type": "function",
                "function": {
                    "name": "retrieve",
                    "description": "从知识库中检索与问题语义相关的文档片段",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "查询文本"},
                            "kb_id": {"type": "string", "description": "知识库ID"},
                            "top_k": {"type": "integer", "default": 30},
                        },
                        "required": ["query", "kb_id"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "bm25_search",
                    "description": "通过关键词精确匹配搜索知识库，适合型号/参数等精确查询",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "关键词"},
                            "kb_id": {"type": "string", "description": "知识库ID"},
                        },
                        "required": ["query", "kb_id"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "graph_search",
                    "description": "【二期启用】在知识图谱中检索与实体相关的关联信息",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "entities": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["entities"],
                    },
                },
            },
        ]

    async def ask(
        self,
        question: str,
        kb_id: str,
        conversation_id: str | None = None,
        stream: bool = True,
    ) -> AsyncGenerator[dict]:
        """
        完整问答流程：单次 LLM 调用，LLM 自主决定是否调工具。
        """
        history = self._get_history(conversation_id)
        messages = self._build_messages(question, history)

        # 1. 第一轮：LLM 判断是否需要调用工具
        response = await self.llm.generate_with_tools(
            messages=messages,
            tools=self.tool_definitions,
            tool_choice="auto",
        )

        # 2. 如果需要调工具，执行工具并返回结果
        if response.tool_calls:
            for tc in response.tool_calls:
                tool_result = await self._execute_tool(tc.function.name, tc.function.arguments, kb_id)
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": tool_result})

            # 3. LLM 基于工具结果生成最终答案（第二次 LLM 调用，同一个回合）
            final_response = await self.llm.generate_with_tools(
                messages=messages,
                tools=self.tool_definitions,
                stream=stream,
            )

            if stream:
                full_answer = ""
                async for delta in final_response:
                    full_answer += delta
                    yield {"type": "delta", "content": delta}
                yield {
                    "type": "done",
                    "answer": full_answer,
                    "sources": self._extract_sources(messages),
                }
            else:
                yield {
                    "type": "done",
                    "answer": final_response,
                    "sources": self._extract_sources(messages),
                }
        else:
            # 无需调工具，直接返回 LLM 的回答
            yield {"type": "done", "answer": response.content, "sources": []}

        # 4. 保存会话记录（异步）
        await self._save_conversation(question, messages, conversation_id)

    async def _execute_tool(self, name: str, args_json: str, kb_id: str) -> str:
        """执行工具调用，纯函数路径，无LLM参与"""
        args = json.loads(args_json)
        args["kb_id"] = kb_id
        tool_map = {
            "retrieve": self.tools.retrieve,
            "bm25_search": self.tools.bm25_search,
            "graph_search": self.tools.graph_search,
        }
        if name == "rerank":
            return await self.tools.rerank(**args)

        results = await tool_map[name](**args)
        # 检查相关度阈值（无依据拒答）
        if not results or results[0].get("score", 0) < 0.6:
            return json.dumps({"status": "no_relevant_results", "message": REJECT_MESSAGE})
        return json.dumps({"status": "ok", "results": results[:10]})

    def _build_messages(self, question: str, history: str) -> list[dict]:
        system_prompt = """你是一个专业的对讲机/通信设备产品知识助手。
你的工作方式是使用我提供的工具来检索知识，然后基于检索结果回答用户问题。

核心规则：
1. 使用 retrieve/bm25_search 工具检索知识库，不要凭记忆回答
2. 如果工具返回 no_relevant_results，回答预设拒答话术，不要编造
3. 引用来源时在答案后标注 [来源：文档标题]
4. 涉及步骤时用编号列表，涉及参数对比时用表格
5. 如需精确匹配（型号/参数），优先使用 bm25_search"""

        msgs = [{"role": "system", "content": system_prompt}]
        if history:
            msgs.append({"role": "system", "content": f"对话历史：\n{history}"})
        msgs.append({"role": "user", "content": question})
        return msgs

    REJECT_MESSAGE = "抱歉，在现有知识库中未找到相关依据，已通知管理员补充。"
```

### 6.5 LLM 服务封装（支持 tool use）

```python
# llm_service.py

class LLMService:
    """
    LLM 调用封装。支持多厂商切换、流式、tool use / function calling。

    ⚠️ 部署约束：
    - 如果客户要求私有化部署或数据不出境（对讲机/安防行业常见约束），
      GPT-4 和 Claude 的 API 不可使用，只能选通义千问（DashScope API）
      或私有化部署开源模型（Qwen2.5-72B-Instruct 等）
    - GPT-4 / Claude 仅适用于数据可出境的非受限场景
    - 多厂商切换逻辑请与客户确认数据合规要求后冻结选型
    """

    def __init__(self, model: str = "qwen-max"):
        self.model = model
        self.client = self._init_client(model)

    async def generate_with_tools(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        tool_choice: str = "auto",
        stream: bool = True,
    ):
        """
        一次 LLM 调用，支持 tool use。
        LLM 自动决定是否调用工具，工具执行由调用方处理。
        """
        kwargs = dict(
            model=self.model,
            messages=messages,
            temperature=0.3,
            max_tokens=2048,
            stream=stream,
        )
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = tool_choice

        if stream:
            return self._stream_generate(**kwargs)
        else:
            return await self.client.chat.completions.create(**kwargs)

    async def stream(self, messages: list[dict], tools: list[dict] | None = None):
        """流式生成"""
        async for chunk in self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            tools=tools,
            stream=True,
            temperature=0.3,
            max_tokens=2048,
        ):
            if chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content
            elif chunk.choices[0].delta.tool_calls:
                yield chunk.choices[0].delta.tool_calls
```

### 6.6 二期 GraphRAG 集成（LightRAG，作为工具函数注册）

```python
# tools.py — 二期新增 graph_search 工具

from lightrag import LightRAG
from lightrag.storage import Neo4jStorage

class GraphTools:
    """
    图检索工具集。注册为 LLM 的 function calling 工具。
    纯函数执行，无 LLM 参与（LightRAG 的 LLM 抽取在图谱构建阶段完成）。
    """

    def __init__(self, neo4j_uri, neo4j_user, neo4j_password):
        self.rag = LightRAG(
            storage=Neo4jStorage(uri=neo4j_uri, user=neo4j_user, password=neo4j_password),
            llm_model="qwen-max",
            embedding_model="BAAI/bge-large-zh-v1.5",
        )

    @tool_schema(
        name="graph_search",
        description="在知识图谱中检索实体关联信息，支持多跳推理",
        parameters={
            "entities": {"type": "array", "items": {"type": "string"}},
            "relation_types": {"type": "array", "items": {"type": "string"}, "default": None},
        }
    )
    async def graph_search(self, entities: list[str], relation_types: list[str] = None) -> list[dict]:
        """
        图检索工具。纯函数，延迟 <200ms。
        LightRAG 的 local 模式以实体为中心检索子图。
        """
        results = await self.rag.query(
            entities,
            mode="local",     # local 模式：从实体出发展开子图
            top_k=10,
        )
        return results

    async def ingest_document(self, content: str, metadata: dict):
        """
        增量更新：LightRAG 自动抽取实体关系，写入 Neo4j。
        无需全量重建。在文档入库管道中调用，不在问答链路中。
        """
        await self.rag.insert(content, metadata=metadata)
```

---

## 7. 前端设计

### 7.1 路由

| 路径 | 视图 | 描述 | 权限 |
|------|------|------|------|
| `/login` | LoginView | 登录 | 无 |
| `/chat` | ChatView | 问答对话框（默认） | viewer+ |
| `/chat/:convId` | ChatView | 续接历史会话 | viewer+ |
| `/kb` | KbBrowseView | 知识库浏览 | viewer+ |
| `/kb/:kbId` | KbDetailView | 知识库详情+文档列表 | viewer+ |
| `/admin/users` | AdminUsersView | 用户管理 | admin |
| `/admin/kb` | AdminKbView | 知识库配置管理 | admin/kb_admin |
| `/admin/stats` | AdminStatsView | 数据统计（P1） | admin/kb_admin |

### 7.2 问答界面组件树

```
ChatView
├── ChatHeader（对话标题 + 知识库选择器）
├── ChatSidebar（左侧：会话历史列表）
├── ChatMessages（消息列表）
│   ├── ChatMessage（用户消息）
│   ├── ChatMessage（AI 回复）
│   │   └── SourceCard（来源卡片，可点击跳转）
│   │   └── FeedbackButtons（👍/👎）
│   └── ...
├── ChatInput（输入框 + 发送按钮）
└── TransferHuman（转人工按钮，P1）
```

### 7.3 流式渲染

```typescript
// 前端 SSE 接收流式响应
const eventSource = new EventSource(`/api/qa/ask?question=${encodeURIComponent(q)}&stream=true`);

eventSource.onmessage = (event) => {
    const data = JSON.parse(event.data);
    if (data.type === 'delta') {
        // 追加流式文本（保持 Markdown 渲染）
        currentMessage.content += data.content;
    } else if (data.type === 'done') {
        // 渲染来源引用卡片
        renderSources(data.sources);
        eventSource.close();
    }
};
```

---

## 8. 部署架构

### 8.1 Docker Compose 服务编排

```yaml
# docker-compose.yml（一期）

version: "3.9"
services:
  api:
    build: ./backend
    ports: ["8000:8000"]
    depends_on: [postgres, milvus, redis]
    environment:
      - DATABASE_URL=postgresql://...
      - MILVUS_HOST=milvus
      - REDIS_URL=redis://redis:6379
      - LLM_API_KEY=${LLM_API_KEY}
      - LLM_MODEL=qwen-max

  worker:          # Celery worker
    build: ./backend
    command: celery -A worker.celery_app worker -l info
    depends_on: [postgres, milvus, redis]

  frontend:
    build: ./frontend
    ports: ["3000:80"]
    depends_on: [api]

  postgres:
    image: postgres:15
    volumes: [pgdata:/var/lib/postgresql/data]

  milvus:
    image: milvusdb/milvus:v2.4.0
    environment:
      ETCD_ENDPOINTS: etcd:2379
      MINIO_ADDRESS: minio:9000

  etcd:
    image: quay.io/coreos/etcd:v3.5.5

  minio:
    image: minio/minio
    volumes: [minio:/data]

  redis:
    image: redis:7

volumes: {pgdata: {}, minio: {}}
```

二期增加 Neo4j 服务：
```yaml
  neo4j:
    image: neo4j:5-community
    ports: ["7474:7474", "7687:7687"]
    environment:
      NEO4J_AUTH: neo4j/${NEO4J_PASSWORD}
    volumes: [neo4jdata:/data]
```

### 8.2 资源估算（一期）

| 服务 | CPU | 内存 | 磁盘 | 实例数 |
|------|-----|------|------|--------|
| API (FastAPI) | 4核 | 8GB | — | 2 (HA) |
| Worker (Celery) | 4核 | 8GB | — | 2 |
| PostgreSQL | 4核 | 8GB | 100GB | 1 |
| Milvus | 8核 | 16GB | 200GB | 1 |
| Redis | 2核 | 4GB | — | 1 |
| Frontend (Nginx) | 1核 | 1GB | — | 1 |

**总计**：约 23 核 CPU / 45GB 内存，3 台服务器可承载。

---

## 附录

### A. 二期关键集成：LightRAG 配置

```python
# 二期集成示例
from lightrag import LightRAG
from lightrag.storage import Neo4jStorage
from lightrag.llm import OpenAILLM
from lightrag.embedding import BGEEmbedding

rag = LightRAG(
    storage=Neo4jStorage(uri="bolt://neo4j:7687", user="neo4j", password="..."),
    vector_storage="milvus",          # 复用一期 Milvus
    llm=OpenAILLM(model="qwen-max"),
    embedding=BGEEmbedding(model="BAAI/bge-large-zh-v1.5"),
    chunk_size=512,
    chunk_overlap=64,
)

# 增量插入（无需全量重建）
await rag.insert("P368对讲机支持SOS报警功能，长按侧键3秒触发...")

# 混合检索
results = await rag.query("如何关闭SOS报警？", mode="hybrid", top_k=10)
```

### B. 表格解析路线对比（二期决策依据）

| 方案 | 优势 | 劣势 | 适用场景 |
|------|------|------|---------|
| **多模态 LLM 读图**（推荐） | 理解合并单元格/跨行/跨列；LLM reasoning 自动做行列映射 | 延迟稍高（2-5s/图）；API 调用费 | 复杂异构表格 |
| PP-StructureV2 | 中文表格支持好；速度快（<1s） | 对合并单元格和多级表头鲁棒性不够 | 辅助结构识别 |

**决策**：二期以多模态 LLM 为主（Qwen2.5-VL 或 Claude），PP-StructureV2 做预处理辅助（表格区域检测+基础行列分割），LLM 做最终的结构修复和三元组映射。

### C. 一期/二期/三期能力矩阵

| 能力 | 一期（P0，8周） | 二期（P1，4周） | 三期（P2，2周） |
|------|:--------------:|:--------------:|:--------------:|
| 文档解析（Unstructured.io） | ✅ | ✅ | ✅ |
| 简单表格转 Markdown | ✅ | ✅ | ✅ |
| 复杂表格（多模态LLM+PP-StrV2） | — | ✅ | ✅ |
| 图片检索（Chinese-CLIP） | — | ✅ | ✅ |
| 流程图解析 | — | — | ✅ |
| 向量检索（Milvus + BGE） | ✅ | ✅ | ✅ |
| 稀疏检索（rank_bm25） | ✅ | ✅ | ✅ |
| 图检索（LightRAG + Neo4j） | — | ✅ | ✅ |
| 规则 Reranker | ✅ | — | — |
| BGE-Reranker-v2-m3 | — | ✅ | ✅ |
| Function Calling 工具模式 | ✅ 一期默认 | ✅ | ✅ |
| 多轮对话上下文 | — | ✅ | ✅ |
| 主动追问 | — | ✅ | ✅ |
| LLM 流式输出 | ✅ | ✅ | ✅ |
| 答案溯源 | ✅ | ✅ | ✅ |
| 无依据拒答 | ✅ | ✅ | ✅ |
| 答案质量反馈 | ✅ | ✅ | ✅ |
| 英文界面/问答 | — | ✅ | ✅ |
| 企微 H5 插件 | ✅ | ✅ | ✅ |
| 在线文档编辑 | — | — | ✅ |
| 语音输入 | — | — | ✅ |
| 方案 Word 导出 | — | — | ✅ |

### D. 延迟预算与性能指标

| 阶段 | 组件 | 预算 | 说明 |
|------|------|------|------|
| LLM 调用 | Function calling（意图识别+工具选择） | <200ms | 与生成共用一次调用，首轮 tool_choice="auto" |
| 检索 | Milvus 向量检索 Top-30 | <100ms | IVF_FLAT，nprobe=16 |
| 检索 | BM25 稀疏检索 Top-20 | <50ms | rank_bm25，预建倒排索引 |
| 检索 | Neo4j 图检索（二期） | <200ms | Cypher 查询 + LightRAG local mode |
| 精排 | 规则 Reranker（一期） | <10ms | 词重叠 + 位置加权 |
| 精排 | BGE-Reranker-v2-m3（二期） | <100ms | Cross-Encoder 模型推理 |
| LLM 生成 | 首 token | <1000ms | 流式输出，含工具结果注入 Prompt |
| **合计** | **首 token 延迟** | **≤1.5s** | **仅一次 LLM 调用**，工具纯函数并行 < 200ms |

**设计约束**：
1. 问答链路中任何时候都不要出现"LLM 调 LLM"的递归——那是延迟灾难
2. 检索/精排/图查询必须为纯函数，不得引入第二次 LLM 调用
3. 如需"推理判断"（如多步排查），将推理逻辑合并到 LLM 生成的同一次调用中，通过 tool use 的多次工具调用来实现，而不是启动新的 LLM 会话

**对比参考：四智能体串行方案的延迟**

| 方案 | LLM 调用次数 | 首 token 延迟 | 根因 |
|------|:----------:|:----------:|------|
| 四智能体串行（调度→检索→推理→融合） | 4次 | ~6s | 每次 LLM 调用 ~1.5s，串行累加 |
| 单引擎 + function calling（✅ 本方案） | **1次** | **≤1.5s** | 检索工具为纯函数 < 50ms，不增加 LLM 串行调用 |
