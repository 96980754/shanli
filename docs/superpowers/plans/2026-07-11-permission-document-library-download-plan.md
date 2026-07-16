# 权限文档库访问与下载实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 `superpowers:subagent-driven-development`（推荐）或 `superpowers:executing-plans` 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 让数据库模式用户按知识库 `can_view` 与有效知识视图规则浏览、查看并下载原始文档，管理员 `can_grant` 可绕过规则；新增稳定本地文件存储、下载审计和独立 `/documents` 页面。

**架构：** 数据库模式的原文件保存由 `FileStorageService` 抽象管理，`Document` 保存与存储后端无关的相对 `storage_key` 和文件描述信息。`DocumentAccessService` 集中执行知识库 `can_view`、`can_grant` 绕过和当前有效 `KnowledgeViewRule` 的判定，供文档列表、详情、下载以及后续 RAG 过滤复用。内存模式维持原有最小行为，不伪造知识视图或可持久下载能力。

**技术栈：** FastAPI、FastAPI `FileResponse`、SQLAlchemy ORM、SQLite/StaticPool 测试、pytest、原生 HTML/JavaScript、`pathlib`。

---

## 文件结构与职责

| 文件 | 变更 | 职责 |
|---|---|---|
| `backend/app/models/document.py` | 修改 | 为 `Document` 增加稳定文件存储元数据。 |
| `backend/app/models/kg_ops.py` | 修改 | 为 `AuditLog` 增加下载审计需要的目标、知识库、详情和时间字段。 |
| `backend/app/services/file_storage.py` | 创建 | 定义 `StoredFile`、文件存储协议和安全的本地实现。 |
| `backend/app/services/document_access_service.py` | 创建 | 统一数据库模式的文档级访问与列表过滤判断。 |
| `backend/app/services/db_document_service.py` | 修改 | 接收并持久化上传文件元数据；提供删除前的元数据定位与数据库删除操作。 |
| `backend/app/services/ingestion_service.py` | 修改 | 从已保存的原始文件读取内容进行解析，不再自行生成另一份原文件。 |
| `backend/app/main.py` | 修改 | 注入文件存储/访问服务，改造上传、列表、详情、下载和删除路由，并增加 `/documents` 页面路由。 |
| `backend/app/static/documents.html` | 创建 | 用户文档库静态页面结构。 |
| `backend/app/static/documents.js` | 创建 | 令牌认证、可见知识库/文档加载、详情展示和 Blob 下载。 |
| `backend/app/static/admin.html` | 修改 | 增加跳转到用户文档库的入口。 |
| `backend/app/static/qa.html` | 修改 | 增加跳转到用户文档库的入口。 |
| `backend/tests/test_file_storage.py` | 创建 | 验证本地存储保存、读取、删除、唯一命名和路径安全。 |
| `backend/tests/test_document_access_service.py` | 创建 | 验证统一的知识库权限、`can_grant` 绕过与知识视图过滤。 |
| `backend/tests/test_document_download_api.py` | 创建 | 验证数据库模式文档列表/详情/下载授权、响应头、审计和历史文档行为。 |
| `backend/tests/test_models_schema.py` | 修改 | 覆盖新增文档和审计字段的持久化。 |
| `backend/tests/test_upload_ingest_flow.py` | 修改 | 覆盖 ingestion 从已保存的 storage key 读取文件。 |
| `backend/tests/test_frontend_shell.py` | 修改 | 覆盖 `/documents` 壳、导航入口和下载交互钩子。 |
| `docs/api/api-reference.md` | 修改 | 记录列表/详情新增字段、下载接口、403/404 语义和下载审计。 |
| `docs/implementation/tech-code-mapping.md` | 修改 | 记录文件存储、文档访问服务、用户文档库及测试映射。 |

---

### 任务 1：建立稳定文件存储抽象

**文件：**
- 创建：`backend/app/services/file_storage.py`
- 测试：`backend/tests/test_file_storage.py`

- [ ] **步骤 1：编写保存后可读取的失败测试**

```python
from pathlib import Path

from app.services.file_storage import LocalFileStorageService


def test_local_file_storage_saves_and_opens_file_under_kb_document_path(tmp_path: Path):
    storage = LocalFileStorageService(tmp_path)

    stored = storage.save(
        content=b"SOS alarm",
        original_filename="P368 manual.txt",
        content_type="text/plain",
        kb_id=101,
    )

    assert stored.storage_key.startswith("knowledge-bases/101/documents/")
    assert stored.storage_key.endswith("-P368-manual.txt")
    assert stored.original_filename == "P368 manual.txt"
    assert stored.content_type == "text/plain"
    assert stored.file_size == 9
    assert storage.exists(stored.storage_key) is True
    assert storage.open(stored.storage_key).read() == b"SOS alarm"
```

- [ ] **步骤 2：运行测试验证失败**

运行：`cd backend && pytest tests/test_file_storage.py::test_local_file_storage_saves_and_opens_file_under_kb_document_path -v`

预期：FAIL，原因是 `app.services.file_storage` 或 `LocalFileStorageService` 尚不存在。

- [ ] **步骤 3：补充重名、删除和路径穿越的失败测试**

