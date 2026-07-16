# 真实样本权限工作台实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 `superpowers:subagent-driven-development`（推荐）或 `superpowers:executing-plans` 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 用一条显式命令将 16 份真实 MCSTARS 样本和 5 个分级演示用户导入 SQLite，并在统一深色运营工作台中验证按权限浏览、下载和管理员授权流程。

**架构：** 新建 `app/demo` 模块承载不可变样本映射和可测试的导入器，CLI 仅负责参数、临时解压和报告。数据库登录在应用数据库模式中校验演示用户身份，文档序列化将解析能力作为显式 API 契约；DOCX/PDF 沿用 `IngestionService`，PPTX/XLSX 只保存原件并标记 `stored_unsupported`。共享 CSS 作为静态页面的唯一视觉令牌入口，`/documents` 复用既有授权 API 组装权限测试工作台。

**技术栈：** Python 3.10、FastAPI、SQLAlchemy、SQLite、pytest、标准库 `argparse`/`zipfile`/`tempfile`、原生 HTML/CSS/JavaScript。

---

## 文件结构与职责

| 文件 | 变更 | 职责 |
|---|---|---|
| `backend/app/demo/__init__.py` | 创建 | 标记可导入的演示数据模块。 |
| `backend/app/demo/sample_catalog.py` | 创建 | 定义 16 个受控文件名、元数据和五个用户/权限规则。 |
| `backend/app/demo/seed_service.py` | 创建 | 初始化、重置、导入、解析和报告的可测试业务服务。 |
| `backend/scripts/__init__.py` | 创建 | 允许通过 `python -m scripts.seed_demo_data` 运行脚本。 |
| `backend/scripts/seed_demo_data.py` | 创建 | 解析命令行参数、建立数据库/目录、调用导入服务并输出报告。 |
| `backend/app/services/auth_service.py` | 修改 | 在不改变内存模式 `admin/admin` 的前提下，支持数据库用户认证。 |
| `backend/app/main.py` | 修改 | 注入数据库登录回调、稳定序列化解析状态，并让上传端点保留不支持格式。 |
| `backend/app/services/ingestion_service.py` | 修改 | 明确解析成功/不支持格式时的任务和文档状态，不将不支持格式伪装成错误。 |
| `backend/app/static/app.css` | 创建 | 深色运营工作台的设计令牌、布局、组件和响应式样式。 |
| `backend/app/static/login.html` | 修改 | 统一登录卡片、演示用户名快捷选择和共享样式入口。 |
| `backend/app/static/login.js` | 创建 | 登录提交、演示用户名填充和成功后跳转工作台。 |
| `backend/app/static/documents.html` | 修改 | 工作台顶部、知识库/筛选栏、文档清单、详情下载和管理员权限区。 |
| `backend/app/static/documents.js` | 修改 | 登录态、筛选、文档详情、下载、管理员权限/知识视图交互。 |
| `backend/app/static/admin.html` | 修改 | 保留现有 DOM/API，接入共享视觉结构和样式。 |
| `backend/app/static/qa.html` | 修改 | 保留现有 DOM/API，接入共享视觉结构和样式。 |
| `backend/tests/test_demo_seed_service.py` | 创建 | 以轻量 ZIP fixture 测试目录契约、导入、重置、格式状态和报告。 |
| `backend/tests/test_demo_seed_cli.py` | 创建 | 测试 CLI 成功、重复导入拒绝和缺失 ZIP 错误。 |
| `backend/tests/test_database_auth_api.py` | 创建 | 测试数据库模式演示用户登录、身份返回和错误密码。 |
| `backend/tests/test_document_download_api.py` | 修改 | 覆盖 `stored_unsupported` 的可下载性和响应字段。 |
| `backend/tests/test_frontend_shell.py` | 修改 | 断言共享样式、登录脚本和工作台结构/管理员钩子。 |
| `docs/api/api-reference.md` | 修改 | 记录解析状态字段和演示初始化的开发使用方式。 |
| `docs/implementation/tech-code-mapping.md` | 修改 | 映射演示初始化、数据库认证、工作台和测试。 |
| `docs/implementation/demo-sample-acceptance.md` | 创建 | 记录实际 ZIP 初始化、运行服务和五账户手工验收清单。 |

