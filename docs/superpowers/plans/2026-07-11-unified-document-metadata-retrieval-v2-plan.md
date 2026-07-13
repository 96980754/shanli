# 知识库统一元数据与检索策略 V2 实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 `superpowers:subagent-driven-development`（推荐）或 `superpowers:executing-plans` 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 将规范化文档元数据、统一有效文档 Filter 与配置化元数据重排接入当前数据库 RAG 链路，确保无权限文档不会进入召回候选集，并支持无需重入库的检索权重调优。

**架构：** `Document` 作为权限和分类元数据 SSOT；`EffectiveDocumentFilter` 将当前知识库权限、知识视图规则和用户角色转换为检索可消费的硬过滤条件。`DbChunkLoader` 仅加载通过该 Filter 的 `DocumentChunk`，保留文档元数据供 `MetadataReranker` 使用；策略从 YAML 在每次查询时读取。当前内存模式保持原有最小行为，V2 硬过滤优先在数据库模式实现。

**技术栈：** FastAPI、SQLAlchemy ORM、PyYAML、pytest、SQLite/StaticPool、现有 `RAGService`/`KnowledgeTools`。

---

## 文件结构与职责

| 文件 | 变更 | 职责 |
|---|---|---|
| `backend/app/models/document.py` | 修改 | 增加 `scope`、`document_type`、`product`、`priority`、`acl_roles` 规范元数据列。 |
| `backend/app/models/user.py` | 修改 | 扩展用户角色与知识视图规则字段，保留现有兼容语义。 |
| `backend/app/services/document_filter_service.py` | 创建 | 定义 `EffectiveDocumentFilter`，从用户、KB 权限和规则计算统一硬过滤条件。 |
| `backend/app/services/db_chunk_loader.py` | 修改 | Join `Document`，按有效 Filter 加载可访问 Chunk，并输出规范元数据。 |
| `backend/app/services/retrieval_policy.py` | 创建 | 加载、校验 YAML 策略；提供产品别名识别与元数据分数计算。 |
| `backend/app/services/reranker.py` | 修改 | 保留既有基础重排，新增策略元数据重排入口。 |
| `backend/app/services/tools.py` | 修改 | 将初始候选集交给 V2 元数据重排，保留 BM25 与向量候选行为。 |
| `backend/app/main.py` | 修改 | 在数据库问答入口计算有效 Filter、仅加载可见 Chunk，并将检索策略注入候选重排。 |
| `backend/config/retrieval_policy.yaml` | 创建 | 默认 Type/Product/Priority 权重、公式系数和 Top-K。 |
| `backend/tests/test_models_schema.py` | 修改 | 覆盖新规范字段与历史默认值。 |
| `backend/tests/test_document_filter_service.py` | 创建 | 覆盖 scope/部门/产品/密级/角色以及 `can_grant` 边界。 |
| `backend/tests/test_db_chunk_loader.py` | 修改 | 验证只加载符合有效 Filter 的 Chunk，且输出元数据。 |
| `backend/tests/test_retrieval_policy.py` | 创建 | 覆盖 YAML 校验、编码回退、产品别名和排序公式。 |
| `backend/tests/test_database_mode_api.py` | 修改 | 端到端验证无权 Chunk 不进入 QA 来源、策略重排有效。 |
| `backend/app/static/admin.html`、`backend/app/static/admin.js` | 修改 | 上传/详情最小编辑 V2 字段，展示当前策略只读摘要。 |
| `backend/tests/test_frontend_shell.py` | 修改 | 覆盖 V2 元数据节点和策略只读节点。 |
| `docs/api/api-reference.md` | 修改 | 记录新元数据、权限过滤、策略行为。 |
| `docs/implementation/tech-code-mapping.md` | 修改 | 记录 SSOT、Filter、策略重排和测试映射。 |

---

### 任务 1：添加规范元数据与兼容默认值

**文件：**
- 修改：`backend/app/models/document.py`
- 修改：`backend/tests/test_models_schema.py`

- [ ] **步骤 1：编写失败的模型持久化测试**