```python
import pytest


def test_local_file_storage_keeps_same_named_uploads_as_distinct_objects(tmp_path: Path):
    storage = LocalFileStorageService(tmp_path)

    first = storage.save(b"first", "manual.txt", "text/plain", kb_id=1)
    second = storage.save(b"second", "manual.txt", "text/plain", kb_id=1)

    assert first.storage_key != second.storage_key
    assert storage.open(first.storage_key).read() == b"first"
    assert storage.open(second.storage_key).read() == b"second"


def test_local_file_storage_deletes_existing_object_and_rejects_unsafe_keys(tmp_path: Path):
    storage = LocalFileStorageService(tmp_path)
    stored = storage.save(b"content", "manual.txt", "text/plain", kb_id=1)

    storage.delete(stored.storage_key)

    assert storage.exists(stored.storage_key) is False
    with pytest.raises(ValueError, match="Invalid storage key"):
        storage.open("../outside.txt")
```

- [ ] **步骤 4：运行扩展后的测试验证失败**

运行：`cd backend && pytest tests/test_file_storage.py -v`

预期：FAIL，原因是本地文件存储实现缺失。

- [ ] **步骤 5：实现最小、安全的存储协议和本地实现**

在 `backend/app/services/file_storage.py` 创建以下接口和实现；`storage_key` 必须始终为根目录下的相对 POSIX 路径：

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Protocol
from uuid import uuid4


@dataclass(frozen=True)
class StoredFile:
    storage_key: str
    original_filename: str
    content_type: str
    file_size: int


class FileStorageService(Protocol):
    def save(self, content: bytes, original_filename: str, content_type: str, kb_id: int) -> StoredFile: ...
    def open(self, storage_key: str) -> BinaryIO: ...
    def path_for(self, storage_key: str) -> Path: ...
    def exists(self, storage_key: str) -> bool: ...
    def delete(self, storage_key: str) -> None: ...