---

### 任务 1：定义受控样本目录与演示身份配置

**文件：**
- 创建：`backend/app/demo/__init__.py`
- 创建：`backend/app/demo/sample_catalog.py`
- 测试：`backend/tests/test_demo_seed_service.py`

- [ ] **步骤 1：编写样本目录完整性失败测试**

```python
from app.demo.sample_catalog import DEMO_USERS, SAMPLE_DOCUMENTS


def test_sample_catalog_covers_exactly_the_real_archive_contract():
    filenames = {sample.filename for sample in SAMPLE_DOCUMENTS}

    assert len(SAMPLE_DOCUMENTS) == 16
    assert len(filenames) == 16
    assert {sample.filename.rsplit('.', 1)[1].lower() for sample in SAMPLE_DOCUMENTS} == {
        "docx", "pdf", "pptx", "xlsx",
    }
    assert {user.username for user in DEMO_USERS} == {
        "admin", "public_viewer", "delivery_viewer", "ops_viewer", "blank_user",
    }
```

- [ ] **步骤 2：运行测试确认失败**

运行：`cd backend && pytest tests/test_demo_seed_service.py::test_sample_catalog_covers_exactly_the_real_archive_contract -v`

预期：FAIL，报错 `ModuleNotFoundError: No module named 'app.demo'`。

- [ ] **步骤 3：实现不可变目录数据结构和完整映射**

创建包和目录对象。使用不可变 dataclass，避免导入时通过文件名猜测分级：

```python
# backend/app/demo/sample_catalog.py
from dataclasses import dataclass

DEMO_PASSWORD = "Demo123!"
DEMO_KB_NAME = "MCSTARS 产品知识库"

@dataclass(frozen=True)
class SampleDocument:
    filename: str
    department: str
    product_line: str
    visibility: str
    security_level: int
    scope: str
    document_type: str
    product: str
    priority: str
    tags: str

    @property
    def parse_supported(self) -> bool:
        return self.filename.lower().endswith((".docx", ".pdf"))

@dataclass(frozen=True)
class DemoUser:
    username: str
    role_name: str
    can_view: bool
    can_upload: bool
    can_delete: bool
    can_grant: bool
    allowed_departments: tuple[str, ...] = ()
    allowed_product_lines: tuple[str, ...] = ()
    allowed_visibilities: tuple[str, ...] = ()
    max_security_level: int | None = None
```

填入规格所列的 16 个精确 ZIP 文件名及元数据。其中三份 `pptx/xlsx` 分别是“产品介绍_MCSTARS产品介绍V1.1.1.pptx”“功能清单_MiniServer全平台功能清单V2.3.xlsx”“解决方案_MiniServer产品解决方案.pptx”。填入 5 个用户的权限和知识视图；`blank_user` 全部权限均为 `False`。`delivery_viewer` 仅允许部门“市场”“交付”，`ops_viewer` 仅允许部门“运维”。

- [ ] **步骤 4：运行目录测试确认通过**

运行：`cd backend && pytest tests/test_demo_seed_service.py::test_sample_catalog_covers_exactly_the_real_archive_contract -v`

预期：PASS。

- [ ] **步骤 5：提交任务**

```bash
git add backend/app/demo/__init__.py backend/app/demo/sample_catalog.py backend/tests/test_demo_seed_service.py
git commit -m "feat: define real sample demo catalog"
```

---

### 任务 2：实现数据库演示身份认证

**文件：**
- 修改：`backend/app/services/auth_service.py`
- 修改：`backend/app/main.py`
- 创建：`backend/tests/test_database_auth_api.py`
- 修改：`backend/tests/test_auth_api.py`

- [ ] **步骤 1：为数据库用户登录写失败测试**