```python
def test_document_persists_v2_metadata_with_compatible_defaults():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    role = Role(name="管理员", level=3)
    user = User(username="admin", password_hash="hash", role=role)
    kb = KnowledgeBase(name="产品库", visibility="department", owner=user)
    explicit = Document(
        kb=kb, title="whitepaper.pdf", file_type="pdf",
        scope="I", document_type="WP", product="MC", priority="P0", acl_roles='["sales"]',
    )
    legacy = Document(kb=kb, title="legacy.txt", file_type="txt")
    session.add_all([role, user, kb, explicit, legacy])
    session.commit()

    assert explicit.scope == "I"
    assert explicit.document_type == "WP"
    assert explicit.product == "MC"
    assert explicit.priority == "P0"
    assert explicit.acl_roles == '["sales"]'
    assert legacy.scope == "I"
    assert legacy.document_type == "OTH"
    assert legacy.product == "GEN"
    assert legacy.priority == "P2"
    assert legacy.acl_roles == "[]"
```

- [ ] **步骤 2：运行测试验证红灯**

运行：`cd backend && pytest tests/test_models_schema.py::test_document_persists_v2_metadata_with_compatible_defaults -v`

预期：FAIL，`Document` 尚不接受 V2 字段。

- [ ] **步骤 3：实现最小模型字段**

在 `Document` 增加：

```python
scope: Mapped[str] = mapped_column(String(1), default="I")
document_type: Mapped[str] = mapped_column(String(16), default="OTH")
product: Mapped[str] = mapped_column(String(16), default="GEN")
priority: Mapped[str] = mapped_column(String(2), default="P2")
acl_roles: Mapped[str] = mapped_column(Text, default="[]")
```

保留 `visibility`、`product_line`、`department`、`security_level`、`tags`，不得删除旧字段；正式 PostgreSQL 迁移留给后续 Alembic 工作。

- [ ] **步骤 4：运行模型测试验证绿灯**

运行：`cd backend && pytest tests/test_models_schema.py -v`

预期：PASS。

- [ ] **步骤 5：提交任务**

```bash
git add backend/app/models/document.py backend/tests/test_models_schema.py
git commit -m "feat: add normalized document metadata"
```

---

### 任务 2：定义统一有效文档过滤条件

**文件：**
- 创建：`backend/app/services/document_filter_service.py`
- 修改：`backend/app/models/user.py`
- 创建：`backend/tests/test_document_filter_service.py`

- [ ] **步骤 1：编写有效 Filter 的失败测试**

```python
def test_effective_filter_allows_matching_scope_department_product_and_security_level(context):
    service, kb, viewer = context.service, context.kb, context.viewer
    context.kb_service.set_permission(kb.id, viewer.id, {
        "can_view": True, "can_upload": False, "can_delete": False, "can_grant": False,
    })
    context.rules.set_rule(
        kb.id, viewer.id,
        allowed_departments=["售后"],
        allowed_product_lines=["MC"],
        allowed_visibilities=["I"],
        max_security_level=2,
    )
    effective = service.build_filter(kb.id, viewer.id, user_roles={"sales"})

    assert effective.matches(Document(
        kb_id=kb.id, title="manual", file_type="txt", scope="I",
        department="售后", product="MC", security_level=2, acl_roles='["sales"]',
    )) is True
```

- [ ] **步骤 2：补充拒绝与管理绕过的失败测试**

覆盖：

```python
assert effective.matches(scope_r_document) is False
assert effective.matches(too_secret_document) is False
assert effective.matches(product_ms_document) is False
assert effective.matches(Document(..., acl_roles='["support"]')) is False
```

另建 `can_grant=True` 用户，验证其绕过**用户级**部门/产品/scope 规则，但仍不绕过显式 `max_security_level` 强制限制；无 `can_view` 的 `build_filter` 必须返回拒绝全部 Filter。

- [ ] **步骤 3：运行测试验证红灯**

运行：`cd backend && pytest tests/test_document_filter_service.py -v`

预期：FAIL，服务未定义。

- [ ] **步骤 4：实现 Filter 数据结构和服务**