class LocalFileStorageService:
    def __init__(self, root: Path) -> None:
        self.root = root

    def save(self, content: bytes, original_filename: str, content_type: str, kb_id: int) -> StoredFile:
        safe_filename = self._safe_filename(original_filename)
        storage_key = f"knowledge-bases/{kb_id}/documents/{uuid4().hex}-{safe_filename}"
        path = self._path_for(storage_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return StoredFile(storage_key, original_filename, content_type, len(content))

    def _path_for(self, storage_key: str) -> Path:
        candidate = (self.root / storage_key).resolve()
        root = self.root.resolve()
        if not storage_key or candidate != root and root not in candidate.parents:
            raise ValueError("Invalid storage key")
        return candidate
```

实现 `open()`、`exists()`、`delete()`，并用保守白名单将文件名标准化（空文件名回退 `uploaded`），不得把客户端路径片段带入 `storage_key`。

- [ ] **步骤 6：运行文件存储测试验证通过**

运行：`cd backend && pytest tests/test_file_storage.py -v`

预期：PASS，全部文件存储行为通过。

- [ ] **步骤 7：提交此任务**

```bash
git add backend/app/services/file_storage.py backend/tests/test_file_storage.py
git commit -m "feat: add local file storage service"
```

---

### 任务 2：持久化文档存储元数据与下载审计字段

**文件：**
- 修改：`backend/app/models/document.py`
- 修改：`backend/app/models/kg_ops.py`
- 修改：`backend/tests/test_models_schema.py`

- [ ] **步骤 1：为文档和审计字段编写失败的模型测试**

在 `test_models_schema.py` 添加：

```python
def test_document_and_audit_log_persist_file_storage_and_download_fields():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    role = Role(name="管理员", level=3)
    user = User(username="admin", password_hash="hash", role=role)
    kb = KnowledgeBase(name="产品知识库", visibility="department", owner=user)
    document = Document(
        kb=kb,
        title="manual.txt",
        file_type="txt",
        storage_key="knowledge-bases/1/documents/manual.txt",
        original_filename="产品手册.txt",
        content_type="text/plain",
        file_size=9,
    )
    audit = AuditLog(
        user=user,
        action="download_document",
        target_type="document",
        target_id="1",
        kb_id=1,
        detail="manual.txt",
    )
    session.add_all([role, user, kb, document, audit])
    session.commit()

    saved_document = session.query(Document).one()
    saved_audit = session.query(AuditLog).one()
    assert saved_document.storage_key == "knowledge-bases/1/documents/manual.txt"
    assert saved_document.original_filename == "产品手册.txt"
    assert saved_document.content_type == "text/plain"
    assert saved_document.file_size == 9
    assert saved_audit.target_id == "1"
    assert saved_audit.kb_id == 1
    assert saved_audit.detail == "manual.txt"
    assert saved_audit.created_at is not None
```

- [ ] **步骤 2：运行模型测试验证失败**

运行：`cd backend && pytest tests/test_models_schema.py::test_document_and_audit_log_persist_file_storage_and_download_fields -v`

预期：FAIL，原因是 `Document`/`AuditLog` 构造函数尚不接受新增字段。

- [ ] **步骤 3：为历史记录兼容性实现最小模型字段**

在 `Document` 增加带默认值的字段：

```python
storage_key: Mapped[str] = mapped_column(String(1024), default="")
original_filename: Mapped[str] = mapped_column(String(512), default="")
content_type: Mapped[str] = mapped_column(String(255), default="")
file_size: Mapped[int] = mapped_column(Integer, default=0)
```

在 `AuditLog` 增加：

```python
target_id: Mapped[str] = mapped_column(String(100), default="")
kb_id: Mapped[int | None] = mapped_column(ForeignKey("knowledge_bases.id"), nullable=True)
detail: Mapped[str] = mapped_column(Text, default="")
created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
```

同时为 `AuditLog` 增加可选 `kb` relationship（如模型风格需要），保留现有 `user` relationship。默认空 `storage_key` 表示历史文档不可下载。

- [ ] **步骤 4：运行模型测试验证通过**

运行：`cd backend && pytest tests/test_models_schema.py -v`

预期：PASS，既有 schema 测试和新增字段持久化测试均通过。

- [ ] **步骤 5：提交此任务**

```bash
git add backend/app/models/document.py backend/app/models/kg_ops.py backend/tests/test_models_schema.py
git commit -m "feat: persist document storage metadata and download audit fields"
```

---

### 任务 3：实现统一的数据库文档访问服务

**文件：**
- 创建：`backend/app/services/document_access_service.py`
- 测试：`backend/tests/test_document_access_service.py`

- [ ] **步骤 1：编写无 `can_view` 和 `can_grant` 绕过的失败测试**

```python
from app.services.document_access_service import DocumentAccessService


def test_document_access_requires_knowledge_base_can_view(context):
    session, kb, owner, viewer, document = context
    service = DocumentAccessService(
        kb_service=DbKnowledgeBaseService(session),
        view_rule_service=KnowledgeViewRuleService(session),
    )

    assert service.can_access_document(kb.id, viewer.id, document) is False


def test_can_grant_user_bypasses_restrictive_knowledge_view_rule(context):
    session, kb, owner, viewer, document = context
    kb_service = DbKnowledgeBaseService(session)
    kb_service.set_permission(kb.id, viewer.id, {
        "can_view": True, "can_upload": False, "can_delete": False, "can_grant": True,
    })
    KnowledgeViewRuleService(session).set_rule(
        kb.id, viewer.id, ["售后"], [], [], 1,
    )
    service = DocumentAccessService(kb_service, KnowledgeViewRuleService(session))

    assert service.can_access_document(kb.id, viewer.id, document) is True
```

测试 fixture 中创建 `department="研发"`、`security_level=3` 的 `Document`，确保它本应被规则过滤。

- [ ] **步骤 2：运行测试验证失败**

运行：`cd backend && pytest tests/test_document_access_service.py -v`

预期：FAIL，原因是 `DocumentAccessService` 尚不存在。

- [ ] **步骤 3：补充无规则、规则过滤与列表一致性的失败测试**

```python
def test_no_rule_allows_can_view_user_and_filter_matches_single_document_decision(context):
    session, kb, owner, viewer, allowed, blocked = context
    kb_service = DbKnowledgeBaseService(session)
    kb_service.set_permission(kb.id, viewer.id, {
        "can_view": True, "can_upload": False, "can_delete": False, "can_grant": False,
    })
    rules = KnowledgeViewRuleService(session)
    service = DocumentAccessService(kb_service, rules)

    assert service.can_access_document(kb.id, viewer.id, allowed) is True
    assert service.filter_accessible_documents(kb.id, viewer.id, [allowed, blocked]) == [allowed, blocked]

    rules.set_rule(kb.id, viewer.id, ["售后"], ["P368"], ["internal"], 2)

    assert service.can_access_document(kb.id, viewer.id, allowed) is True
    assert service.can_access_document(kb.id, viewer.id, blocked) is False
    assert service.filter_accessible_documents(kb.id, viewer.id, [allowed, blocked]) == [allowed]
```

- [ ] **步骤 4：运行扩展测试验证失败**

运行：`cd backend && pytest tests/test_document_access_service.py -v`

预期：FAIL，原因是统一访问逻辑缺失。

- [ ] **步骤 5：实现最小统一访问逻辑**

创建：

```python
from app.models import Document
from app.services.db_kb_service import DbKnowledgeBaseService
from app.services.view_rule_service import KnowledgeViewRuleService


class DocumentAccessService:
    def __init__(
        self,
        kb_service: DbKnowledgeBaseService,
        view_rule_service: KnowledgeViewRuleService,
    ) -> None:
        self.kb_service = kb_service
        self.view_rule_service = view_rule_service

    def can_access_document(self, kb_id: int, user_id: int, document: Document) -> bool:
        if document.kb_id != kb_id:
            return False
        if not self.kb_service.has_permission(kb_id, user_id, "can_view"):
            return False
        if self.kb_service.has_permission(kb_id, user_id, "can_grant"):
            return True
        effective_view_rule = self.view_rule_service.get_rule(kb_id, user_id)
        return self.view_rule_service.can_view_document(document, effective_view_rule)

    def filter_accessible_documents(
        self,
        kb_id: int,
        user_id: int,
        documents: list[Document],
    ) -> list[Document]:
        return [
            document
            for document in documents
            if self.can_access_document(kb_id, user_id, document)
        ]
```

这里的局部变量应保留 `effective_view_rule` 命名，明确未来角色、项目、部门规则可以在规则服务层计算后无缝替换；不得把文档访问 API 锁死到用户级表查询。

- [ ] **步骤 6：运行访问服务测试验证通过**

运行：`cd backend && pytest tests/test_document_access_service.py -v`

预期：PASS，所有权限与规则组合通过。

- [ ] **步骤 7：提交此任务**

```bash
git add backend/app/services/document_access_service.py backend/tests/test_document_access_service.py
git commit -m "feat: centralize database document access rules"
```

---

### 任务 4：改造上传和解析链路以复用原文件存储

**文件：**
- 修改：`backend/app/services/db_document_service.py`
- 修改：`backend/app/services/ingestion_service.py`
- 修改：`backend/tests/test_upload_ingest_flow.py`
- 修改：`backend/tests/test_database_mode_api.py`

- [ ] **步骤 1：为数据库文档服务写入存储元数据编写失败测试**

在 `test_upload_ingest_flow.py` 添加：

```python
def test_db_document_service_persists_stored_file_metadata():
    session = build_session()
    role = Role(name="管理员", level=3)
    owner = User(username="admin", password_hash="hash", role=role)
    session.add_all([role, owner])
    session.commit()
    kb = DbKnowledgeBaseService(session).create(name="入库知识库", owner_id=owner.id)

    document = DbDocumentService(session).upload(
        kb_id=kb.id,
        filename="manual.txt",
        content=b"ignored-by-service",
        storage_key="knowledge-bases/1/documents/file.txt",
        original_filename="原始手册.txt",
        content_type="text/plain",
        file_size=9,
    )

    assert document.storage_key == "knowledge-bases/1/documents/file.txt"
    assert document.original_filename == "原始手册.txt"
    assert document.content_type == "text/plain"
    assert document.file_size == 9
```

- [ ] **步骤 2：运行测试验证失败**

运行：`cd backend && pytest tests/test_upload_ingest_flow.py::test_db_document_service_persists_stored_file_metadata -v`

预期：FAIL，`DbDocumentService.upload()` 尚不接受存储元数据关键字参数。

- [ ] **步骤 3：为 ingestion 从 storage key 读取原文件编写失败测试**

```python
def test_ingestion_reads_the_already_saved_original_file():
    session = build_session()
    # 创建 role/user/kb/document，document.storage_key 指向本地存储中的文件。
    with TemporaryDirectory() as tmpdir:
        storage = LocalFileStorageService(Path(tmpdir))
        stored = storage.save(b"SOS alarm", "manual.txt", "text/plain", kb.id)
        document.storage_key = stored.storage_key
        document.original_filename = stored.original_filename
        document.content_type = stored.content_type
        document.file_size = stored.file_size
        session.commit()

        result = IngestionService(session, storage).ingest_uploaded_document(document)

    assert result["block_count"] == 1
    assert session.query(ContentBlock).one().raw_text == "SOS alarm"
```

- [ ] **步骤 4：运行 ingestion 测试验证失败**

运行：`cd backend && pytest tests/test_upload_ingest_flow.py::test_ingestion_reads_the_already_saved_original_file -v`

预期：FAIL，`IngestionService` 当前仍要求 `upload_root` 与 `content` 参数。

- [ ] **步骤 5：实现最小上传元数据和复用存储对象的 ingestion 改造**

1. 扩展 `DbDocumentService.upload()`：新增关键字参数 `storage_key=""`、`original_filename=""`、`content_type=""`、`file_size=0`，并写入 `Document`。保留 `content` 参数以兼容当前调用方，但不在服务层存文件。
2. 将 `IngestionService.__init__` 改为接收 `file_storage: FileStorageService`；将 `stage_uploaded_document(document, content)` 和 `ingest_uploaded_document(document, content)` 改为仅接收 `document`。
3. `stage_uploaded_document()` 必须：

```python
if not document.storage_key or not self.file_storage.exists(document.storage_key):
    raise FileNotFoundError("File not found")

file_path = self.file_storage.path_for(document.storage_key)
```

只创建 `ParseTask`，返回 `task_id`、`storage_key`、`staged_filename=Path(document.storage_key).name`；不再写入第二份文件。将 `LocalFileStorageService.path_for()` 作为具体实现的公共只读路径解析方法，且仍通过安全校验。
4. `ingest_uploaded_document()` 使用该路径调用 `parse_file_to_elements()`，保留现有块与 chunk 写入逻辑。

- [ ] **步骤 6：为 API 上传返回存储字段编写失败测试**

在 `test_database_mode_api.py` 添加：

```python
def test_database_mode_upload_saves_original_file_metadata(tmp_path):
    app = build_database_app()
    app.state.file_storage_root = tmp_path
    client = TestClient(app)
    token = login_default_admin(client)
    kb = client.post("/api/kb", json={"name": "文件库"}).json()

    response = client.post(
        f"/api/kb/{kb['id']}/documents/upload",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("产品手册.txt", b"SOS alarm", "text/plain")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["original_filename"] == "产品手册.txt"
    assert body["content_type"] == "text/plain"
    assert body["file_size"] == 9
    assert body["storage_key"].startswith(f"knowledge-bases/{kb['id']}/documents/")
    assert body["download_available"] is True
    assert (tmp_path / body["storage_key"]).read_bytes() == b"SOS alarm"
```

- [ ] **步骤 7：运行上传 API 测试验证失败**

运行：`cd backend && pytest tests/test_database_mode_api.py::test_database_mode_upload_saves_original_file_metadata -v`

预期：FAIL，应用尚未注入存储服务或返回新增字段。

- [ ] **步骤 8：在应用上传路由中接入存储后再解析**

在 `main.py`：

```python
app.state.file_storage_root = Path("uploads/files")
app.state.document_access_service = None
```

在 `create_app()` 为数据库模式注入：

```python
app.state.document_access_service = DocumentAccessService(
    kb_service=app.state.kb_service,
    view_rule_service=app.state.view_rule_service,
)
```

文件根目录允许测试在 `create_app()` 后通过 `app.state.file_storage_root` 覆盖，因此路由每次应依据该当前状态创建 `LocalFileStorageService`，避免测试写入仓库目录。

上传顺序必须是：

```python
content = await file.read()
storage = LocalFileStorageService(app.state.file_storage_root)
stored = storage.save(
    content=content,
    original_filename=file.filename or "uploaded",
    content_type=file.content_type or "application/octet-stream",
    kb_id=service_kb_id,
)
doc = document_service.upload(
    kb_id=service_kb_id,
    filename=stored.original_filename,
    content=content,
    storage_key=stored.storage_key,
    original_filename=stored.original_filename,
    content_type=stored.content_type,
    file_size=stored.file_size,
    # 现有元数据参数保持原样
)
staged = IngestionService(session=app.state.db_session, file_storage=storage).ingest_uploaded_document(doc)
```

当 `DbDocumentService.upload()` 或解析抛出异常时，删除刚保存的 `stored.storage_key` 并重新抛出，避免留下孤儿文件。成功响应加入 `storage_key`、`original_filename`、`content_type`、`file_size`、`download_available=True`，且不返回绝对路径。

- [ ] **步骤 9：运行上传和解析专项测试验证通过**

运行：

```bash
cd backend && pytest tests/test_upload_ingest_flow.py tests/test_database_mode_api.py -v
```

预期：PASS，既有上传、解析、问答桥接测试及新增稳定存储测试全部通过。

- [ ] **步骤 10：提交此任务**

```bash
git add backend/app/services/db_document_service.py backend/app/services/ingestion_service.py backend/app/main.py backend/tests/test_upload_ingest_flow.py backend/tests/test_database_mode_api.py
git commit -m "feat: store originals before document ingestion"
```

---

### 任务 5：实现受控文档列表、详情、下载和删除一致性

**文件：**
- 修改：`backend/app/services/db_document_service.py`
- 修改：`backend/app/main.py`
- 创建：`backend/tests/test_document_download_api.py`
- 修改：`backend/tests/test_document_detail_api.py`

- [ ] **步骤 1：为数据库模式列表按规则隐藏文档编写失败测试**

在新文件中建立数据库应用 fixture。fixture 明确返回一个命名结构（例如 `SimpleNamespace`），字段为 `client`、`session`、`file_root`、`kb`、`owner`、`viewer`、`admin_token`、`viewer_token`、`allowed`、`blocked`：管理员创建知识库，上传两篇不同部门/产品线/密级的文档，授权普通用户 `can_view`，再设置仅允许其中一篇的视图规则。

```python
def test_document_list_only_returns_documents_visible_to_regular_user(context):
    client, kb, admin_token, viewer, viewer_token, allowed, blocked = context
    client.put(
        f"/api/kb/{kb['id']}/view-rules/{viewer.id}",
        headers=auth(admin_token),
        json={
            "allowed_departments": ["售后"],
            "allowed_product_lines": ["P368"],
            "allowed_visibilities": ["internal"],
            "max_security_level": 2,
        },
    )

    response = client.get(
        f"/api/kb/{kb['id']}/documents",
        headers=auth(viewer_token),
    )

    assert response.status_code == 200
    assert [item["id"] for item in response.json()["items"]] == [allowed["id"]]
    assert response.json()["items"][0]["download_available"] is True
```

- [ ] **步骤 2：运行列表测试验证失败**

运行：`cd backend && pytest tests/test_document_download_api.py::test_document_list_only_returns_documents_visible_to_regular_user -v`

预期：FAIL，当前列表只按 `can_view` 返回所有文档且没有 `download_available`。

- [ ] **步骤 3：为详情/下载 403、历史文档 404 和管理员绕过编写失败测试**

```python
def test_restricted_document_detail_and_download_return_403_for_regular_user(context):
    client, kb, _, viewer, viewer_token, _, blocked = context

    detail = client.get(f"/api/kb/{kb['id']}/documents/{blocked['id']}", headers=auth(viewer_token))
    download = client.get(f"/api/kb/{kb['id']}/documents/{blocked['id']}/download", headers=auth(viewer_token))

    assert detail.status_code == 403
    assert download.status_code == 403
    assert detail.json()["detail"] == "Permission denied"
    assert download.json()["detail"] == "Permission denied"


def test_can_grant_user_can_download_document_outside_own_view_rule(context):
    client, kb, admin_token, _, _, _, blocked = context

    response = client.get(
        f"/api/kb/{kb['id']}/documents/{blocked['id']}/download",
        headers=auth(admin_token),
    )

    assert response.status_code == 200
    assert response.content == b"blocked document"


def test_download_returns_404_for_legacy_document_without_storage_key(context):
    client, kb, admin_token, _, _, _, _ = context
    legacy = create_document_without_storage_key(context)

    response = client.get(
        f"/api/kb/{kb['id']}/documents/{legacy.id}/download",
        headers=auth(admin_token),
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "File not found"}
```

- [ ] **步骤 4：运行详情和下载测试验证失败**

运行：`cd backend && pytest tests/test_document_download_api.py -v`

预期：FAIL，下载路由不存在，且详情尚未使用 `DocumentAccessService`。

- [ ] **步骤 5：为成功下载响应和审计写入编写失败测试**

```python
def test_successful_download_returns_attachment_and_writes_audit_log(context):
    client, kb, admin_token, owner, _, allowed, _ = context

    response = client.get(
        f"/api/kb/{kb['id']}/documents/{allowed['id']}/download",
        headers=auth(admin_token),
    )

    assert response.status_code == 200
    assert response.content == b"allowed document"
    assert response.headers["content-type"].startswith("text/plain")
    assert "attachment" in response.headers["content-disposition"]
    audit = context.session.query(AuditLog).one()
    assert audit.user_id == owner.id
    assert audit.action == "download_document"
    assert audit.target_type == "document"
    assert audit.target_id == str(allowed["id"])
    assert audit.kb_id == kb["id"]
```

- [ ] **步骤 6：运行成功下载测试验证失败**

运行：`cd backend && pytest tests/test_document_download_api.py::test_successful_download_returns_attachment_and_writes_audit_log -v`

预期：FAIL，下载路由和审计写入缺失。

- [ ] **步骤 7：实现列表与详情统一授权和响应序列化**

在数据库模式中：

1. 从登录 session 解析 `user_id`；列表仍先执行 `require_kb_permission(..., "can_view")`，再使用：

```python
access_service.filter_accessible_documents(service_kb_id, int(user_id), items)
```

2. 将列表项统一序列化为：

```python
{
    "id": item.id,
    "kb_id": item.kb_id,
    "title": item.title,
    "file_type": item.file_type,
    "status": item.status,
    "department": item.department,
    "product_line": item.product_line,
    "visibility": item.visibility,
    "security_level": item.security_level,
    "tags": item.tags,
    "original_filename": item.original_filename,
    "content_type": item.content_type,
    "file_size": item.file_size,
    "download_available": bool(item.storage_key and storage.exists(item.storage_key)),
}
```

3. 详情定位文档后，在数据库模式调用 `access_service.can_access_document(...)`。若为假，抛 `HTTPException(403, "Permission denied")`；仅文档不存在返回 404。详情响应也加入 `original_filename`、`content_type`、`file_size` 与实时的 `download_available`。
4. 内存模式保持当前可用行为，仅可在其现有字典的 `file_size` 上继续返回；不得为内存数据提供伪下载 URL。

- [ ] **步骤 8：实现附件下载和成功审计**

在 `main.py` 导入：

```python
from fastapi.responses import FileResponse, HTMLResponse
from app.models import AuditLog
```

新增 `GET /api/kb/{kb_id}/documents/{doc_id}/download`：

```python
item = app.state.document_service.get(service_kb_id, service_doc_id)
if item is None:
    raise HTTPException(status_code=404, detail="Document not found")
if app.state.service_mode != "database":
    raise HTTPException(status_code=404, detail="File not found")
if not app.state.document_access_service.can_access_document(service_kb_id, int(user_id), item):
    raise HTTPException(status_code=403, detail="Permission denied")
storage = LocalFileStorageService(app.state.file_storage_root)
if not item.storage_key or not storage.exists(item.storage_key):
    raise HTTPException(status_code=404, detail="File not found")

app.state.db_session.add(AuditLog(
    user_id=int(user_id),
    action="download_document",
    target_type="document",
    target_id=str(item.id),
    kb_id=item.kb_id,
    detail=item.original_filename or item.title,
))
app.state.db_session.commit()
return FileResponse(
    path=storage.path_for(item.storage_key),
    media_type=item.content_type or "application/octet-stream",
    filename=item.original_filename or item.title,
)
```

审计只在全部鉴权和文件存在检查成功后写入。确保当前用户无 `can_view` 时依然在定位详情之前由知识库权限检查返回 403；规则拒绝时详情/下载同样返回 403。

- [ ] **步骤 9：为删除先删原文件、失败不删记录编写失败测试**

```python
def test_delete_document_removes_original_storage_object_after_permission_check(context):
    client, kb, admin_token, _, _, allowed, _ = context
    storage_path = context.file_root / allowed["storage_key"]

    response = client.delete(
        f"/api/kb/{kb['id']}/documents/{allowed['id']}",
        headers=auth(admin_token),
    )

    assert response.status_code == 200
    assert response.json() == {"deleted": True}
    assert storage_path.exists() is False
```

- [ ] **步骤 10：运行删除测试验证失败**

运行：`cd backend && pytest tests/test_document_download_api.py::test_delete_document_removes_original_storage_object_after_permission_check -v`

预期：FAIL，数据库文档服务尚无完整删除，路由未同步清理存储对象。

- [ ] **步骤 11：实现数据库删除一致性**

1. 在 `DbDocumentService` 增加：

```python
def delete(self, kb_id: int, doc_id: int) -> Document | None:
    document = self.get(kb_id, doc_id)
    if document is None:
        return None
    kb = self.session.get(KnowledgeBase, kb_id)
    if kb is not None:
        kb.doc_count = max(0, kb.doc_count - 1)
    self.session.delete(document)
    self.session.commit()
    return document
```

2. 在删除路由的数据库分支先定位 document；若有 `storage_key` 且文件存在，先调用 `storage.delete()`，仅成功后执行 `document_service.delete()`。若存储删除抛异常，返回 `500` 且不删除数据库记录。无 `storage_key` 的历史文档保留既有删除能力。

- [ ] **步骤 12：运行文档访问、下载与详情测试验证通过**

运行：

```bash
cd backend && pytest tests/test_document_download_api.py tests/test_document_detail_api.py tests/test_document_api.py -v
```

预期：PASS，授权过滤、403/404 语义、附件头、审计、删除一致性及既有文档 API 行为全部通过。

- [ ] **步骤 13：提交此任务**

```bash
git add backend/app/services/db_document_service.py backend/app/main.py backend/tests/test_document_download_api.py backend/tests/test_document_detail_api.py
git commit -m "feat: add permissioned document download APIs"
```

---

### 任务 6：实现用户侧 `/documents` 文档库页面与导航

**文件：**
- 创建：`backend/app/static/documents.html`
- 创建：`backend/app/static/documents.js`
- 修改：`backend/app/static/admin.html`
- 修改：`backend/app/static/qa.html`
- 修改：`backend/app/main.py`
- 修改：`backend/tests/test_frontend_shell.py`

- [ ] **步骤 1：为页面路由和静态节点写失败测试**

在 `test_frontend_shell.py` 添加：

```python
def test_documents_page_contains_kb_selector_document_list_detail_and_download_hook():
    client = TestClient(create_app())

    response = client.get("/documents")

    assert response.status_code == 200
    assert 'id="documents-kb-list"' in response.text
    assert 'id="documents-list"' in response.text
    assert 'id="documents-detail"' in response.text
    assert 'id="download-document"' in response.text
    script = client.get("/static/documents.js")
    assert script.status_code == 200
    assert "fetch(" in script.text
    assert "URL.createObjectURL" in script.text
    assert "Authorization" in script.text
```

- [ ] **步骤 2：运行页面测试验证失败**

运行：`cd backend && pytest tests/test_frontend_shell.py::test_documents_page_contains_kb_selector_document_list_detail_and_download_hook -v`

预期：FAIL，`/documents` 路由、HTML 和 JS 尚不存在。

- [ ] **步骤 3：为管理页与问答页导航链接写失败测试**

```python
def test_admin_and_qa_shells_link_to_documents_page():
    client = TestClient(create_app())

    assert 'href="/documents"' in client.get("/admin").text
    assert 'href="/documents"' in client.get("/qa").text
```

- [ ] **步骤 4：运行导航测试验证失败**

运行：`cd backend && pytest tests/test_frontend_shell.py::test_admin_and_qa_shells_link_to_documents_page -v`

预期：FAIL，两个页面尚未提供文档库入口。

- [ ] **步骤 5：实现最小页面结构与路由**

1. 在 `main.py` 增加：

```python
DOCUMENTS_HTML = STATIC_DIR / "documents.html"

@app.get("/documents", response_class=HTMLResponse)
def documents_page() -> str:
    return DOCUMENTS_HTML.read_text(encoding="utf-8")
```

保持路由函数置于 `/admin` 和 `/qa` 页面路由旁。

2. 创建 `documents.html`，至少含：

```html
<h1>我的文档库</h1>
<p><a href="/qa">进入问答页</a> <a href="/admin">进入管理台</a></p>
<p id="documents-message"></p>
<section id="documents-kb-list"></section>
<p id="documents-kb-empty-state">暂无可访问知识库。</p>
<section id="documents-list"></section>
<p id="documents-empty-state">当前知识库暂无可访问文档。</p>
<section id="documents-detail">
  <h2 id="documents-detail-title">未选择文档</h2>
  <p id="documents-detail-metadata"></p>
  <button id="download-document" type="button" disabled>下载原文件</button>
</section>
<script src="/static/documents.js"></script>
```

3. 在 `admin.html` 与 `qa.html` 顶部导航区域增加精确链接：`<a href="/documents">进入文档库</a>`。

- [ ] **步骤 6：实现令牌认证、受过滤列表和 Blob 下载交互**

`documents.js` 使用已有静态壳的令牌约定：

```javascript
function authorizationHeaders() {
  const token = localStorage.getItem('session_token')
  return token ? { Authorization: `Bearer ${token}` } : {}
}
```

实现以下函数：

```javascript
async function loadKnowledgeBases() { /* GET /api/kb，渲染知识库按钮 */ }
async function loadDocuments() { /* GET /api/kb/{id}/documents，渲染可见文档 */ }
async function loadDocumentDetail(docId) { /* GET 详情，更新按钮 disabled */ }
async function downloadSelectedDocument() {
  const response = await fetch(`/api/kb/${activeKbId}/documents/${selectedDocumentId}/download`, {
    headers: authorizationHeaders(),
  })
  if (!response.ok) { /* 显示后端 detail */ return }
  const blob = await response.blob()
  const objectUrl = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = objectUrl
  link.download = selectedDocument?.original_filename || selectedDocument?.title || 'download'
  link.click()
  URL.revokeObjectURL(objectUrl)
}
```

下载按钮仅在当前详情的 `download_available === true` 时启用。请求失败时不得将错误响应当作 Blob 下载；将 `detail` 写入 `#documents-message`。

- [ ] **步骤 7：运行前端壳测试验证通过**

运行：`cd backend && pytest tests/test_frontend_shell.py -v`

预期：PASS，现有登录、管理、问答壳测试及新增文档库/导航测试均通过。

- [ ] **步骤 8：提交此任务**

```bash
git add backend/app/main.py backend/app/static/documents.html backend/app/static/documents.js backend/app/static/admin.html backend/app/static/qa.html backend/tests/test_frontend_shell.py
git commit -m "feat: add user document library page"
```

---

### 任务 7：同步接口与技术实现文档并执行完整回归

**文件：**
- 修改：`docs/api/api-reference.md`
- 修改：`docs/implementation/tech-code-mapping.md`
- 参考：`docs/superpowers/specs/2026-07-11-permission-document-library-download-design.md`

- [ ] **步骤 1：为 API 文档补充文档访问与下载章节**

在 `docs/api/api-reference.md` 的文档 API 区域更新并明确：

1. `GET /api/kb/{kb_id}/documents`：需要 Bearer token 和 `can_view`；普通用户按有效视图规则过滤，`can_grant` 返回全量；每项含元数据、`original_filename`、`content_type`、`file_size`、`download_available`。
2. `GET /api/kb/{kb_id}/documents/{doc_id}`：文档不存在 404；知识库无权或规则不匹配均为 `403 Permission denied`；含 `download_available`。
3. 新增 `GET /api/kb/{kb_id}/documents/{doc_id}/download`：附件响应、`Content-Disposition`、授权决策、成功下载审计；无原文件或历史 `storage_key` 为空时 `404 File not found`。
4. `POST /documents/upload` 的实际路径条目中加入 `storage_key`（仅内部相对键）、`original_filename`、`content_type`、`file_size`、`download_available`，并声明不返回服务器绝对路径。
5. 页面路由章节记录 `GET /documents`。

- [ ] **步骤 2：同步技术代码映射**

在 `docs/implementation/tech-code-mapping.md`：

1. 文档元数据行补充原始文件存储字段和下载可用状态。
2. 新增 `DocumentAccessService` 行，说明其统一复用 `can_view`、`can_grant` 和最终有效视图规则，后续 RAG/图谱也应复用该边界。
3. 新增 `FileStorageService`/`LocalFileStorageService` 行，标注一期本地实现及未来 MinIO 替换点。
4. 用户文档库行记录 `/documents`、`documents.html`、`documents.js` 的能力与限制。
5. 测试表加入 `test_file_storage.py`、`test_document_access_service.py`、`test_document_download_api.py`。
6. 更新上传链路图：

```text
UploadFile → LocalFileStorageService → Document(storage metadata) → IngestionService(read saved original) → ParseTask → ContentBlock → DocumentChunk
```

- [ ] **步骤 3：检查文档内容与既有规格一致**

逐项人工核对以下不可变行为：

```text
列表隐藏无权文档
详情/下载无权返回 403
can_grant 绕过知识视图规则
无规则默认允许
历史文档无 storage_key 时下载 404 File not found
成功下载才写 download_document 审计
存储键为相对路径，不泄露服务器绝对路径
```

- [ ] **步骤 4：运行全部后端回归**

运行：`cd backend && pytest -q`

预期：PASS，全部测试通过；若失败，保留失败输出并按 `systematic-debugging` 从根因调查开始处理，不通过则不得标记任务完成。

- [ ] **步骤 5：进行端到端运行态验证**

运行应用并按实际项目启动方式验证以下流程：

```text
管理员登录 → 创建知识库 → 上传带元数据文档 → 访问 /documents → 选择知识库 → 查看详情 → 下载文件
```

再创建一个仅有 `can_view` 的测试用户并配置限制规则，确认：列表看不到规则外文档、直接请求详情和下载都得到 403。验证后停止本地服务。

- [ ] **步骤 6：提交文档与最终验证改动**

```bash
git add docs/api/api-reference.md docs/implementation/tech-code-mapping.md
git commit -m "docs: document permissioned library and download APIs"
```

---

## 完成核对

- [ ] 新上传文件保存稳定相对 `storage_key`，并保存原始文件名、MIME 类型和大小。
- [ ] 本地存储实现覆盖保存、读取、存在性、删除、重名和路径安全。
- [ ] 上传解析读取已保存原文件，不产生第二份解析暂存副本。
- [ ] 列表、详情和下载全部复用同一 `DocumentAccessService` 决策。
- [ ] 普通用户的列表按视图规则隐藏文档；直接详情/下载规则外文档返回 403。
- [ ] `can_grant` 用户可绕过规则访问全部文档。
- [ ] 无原始文件的历史文档详情仍可查看，下载返回 404 File not found。
- [ ] 下载使用附件响应、正确 MIME 类型和文件名，并且只对成功下载写审计。
- [ ] 删除新文档先删除存储对象，失败时不删除数据库文档。
- [ ] `/documents` 页面能加载、查看和下载用户可访问的文件，并在 `/admin`、`/qa` 提供入口。
- [ ] API 文档、技术映射与完整测试回归均已更新并验证。