```python
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.db import Base
from app.models import Role, User
from app.main import create_app


def test_database_mode_logs_in_seeded_user_and_returns_database_identity(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'demo.db'}")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    role = Role(name="演示用户", level=1)
    session.add_all([role, User(username="public_viewer", password_hash="Demo123!", role=role)])
    session.commit()
    client = TestClient(create_app(mode="database", session=session))

    login = client.post("/api/auth/login", json={"username": "public_viewer", "password": "Demo123!"})
    profile = client.get("/api/auth/me", headers={"Authorization": f"Bearer {login.json()['token']}"})

    assert login.status_code == 200
    assert profile.json()["username"] == "public_viewer"
    assert profile.json()["user_id"].isdigit()
```

另加错误密码返回 `401` 的测试，并保留现有内存模式 `admin/admin` 测试。

- [ ] **步骤 2：运行认证测试确认失败**

运行：`cd backend && pytest tests/test_database_auth_api.py -v`

预期：FAIL，数据库模式的 `AuthService` 尚未查询 `User`。

- [ ] **步骤 3：实现显式数据库身份查找回调**

将 `AuthService` 改为接收可选认证函数，保持内存模式行为不变：

```python
class AuthService:
    def __init__(self, session_store, authenticate=None):
        self.session_store = session_store
        self.authenticate = authenticate

    def login(self, username, password):
        if self.authenticate is not None:
            user = self.authenticate(username, password)
            if user is None:
                return None
            return self.session_store.create(user_id=str(user.id), username=user.username)
        if username == "admin" and password == "admin":
            return self.session_store.create(user_id="admin", username=username)
        return None
```

在 `create_app()` 的数据库分支注入一个只查询当前 `app.state.db_session` 的认证函数：用户名精确匹配且 `password_hash == password` 时返回用户。此阶段密码字段是本地演示数据的明文比较，必须在函数和文档注明仅用于当前无加密迁移的演示环境；不得影响内存模式的 `admin/admin`。

- [ ] **步骤 4：运行认证回归测试确认通过**

运行：`cd backend && pytest tests/test_auth_api.py tests/test_database_auth_api.py -v`

预期：PASS。

- [ ] **步骤 5：提交任务**

```bash
git add backend/app/services/auth_service.py backend/app/main.py backend/tests/test_auth_api.py backend/tests/test_database_auth_api.py
git commit -m "feat: authenticate database demo users"
```

---

### 任务 3：使解析状态成为稳定的 API 契约

**文件：**
- 修改：`backend/app/main.py`
- 修改：`backend/app/services/ingestion_service.py`
- 修改：`backend/tests/test_document_download_api.py`
- 修改：`backend/tests/test_database_mode_api.py`

- [ ] **步骤 1：为仅可下载文件写失败 API 测试**

```python
def test_stored_unsupported_document_is_downloadable_but_not_parse_available(database_client):
    client, token, kb, document, storage = database_client
    document.status = "stored_unsupported"
    stored = storage.save(b"pptx bytes", "产品介绍.pptx", "application/vnd.openxmlformats-officedocument.presentationml.presentation", kb.id)
    document.storage_key = stored.storage_key
    document.original_filename = stored.original_filename
    document.content_type = stored.content_type
    document.file_size = stored.file_size
    client.app.state.db_session.commit()

    detail = client.get(f"/api/kb/{kb.id}/documents/{document.id}", headers={"Authorization": f"Bearer {token}"})
    download = client.get(f"/api/kb/{kb.id}/documents/{document.id}/download", headers={"Authorization": f"Bearer {token}"})

    assert detail.json()["parse_available"] is False
    assert detail.json()["parse_status_label"] == "仅可下载（暂不支持内容解析）"
    assert detail.json()["download_available"] is True
    assert download.status_code == 200
    assert download.content == b"pptx bytes"
```

再为 `parsed` 文档断言 `parse_available=True`、标签为“已解析，可用于问答”。

- [ ] **步骤 2：运行新增测试确认失败**