```python
@dataclass(frozen=True)
class EffectiveDocumentFilter:
    allow_all: bool
    allowed_scopes: set[str] | None
    allowed_departments: set[str] | None
    allowed_products: set[str] | None
    allowed_roles: set[str] | None
    max_security_level: int | None

    def matches(self, document: Document) -> bool: ...
```

实现要求：

- `None` 表示维度未限制；空集合表示显式拒绝该维度全部值；
- 读取现有 `KnowledgeViewRule` 的三个 JSON 列时，将旧 `visibility` 值转换为 `C/I/R`；`public→C`、`internal→I`、`restricted→R`；
- 旧 `allowed_product_lines` 与规范 `Document.product` 比较，先支持编码值，未匹配编码时回退比对 `Document.product_line`；
- `acl_roles=[]` 表示文档不限制角色，非空时必须与 `user_roles` 相交；
- `can_grant` 只将用户级限制折叠为 `None`，不跳过参数传入的 `system_max_security_level`；
- 无 `can_view` 返回所有维度拒绝的 Filter，不能使 Chunk 进入候选集。

- [ ] **步骤 5：运行测试验证绿灯**

运行：`cd backend && pytest tests/test_document_filter_service.py -v`

预期：PASS。

- [ ] **步骤 6：提交任务**

```bash
git add backend/app/services/document_filter_service.py backend/app/models/user.py backend/tests/test_document_filter_service.py
git commit -m "feat: add effective document filter service"
```

---

### 任务 3：让数据库 Chunk 加载器执行权限过滤并携带元数据

**文件：**
- 修改：`backend/app/services/db_chunk_loader.py`
- 修改：`backend/tests/test_db_chunk_loader.py`

- [ ] **步骤 1：编写过滤 Chunk 的失败测试**

```python
def test_db_chunk_loader_only_returns_chunks_matching_effective_filter(session_with_documents):
    session, kb, allowed, blocked = session_with_documents
    loader = DbChunkLoader(session)
    effective = EffectiveDocumentFilter(
        allow_all=False,
        allowed_scopes={"I"},
        allowed_departments={"售后"},
        allowed_products={"MC"},
        allowed_roles={"sales"},
        max_security_level=2,
    )

    chunks = loader.load_chunks(kb_id=kb.id, document_filter=effective)

    assert [item["content"] for item in chunks] == ["allowed"]
    assert chunks[0]["scope"] == "I"
    assert chunks[0]["document_type"] == "UM"
    assert chunks[0]["product"] == "MC"
    assert chunks[0]["priority"] == "P0"
```

- [ ] **步骤 2：运行测试验证红灯**

运行：`cd backend && pytest tests/test_db_chunk_loader.py::test_db_chunk_loader_only_returns_chunks_matching_effective_filter -v`

预期：FAIL，加载器不接受 `document_filter`，且不输出元数据。

- [ ] **步骤 3：实现最小加载器改造**

```python
def load_chunks(
    self,
    kb_id: int,
    document_filter: EffectiveDocumentFilter | None = None,
) -> list[dict[str, Any]]:
```

查询仍按 KB 获取 `DocumentChunk`，再通过 `document_filter.matches(chunk.document)` 过滤；`None` 仅用于已有低层单元测试，应用层数据库检索必须始终传 Filter。输出增加：`document_id`、`scope`、`document_type`、`product`、`priority`、`security_level`、`acl_roles`。

- [ ] **步骤 4：运行加载器测试验证绿灯**

运行：`cd backend && pytest tests/test_db_chunk_loader.py -v`

预期：PASS。

- [ ] **步骤 5：提交任务**

```bash
git add backend/app/services/db_chunk_loader.py backend/tests/test_db_chunk_loader.py
git commit -m "feat: filter database retrieval chunks by document metadata"
```

---

### 任务 4：创建可配置检索策略、产品识别和元数据重排

**文件：**
- 创建：`backend/config/retrieval_policy.yaml`
- 创建：`backend/app/services/retrieval_policy.py`
- 修改：`backend/app/services/reranker.py`
- 创建：`backend/tests/test_retrieval_policy.py`

- [ ] **步骤 1：编写策略加载与配置校验失败测试**