运行：`cd backend && pytest tests/test_document_download_api.py -v`

预期：FAIL，响应尚未包含 `parse_available` 与 `parse_status_label`。

- [ ] **步骤 3：集中生成解析能力字段，并确保不支持格式状态不回滚**

在 `main.py` 定义纯函数：

```python
def parse_state_payload(status: str) -> dict[str, bool | str]:
    if status == "parsed":
        return {"parse_available": True, "parse_status_label": "已解析，可用于问答"}
    if status == "stored_unsupported":
        return {"parse_available": False, "parse_status_label": "仅可下载（暂不支持内容解析）"}
    if status == "pending":
        return {"parse_available": False, "parse_status_label": "等待处理"}
    return {"parse_available": False, "parse_status_label": "解析未完成"}
```

让 `serialize_database_document()` 合并该返回值。在上传路径中，对 `.pptx`、`.xlsx` 调用存储和 `DbDocumentService.upload()` 后直接设置 `doc.status = "stored_unsupported"`、提交并返回 `block_count=0`、`chunk_count=0`；不得调用 `IngestionService`，不得删除存储文件。DOCX/PDF 继续调用 `IngestionService`，解析成功时将 `document.status = "parsed"` 后再提交。

在 `IngestionService.ingest_uploaded_document()` 中只处理支持格式；当解析器抛出不支持格式异常时设置 `task.status = "unsupported"` 并返回零计数，而不是使调用者把保存成功的文档当成失败。上传路由只为已明确支持的 DOCX/PDF 调用它。

- [ ] **步骤 4：运行文档 API 测试确认通过**

运行：`cd backend && pytest tests/test_database_mode_api.py tests/test_document_download_api.py tests/test_upload_ingest_flow.py -v`

预期：PASS。

- [ ] **步骤 5：提交任务**

```bash
git add backend/app/main.py backend/app/services/ingestion_service.py backend/tests/test_document_download_api.py backend/tests/test_database_mode_api.py
git commit -m "feat: expose document parse availability"
```

---

### 任务 4：实现可重置的真实样本导入服务和 CLI

**文件：**
- 创建：`backend/app/demo/seed_service.py`
- 创建：`backend/scripts/__init__.py`
- 创建：`backend/scripts/seed_demo_data.py`
- 测试：`backend/tests/test_demo_seed_service.py`
- 测试：`backend/tests/test_demo_seed_cli.py`

- [ ] **步骤 1：创建只含 16 个目录文件名的 ZIP fixture 和失败导入测试**

在 fixture 中通过 `ZipFile.writestr()` 创建受控 ZIP；DOCX/PDF 文件内容可以是测试用的最小字节，由测试替换解析调用，以避免依赖大型真实包。

```python
def test_seed_service_creates_users_documents_and_unsupported_downloadable_files(tmp_path, monkeypatch):
    archive = build_catalog_archive(tmp_path / "samples.zip")
    service = build_demo_seed_service(tmp_path)
    monkeypatch.setattr("app.demo.seed_service.parse_file_to_elements", lambda path: [{"type": "text", "content": "sample", "source_name": path.name}])

    report = service.seed(archive)

    assert report.total_documents == 16
    assert report.parsed_documents == 13
    assert report.stored_unsupported_documents == 3
    assert report.created_users == 5
    assert {document.status for document in service.session.query(Document).all()} == {"parsed", "stored_unsupported"}
    assert all(document.storage_key for document in service.session.query(Document).all())
```

- [ ] **步骤 2：运行导入测试确认失败**

运行：`cd backend && pytest tests/test_demo_seed_service.py -v`

预期：FAIL，`DemoSeedService` 不存在。

- [ ] **步骤 3：实现明确、幂等且受控的导入服务**

实现服务构造函数接收 `Session`、`LocalFileStorageService`。定义：

```python
@dataclass(frozen=True)
class SeedReport:
    knowledge_base_id: int
    total_documents: int
    parsed_documents: int
    stored_unsupported_documents: int
    created_users: int
    user_ids: dict[str, int]
```

`seed(archive: Path, reset: bool = False) -> SeedReport` 按此顺序：

1. 检查 archive 存在；读取 ZIP 的非目录文件名集合必须与 `SAMPLE_DOCUMENTS` 精确相等；否则抛出 `ValueError`，列出缺失和多余文件名；
2. 若存在同名 `DEMO_KB_NAME`，且没有 `reset`，抛出 `RuntimeError("Demo data already exists; rerun with --reset")`；
3. 有 `reset` 时只删除该知识库下的文档、解析任务、块、chunk、权限、规则和已保存 storage key，并只删除 `DEMO_USERS` 中用户名的用户；不得批量清空其他库或用户；
4. 创建“演示管理员”“演示查看者”两个 `Role`（若不存在），再创建 5 个 `User`，`password_hash=DEMO_PASSWORD`；
5. 使用 `DbKnowledgeBaseService.create()` 创建 `DEMO_KB_NAME`，管理员作为 owner；为其余允许访问用户使用 `set_permission()` 写 `can_view=True`，其他能力为 `False`；为有规则的用户使用 `KnowledgeViewRuleService.set_rule()` 写入目录中的规则；
6. 用 `TemporaryDirectory()` 解压每个条目。读取字节后先调用 `storage.save()`，随后使用 `DbDocumentService.upload()` 写全部目录元数据；
7. 支持解析时调用 `IngestionService`，并写 `document.status="parsed"`；不支持格式时写 `document.status="stored_unsupported"`，无 ParseTask/blocks/chunks；每份完成后提交；
8. 返回聚合报告。

每个已保存文件后续异常都必须删除它并回滚当前数据库事务，不能让部分失败文件继续留在 storage。

- [ ] **步骤 4：补充重置隔离、规则可见范围和 CLI 失败测试**

```python
def test_seed_without_reset_refuses_duplicates_and_reset_keeps_unrelated_data(tmp_path):
    service = build_demo_seed_service(tmp_path)
    archive = build_catalog_archive(tmp_path / "samples.zip")
    unrelated = service.kb_service.create(name="不要删除", owner_id=create_unrelated_user(service.session).id)

    service.seed(archive)
    with pytest.raises(RuntimeError, match="rerun with --reset"):
        service.seed(archive)

    report = service.seed(archive, reset=True)

    assert report.total_documents == 16
    assert service.kb_service.get(unrelated.id).name == "不要删除"


def test_seeded_viewer_rules_produce_expected_access_ranges(tmp_path):
    report, session, kb = seed_fixture(tmp_path)
    access = DocumentAccessService(DocumentFilterService(DbKnowledgeBaseService(session), KnowledgeViewRuleService(session)))
    documents = DbDocumentService(session).list(kb.id)

    visible = {doc.title for doc in access.filter_accessible_documents(kb.id, report.user_ids["public_viewer"], documents)}
    assert len(visible) == 4
```

CLI 测试以 subprocess 或直接 `main([...])` 验证成功输出含 `16`、`13`、`3`，缺失 archive 返回 2 且不创建数据库。

- [ ] **步骤 5：实现 CLI 参数、数据库创建和稳定报告**

```python
parser.add_argument("--archive", type=Path, required=True)
parser.add_argument("--database", required=True)
parser.add_argument("--storage-root", type=Path)
parser.add_argument("--reset", action="store_true")
```

CLI 使用 `create_engine(database_url)`、`Base.metadata.create_all(engine)`、`ensure_runtime_schema(engine)` 和 `sessionmaker(bind=engine)`。默认 `storage_root` 为 SQLite 数据库文件同级的 `demo-files`；若 database 不是 SQLite URL，则要求显式 `--storage-root`。成功输出：

```text
知识库：MCSTARS 产品知识库（ID: <id>）
文档：16（已解析：13；仅可下载：3）
用户：5（admin=<id>, public_viewer=<id>, delivery_viewer=<id>, ops_viewer=<id>, blank_user=<id>）
```