```python
def test_retrieval_policy_loads_default_weights_and_validates_formula(tmp_path):
    path = tmp_path / "policy.yaml"
    path.write_text("""
type_weight: {UM: 0.9, OTH: 0.3}
product_weight: {MC: 1.0, GEN: 0.8}
priority_boost: {P0: 1.2, P1: 1.0, P2: 0.8}
formula: {similarity_ratio: 0.75, type_ratio: 0.10, product_ratio: 0.10, priority_ratio: 0.05}
top_k: {initial: 100, after_rerank: 20, final: 10}
""")

    policy = RetrievalPolicy.load(path)

    assert policy.type_weight("UM") == 0.9
    assert policy.type_weight("unknown") == 0.3
    assert policy.top_k.final == 10
```

增加总和不是 `1.0`、负数权重、缺少 Top-K 时抛 `ValueError` 的测试。

- [ ] **步骤 2：编写产品识别与排序的失败测试**

```python
def test_policy_rerank_only_boosts_documents_matching_detected_product(tmp_path):
    policy = RetrievalPolicy.load(write_policy(tmp_path))
    assert policy.detect_products("MCSTARS 支持什么部署方式？") == {"MC"}

    ranked = policy.rerank([
        {"chunk_id": "mc", "score": 0.91, "document_type": "DG", "product": "MC", "priority": "P0"},
        {"chunk_id": "ms", "score": 0.93, "document_type": "DG", "product": "MS", "priority": "P0"},
    ], matched_products={"MC"})

    assert ranked[0]["chunk_id"] == "mc"
    assert ranked[0]["metadata_score"] > ranked[1]["metadata_score"]


def test_policy_does_not_apply_product_boost_when_question_has_no_product_alias(tmp_path):
    policy = RetrievalPolicy.load(write_policy(tmp_path))
    assert policy.detect_products("支持什么部署方式？") == set()
```

- [ ] **步骤 3：运行策略测试验证红灯**

运行：`cd backend && pytest tests/test_retrieval_policy.py -v`

预期：FAIL，策略模块不存在。

- [ ] **步骤 4：创建默认 YAML 与策略服务**

写入设计文档第 5.1 节的默认权重。`RetrievalPolicy` 需支持：

```python
@classmethod
def load(cls, path: Path) -> "RetrievalPolicy": ...
def detect_products(self, question: str) -> set[str]: ...
def rerank(self, chunks: list[dict[str, Any]], matched_products: set[str]) -> list[dict[str, Any]]: ...
```

`rerank()` 对每个 Chunk：

```python
final = (
    similarity_ratio * normalized_score
    + type_ratio * type_weight(document_type)
    + product_ratio * (product_weight(product) if product in matched_products else 0.0)
    + priority_ratio * priority_boost(priority)
)
```

`normalized_score` 先将当前候选集分数归一化至 `[0, 1]`；当全部分数相同时，非负分数统一归为 `1.0`。未知 type/product/priority 分别回退 `OTH/GEN/P2`。把结果放入 `metadata_score`，按降序稳定排序。

- [ ] **步骤 5：使 RuleReranker 可组合策略排序**

给 `RuleReranker.rerank()` 增加可选 `policy` 与 `matched_products` 参数；传入策略时优先调用 `policy.rerank()`，再只保留 `top_k`。无策略时保留既有算法以避免内存模式回归。

- [ ] **步骤 6：运行策略测试验证绿灯**

运行：`cd backend && pytest tests/test_retrieval_policy.py tests/test_reranker.py -v`

预期：PASS。

- [ ] **步骤 7：提交任务**

```bash
git add backend/config/retrieval_policy.yaml backend/app/services/retrieval_policy.py backend/app/services/reranker.py backend/tests/test_retrieval_policy.py
git commit -m "feat: add configurable metadata retrieval policy"
```

---

### 任务 5：将有效 Filter 和策略重排接入数据库问答链路

**文件：**
- 修改：`backend/app/services/tools.py`
- 修改：`backend/app/main.py`
- 修改：`backend/tests/test_database_mode_api.py`
- 修改：`backend/tests/test_rag_service.py`

- [ ] **步骤 1：为数据库问答排除不可见来源编写失败测试**