异常打印单行 `错误：...` 到 stderr，返回状态码 2；不得显示堆栈，除非设置 `--verbose`。

- [ ] **步骤 6：运行导入和 CLI 测试确认通过**

运行：`cd backend && pytest tests/test_demo_seed_service.py tests/test_demo_seed_cli.py -v`

预期：PASS。

- [ ] **步骤 7：提交任务**

```bash
git add backend/app/demo/seed_service.py backend/scripts/__init__.py backend/scripts/seed_demo_data.py backend/tests/test_demo_seed_service.py backend/tests/test_demo_seed_cli.py
git commit -m "feat: seed real sample permission demo"
```

---

### 任务 5：建立共享深色运营工作台视觉系统和登录页

**文件：**
- 创建：`backend/app/static/app.css`
- 修改：`backend/app/static/login.html`
- 创建：`backend/app/static/login.js`
- 修改：`backend/app/static/admin.html`
- 修改：`backend/app/static/qa.html`
- 测试：`backend/tests/test_frontend_shell.py`

- [ ] **步骤 1：为共享样式入口和登录钩子写失败前端壳测试**

```python
def test_static_shells_share_workbench_styles_and_login_uses_dedicated_script():
    client = TestClient(create_app())

    for path in ["/login", "/admin", "/qa", "/documents"]:
        response = client.get(path)
        assert response.status_code == 200
        assert '<link rel="stylesheet" href="/static/app.css"' in response.text

    login = client.get("/login")
    assert '<script src="/static/login.js"></script>' in login.text
    assert 'id="demo-account-picker"' in login.text
    assert 'value="admin"' not in login.text
    assert 'value="Demo123!"' not in login.text
```

- [ ] **步骤 2：运行前端壳测试确认失败**

运行：`cd backend && pytest tests/test_frontend_shell.py::test_static_shells_share_workbench_styles_and_login_uses_dedicated_script -v`

预期：FAIL，页面尚未加载 `app.css` 或 `login.js`。

- [ ] **步骤 3：实现设计令牌与基础组件样式**

在 `app.css` 用 CSS 自定义属性实现：深蓝灰页面背景、分层 surface、青蓝 accent、文本层级、绿色成功、琥珀提醒、红色危险、12px 圆角和统一焦点环。至少定义：

```css
:root { --bg: #0b1220; --surface: #121d2f; --surface-raised: #18263c; --accent: #41c7e8; --text: #f1f5f9; --muted: #94a3b8; --success: #4ade80; --warning: #fbbf24; --danger: #fb7185; }
```

为 `.app-shell`、`.topbar`、`.panel`、`.button`、`.button--primary`、`.status-badge`、`.status-badge--parsed`、`.status-badge--unsupported`、`input`、`select`、`textarea`、`table`、`.empty-state`、`.message` 编写可复用样式。使用 `@media (max-width: 900px)` 将多栏降为单列，并确保 `:focus-visible` 明显可见。

登录页移除预填凭据，加载 `app.css` 与专用 `login.js`。提供用户名下拉或按钮组 `id="demo-account-picker"`，仅写入账号名；密码输入始终留空。`login.js` 从登录表单迁移现有登录 POST、token 保存和跳转逻辑，目标改为 `/documents`。

将 admin/qa 的原结构包入 `.app-shell`/`.panel` 等语义类并加载 CSS；保持每一个现有 `id` 与脚本引用不变。

- [ ] **步骤 4：运行前端壳测试确认通过**

运行：`cd backend && pytest tests/test_frontend_shell.py -v`

预期：PASS。

- [ ] **步骤 5：提交任务**

```bash
git add backend/app/static/app.css backend/app/static/login.html backend/app/static/login.js backend/app/static/admin.html backend/app/static/qa.html backend/tests/test_frontend_shell.py
git commit -m "feat: add shared operations workbench styling"
```

---

### 任务 6：实现权限感知文档测试工作台

**文件：**
- 修改：`backend/app/static/documents.html`
- 修改：`backend/app/static/documents.js`
- 修改：`backend/tests/test_frontend_shell.py`

- [ ] **步骤 1：为工作台结构和交互钩子写失败测试**

```python
def test_documents_shell_contains_permission_workbench_regions_and_hooks():
    client = TestClient(create_app())
    response = client.get("/documents")
    script = DOCUMENTS_JS_PATH.read_text(encoding="utf-8")

    assert 'id="documents-topbar"' in response.text
    assert 'id="documents-current-user"' in response.text
    assert 'id="documents-status-filter"' in response.text
    assert 'id="documents-visibility-filter"' in response.text
    assert 'id="documents-product-filter"' in response.text
    assert 'id="documents-admin-panel"' in response.text
    assert 'id="documents-permission-user"' in response.text
    assert 'id="documents-save-permission"' in response.text
    assert 'id="documents-save-view-rule"' in response.text
    assert "applyDocumentFilters" in script
    assert "loadAdminPermissions" in script
    assert "saveWorkbenchPermission" in script
    assert "saveWorkbenchViewRule" in script
```

- [ ] **步骤 2：运行工作台壳测试确认失败**

运行：`cd backend && pytest tests/test_frontend_shell.py::test_documents_shell_contains_permission_workbench_regions_and_hooks -v`

预期：FAIL，现有 `/documents` 只有最小列表和下载区域。

- [ ] **步骤 3：实现语义化三栏工作台 HTML**

在 `documents.html` 保留现有功能 ID，并替换为：顶部 `.topbar`（当前用户、退出、页面导航）、左侧 `.workbench-sidebar`（知识库、统计、筛选）、中部 `.document-grid`（列表）、右侧 `.detail-panel`（详情、元数据、下载），以及初始 `hidden` 的管理员 `.panel`。

为以下控件添加固定 ID：

```html
<select id="documents-status-filter"><option value="all">全部状态</option><option value="parsed">已解析</option><option value="stored_unsupported">仅可下载</option></select>
<select id="documents-visibility-filter"></select>
<select id="documents-product-filter"></select>
<section id="documents-admin-panel" hidden></section>
```

管理员面板使用用户下拉 `documents-permission-user`（不要让管理员手输 ID），包含四个权限 checkbox、三种可见性 checkbox、部门/产品线文本输入、密级选择、加载/保存规则与保存权限按钮。所有管理控件默认 `hidden`，由 JS 中的 `can_grant` 判断显示。

- [ ] **步骤 4：实现前端数据流、筛选、下载和管理员操作**

重构 `documents.js`，但所有请求均继续带 `Authorization`。关键函数：

```javascript
function applyDocumentFilters() {
  const status = document.getElementById('documents-status-filter').value
  const visibility = document.getElementById('documents-visibility-filter').value
  const product = document.getElementById('documents-product-filter').value
  return allDocuments.filter((item) =>
    (status === 'all' || item.status === status) &&
    (!visibility || item.visibility === visibility) &&
    (!product || item.product_line === product || item.product === product),
  )
}
```

- `loadWorkspace()` 先请求 `/api/auth/me` 和 `/api/kb`；未登录时跳转 `/login`；
- 根据用户可访问知识库加载文档；所有筛选选项只能从已授权 `allDocuments` 构建；
- 列表项目显示 `parse_status_label`、visibility、密级和下载可用性；
- 详情使用服务端 `download_available` 管理按钮，不要仅根据状态猜测；
- `logout()` 清除 `session_token` 后跳转 `/login`；
- 管理员判定通过请求当前知识库 `/permissions`：当前 profile ID 对应记录有 `can_grant=true` 时才显示管理区；
- 管理区读取现有 `/permissions`、`/view-rules` API，使用返回的 `user_id` 和 username 填充下拉，保存后刷新文档和权限状态；
- 403/404 均展示 API `detail`，不会把客户端过滤当作安全机制。