在数据库应用 fixture 中创建两个文档：一个符合 viewer 的 scope/部门/产品/密级规则，另一个只在产品或密级上不匹配。向 viewer 授予 `can_view` 并配置规则后：

```python
response = client.post(
    "/api/qa/ask/sync",
    headers=auth(viewer_token),
    json={"kb_id": str(kb.id), "question": "MCSTARS 部署"},
)

assert response.status_code == 200
assert [source["doc_title"] for source in response.json()["sources"]] == ["allowed.txt"]
```

- [ ] **步骤 2：为配置变更生效编写失败测试**

复制默认策略到 `tmp_path`，将两个相近 Chunk 设为 `UM/P2` 和 `WP/P0`。首次请求使用默认策略；改写 YAML 的 type/priority 权重后再次请求，断言同一个可见候选集的第一个 source 变化，且无需重新上传/解析文档。

- [ ] **步骤 3：运行端到端测试验证红灯**

运行：`cd backend && pytest tests/test_database_mode_api.py -v`

预期：FAIL，不可见文档仍被 `DbChunkLoader` 读取，或策略没有注入问答链路。

- [ ] **步骤 4：在问答入口构建并传递 Filter**

数据库模式 `ask_sync()` 中：

```python
user_id = int(resolve_session_user_id(app, session))
document_filter = app.state.document_filter_service.build_filter(
    kb_id=int(request.kb_id),
    user_id=user_id,
    user_roles=app.state.document_filter_service.roles_for_user(user_id),
)
chunks = DbChunkLoader(app.state.db_session).load_chunks(
    kb_id=int(request.kb_id),
    document_filter=document_filter,
)
```

先保留现有 `require_kb_permission(..., "can_view")`，但不得在 Filter 缺失时降级加载全部 Chunk。设置：

```python
app.state.rag_service.tools.set_retrieval_policy_path(app.state.retrieval_policy_path)
```

`create_app()` 默认：

```python
app.state.retrieval_policy_path = Path(__file__).resolve().parents[1] / "config" / "retrieval_policy.yaml"
```

允许测试覆盖为 `tmp_path` 下 YAML。

- [ ] **步骤 5：在 KnowledgeTools 中执行策略重排**

为 `KnowledgeTools` 增加：

```python
def set_retrieval_policy_path(self, path: Path | None) -> None: ...
```

在 `retrieve()` 与 `bm25_search()` 获得初始候选后，各自读取当前策略、识别 query 产品并按策略重排，初始候选上限来自 `top_k.initial`，最终保留调用方请求 top_k。若策略文件不可读或校验失败，必须显式抛出 `ValueError`，不得静默退化为可能绕开策略的排序；权限 Filter 已在 loader 中完成，不受策略错误影响。

- [ ] **步骤 6：运行问答和 RAG 回归验证绿灯**

运行：

```bash
cd backend && pytest tests/test_database_mode_api.py tests/test_rag_service.py tests/test_tools.py tests/test_reranker.py -v
```

预期：PASS，且数据库模式 QA 来源不包含任一不可见文档。

- [ ] **步骤 7：提交任务**

```bash
git add backend/app/services/tools.py backend/app/main.py backend/tests/test_database_mode_api.py backend/tests/test_rag_service.py
git commit -m "feat: enforce metadata filtering in database rag"
```

---

### 任务 6：更新管理台最小元数据编辑、文档与完整验证

**文件：**
- 修改：`backend/app/main.py`
- 修改：`backend/app/static/admin.html`
- 修改：`backend/app/static/admin.js`
- 修改：`backend/tests/test_frontend_shell.py`
- 修改：`docs/api/api-reference.md`
- 修改：`docs/implementation/tech-code-mapping.md`

- [ ] **步骤 1：为上传表单和策略只读区写失败壳测试**

```python
def test_admin_shell_contains_v2_metadata_fields_and_policy_summary():
    client = TestClient(create_app())
    body = client.get("/admin").text

    assert 'id="document-scope"' in body
    assert 'id="document-type"' in body
    assert 'id="document-product"' in body
    assert 'id="document-priority"' in body
    assert 'id="retrieval-policy-summary"' in body
```