- [ ] **步骤 5：运行工作台壳和文档 API 测试确认通过**

运行：`cd backend && pytest tests/test_frontend_shell.py tests/test_document_download_api.py tests/test_view_rule_api.py -v`

预期：PASS。

- [ ] **步骤 6：提交任务**

```bash
git add backend/app/static/documents.html backend/app/static/documents.js backend/tests/test_frontend_shell.py
git commit -m "feat: build permission-aware document workbench"
```

---

### 任务 7：补齐文档、执行真实 ZIP 验收并运行完整回归

**文件：**
- 修改：`docs/api/api-reference.md`
- 修改：`docs/implementation/tech-code-mapping.md`
- 创建：`docs/implementation/demo-sample-acceptance.md`

- [ ] **步骤 1：记录 API 状态字段和明确的演示启动命令**

在 API 文档的文档列表/详情章节增加 `parse_available` 和 `parse_status_label` 字段说明，以及 `stored_unsupported` 的下载语义。在技术映射中指向 `app/demo/sample_catalog.py`、`app/demo/seed_service.py`、`scripts/seed_demo_data.py` 与新增测试。

在验收文档写入可直接复制的流程：

```bash
cd backend
python -m scripts.seed_demo_data \
  --archive ../data/examples/AI知识库_数据样本包.zip \
  --database sqlite:///../data/demo.db \
  --reset
DATABASE_URL=sqlite:///../data/demo.db DEFAULT_OWNER_ID=<输出的-admin-id> uvicorn app.main:app --reload
```

列出五个账号（所有密码 `Demo123!`）及完整人工验收步骤：16/13/3 统计、三个受限用户范围、blank_user 初始不可访问、管理员授权后重新登录、DOCX/PDF/PPTX/XLSX 下载以及审计记录。

- [ ] **步骤 2：运行导入命令进行真实样本验收**

运行：

```bash
cd backend && python -m scripts.seed_demo_data \
  --archive ../data/examples/AI知识库_数据样本包.zip \
  --database sqlite:///../data/demo.db \
  --reset
```

预期：成功报告精确包含 `文档：16（已解析：13；仅可下载：3）`、5 个用户 ID。若本机未安装任一真实 DOCX/PDF 解析依赖，记录具体失败文件和异常；不得将失败伪报为成功。

- [ ] **步骤 3：运行完整自动化回归**

运行：`cd backend && pytest -q`

预期：所有测试通过。

- [ ] **步骤 4：检查差异格式和演示数据库未被跟踪**

运行：

```bash
git diff --check
git status --short
```

预期：没有 whitespace 错误；`data/demo.db`、`data/demo-files/` 与临时解压文件不被 Git 跟踪。必要时在 `.gitignore` 添加精确忽略规则，并先为该规则写一个检查 `git check-ignore -q data/demo.db`。

- [ ] **步骤 5：提交任务**

```bash
git add docs/api/api-reference.md docs/implementation/tech-code-mapping.md docs/implementation/demo-sample-acceptance.md .gitignore
git commit -m "docs: add real sample demo acceptance guide"
```

---

## 计划自检

- **规格覆盖度：** 任务 1 覆盖固定元数据与用户矩阵；任务 2 覆盖实际登录；任务 3 覆盖 DOCX/PDF 与 PPTX/XLSX 状态/API；任务 4 覆盖显式、可重置的 16 份导入；任务 5/6 覆盖统一深色视觉和工作台；任务 7 覆盖真实样本运行、文档与全量回归。
- **依赖顺序：** 目录先于导入；数据库登录先于真实账户验收；状态 API 先于工作台；导入服务先于真实 ZIP 运行；视觉基础先于页面实现，顺序正确。
- **类型一致性：** `stored_unsupported`、`parse_available`、`parse_status_label`、`SeedReport` 和 `DEMO_PASSWORD` 在所有任务中使用同一名称。
- **范围控制：** 不实现 PPTX/XLSX 解析，不启动时自动导入，不引入前端框架，不写生产账户管理。