并检查 `admin.js` 会将四个字段放入 `FormData`，且会请求只读策略摘要接口。

- [ ] **步骤 2：运行壳测试验证红灯**

运行：`cd backend && pytest tests/test_frontend_shell.py::test_admin_shell_contains_v2_metadata_fields_and_policy_summary -v`

预期：FAIL，节点与交互缺失。

- [ ] **步骤 3：实现最小上传、详情和策略摘要 API**

1. 上传路由增加 `scope: str = Form("I")`、`document_type: str = Form("OTH")`、`product: str = Form("GEN")`、`priority: str = Form("P2")`，在 `DbDocumentService.upload()` 和内存文档字典中写入。
2. 文档详情、列表和上传响应加入四个字段；使用允许编码集合验证非法值返回 422。
3. 新增只读 `GET /api/retrieval-policy`，要求已认证 session；返回当前 YAML 的 formula/top_k 与已加载权重，不允许在线写入。
4. `admin.html` 上传表单加入四个 select，显示编码与中文名；策略区域只读显示公式与 Top-K。
5. `admin.js` 在上传时把字段 append 到 `FormData`，在加载后台时读取 `/api/retrieval-policy` 并渲染文本。

- [ ] **步骤 4：运行壳和文档 API 测试验证绿灯**

运行：

```bash
cd backend && pytest tests/test_frontend_shell.py tests/test_document_api.py tests/test_document_detail_api.py -v
```

预期：PASS。

- [ ] **步骤 5：同步文档**

在 `docs/api/api-reference.md` 记录：规范 metadata 字段与编码、`GET /api/retrieval-policy`、硬 Filter 与 ReRank 的责任边界、无 `can_view` 或不匹配有效视图时不进入问答来源。

在 `docs/implementation/tech-code-mapping.md` 记录：

```text
Document Metadata SSOT
→ EffectiveDocumentFilter
→ DbChunkLoader 过滤
→ RetrievalPolicy 配置化重排
→ RAGService
```

登记新增测试文件和未来 Milvus/图谱保持同一 Filter 接口的迁移点。

- [ ] **步骤 6：执行完整回归**

运行：`cd backend && pytest -q`

预期：PASS。若失败，停止后按 `systematic-debugging` 处理；不得将失败状态标记完成。

- [ ] **步骤 7：端到端运行态验证**

以数据库模式启动应用，验证：

```text
管理员上传 MC/P0/I/售后文档
→ 授予 viewer can_view 并配置 scope=I、department=售后、product=MC、max_security_level=2
→ viewer 询问 MCSTARS
→ 返回来源只包含允许文档
→ 修改 retrieval_policy.yaml 权重
→ 同一候选集的来源排序发生预期变化，未重入库
```

同时验证 viewer 直接问题无法让规则外文档出现在来源中。停止验证服务。

- [ ] **步骤 8：提交任务**

```bash
git add backend/app/main.py backend/app/static/admin.html backend/app/static/admin.js backend/tests/test_frontend_shell.py docs/api/api-reference.md docs/implementation/tech-code-mapping.md
git commit -m "feat: expose v2 metadata and retrieval policy"
```

---

## 完成核对

- [ ] `Document` 持久化 `scope/document_type/product/priority/acl_roles`，旧数据有安全默认值。
- [ ] `EffectiveDocumentFilter` 是数据库检索、未来 Milvus 与图谱查询共用的硬权限契约。
- [ ] 无权限或不满足 scope、部门、产品、密级、角色的 Chunk 绝不进入数据库 RAG 候选集。
- [ ] `can_grant` 仅绕过用户级规则，不绕过强制密级约束。
- [ ] 策略 YAML 通过校验；未知编码与无产品问题有确定回退行为。
- [ ] 产品识别只影响排序，除非有效 Filter 明确配置 `allowed_products`。
- [ ] 修改策略文件可影响排序，不要求重入库。
- [ ] 管理台能填写/显示 V2 元数据，并只读查看生效策略。
- [ ] API、技术映射、定向与完整回归测试已同步并有新鲜验证证据。
