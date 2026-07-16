# 自助注册与权限文档工作台实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 `superpowers:subagent-driven-development`（推荐）或 `superpowers:executing-plans` 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 支持普通用户注册、管理员人工上传筛选后的真实文件并授予文档权限，提供可用的深色文档工作台。

**架构：** 在数据库模式下以 bcrypt 存储和验证密码；注册用户默认无知识库授权。上传服务根据扩展名将可解析与仅存储文档分开处理，文档 API 返回稳定的解析状态字段。静态页面继续使用原生 HTML/JS，通过一个共享 CSS 文件统一视觉样式；工作台只消费后端已授权返回的数据。

**技术栈：** Python 3.10、FastAPI、SQLAlchemy、bcrypt、pytest、原生 HTML/CSS/JavaScript。

---

## 文件结构与职责

| 文件 | 变更 | 职责 |
|---|---|---|
| `backend/requirements.txt` | 修改 | 增加 bcrypt 依赖。 |
| `backend/app/services/password_service.py` | 创建 | 密码哈希、验证和注册输入校验。 |
| `backend/app/services/auth_service.py` | 修改 | 使用数据库认证回调，同时维持内存模式 MVP 登录。 |
| `backend/app/main.py` | 修改 | 注册、用户列表、认证注入、文档解析状态和不支持格式上传分支。 |
| `backend/app/services/ingestion_service.py` | 修改 | 明确支持格式解析后的 `parsed` 状态。 |
| `backend/app/static/app.css` | 创建 | 共享深色运营工作台样式和响应式布局。 |
| `backend/app/static/login.html` | 修改 | 登录/注册界面与共享样式入口。 |
| `backend/app/static/login.js` | 创建 | 登录、注册和错误提示交互。 |
| `backend/app/static/documents.html` | 修改 | 三栏文档工作台和管理员用户授权区域。 |
| `backend/app/static/documents.js` | 修改 | 工作台数据流、筛选、下载、权限和规则编辑。 |
| `backend/app/static/admin.html` | 修改 | 接入共享视觉样式，保留既有 ID。 |
| `backend/app/static/qa.html` | 修改 | 接入共享视觉样式，保留既有 ID。 |
| `backend/tests/test_registration_api.py` | 创建 | 注册、密码哈希、数据库登录、用户列表和权限边界。 |
| `backend/tests/test_document_download_api.py` | 修改 | 解析状态与仅下载格式 API 语义。 |
| `backend/tests/test_database_mode_api.py` | 修改 | PPTX/XLSX 上传保存及零 chunk 行为。 |
| `backend/tests/test_frontend_shell.py` | 修改 | 共享样式、注册和工作台结构钩子。 |
| `docs/api/api-reference.md` | 修改 | 注册、用户列表和解析状态接口。 |
| `docs/implementation/tech-code-mapping.md` | 修改 | 映射账号、工作台与上传状态实现。 |
| `docs/implementation/manual-permission-workbench-acceptance.md` | 创建 | 人工上传、注册、授权、下载验收流程。 |

---

### 任务 1：建立 bcrypt 密码服务并实现开放注册

**文件：**
- 修改：`backend/requirements.txt`
- 创建：`backend/app/services/password_service.py`
- 修改：`backend/app/main.py`
- 测试：`backend/tests/test_registration_api.py`

- [ ] **步骤 1：写注册与密码哈希失败测试**

```python
def test_register_creates_hashed_database_user_without_permissions(database_client):
    response = database_client.post(
        "/api/auth/register",
        json={"username": "alice", "password": "eightpass", "password_confirmation": "eightpass"},
    )

    assert response.status_code == 201
    assert response.json() == {"id": 2, "username": "alice"}
    user = database_client.app.state.db_session.query(User).filter_by(username="alice").one()
    assert user.password_hash != "eightpass"
    assert bcrypt.checkpw(b"eightpass", user.password_hash.encode())
    assert database_client.app.state.kb_service.list_for_user(user.id) == []
```

增加用户名过短/非法、密码少于 8 位、确认密码不同返回 `422`，重复用户名返回 `409`，且响应绝不含 `password`/`password_hash` 的测试。

- [ ] **步骤 2：运行测试确认失败**

运行：`cd backend && pytest tests/test_registration_api.py -v`

预期：FAIL，`/api/auth/register` 尚不存在。

- [ ] **步骤 3：实现密码服务和注册端点**

在 `requirements.txt` 增加：

```text
bcrypt==4.2.0
```

创建：

```python
# backend/app/services/password_service.py
import re
import bcrypt

USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{3,64}$")


def validate_registration(username: str, password: str, password_confirmation: str) -> str:
    normalized = username.strip()
    if not USERNAME_PATTERN.fullmatch(normalized):
        raise ValueError("Username must be 3-64 characters using letters, numbers, dot, dash, or underscore")
    if len(password) < 8:
        raise ValueError("Password must be at least 8 characters")
    if password != password_confirmation:
        raise ValueError("Password confirmation does not match")
    return normalized


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False
```

在 `main.py` 增加 `RegistrationRequest`，数据库模式 `POST /api/auth/register`：查重、确保名为 `普通用户` 的 role 存在、保存 bcrypt 哈希的 `User`，提交后返回 `201`。内存模式返回 `501 Registration requires database mode`。禁止客户端提交 role 或权限字段。

- [ ] **步骤 4：运行注册测试确认通过**

运行：`cd backend && pytest tests/test_registration_api.py -v`

预期：PASS。

- [ ] **步骤 5：提交任务**

```bash
git add backend/requirements.txt backend/app/services/password_service.py backend/app/main.py backend/tests/test_registration_api.py
git commit -m "feat: add self-service user registration"
```

---

### 任务 2：切换数据库认证为 bcrypt 并提供安全用户列表

**文件：**
- 修改：`backend/app/services/auth_service.py`
- 修改：`backend/app/main.py`
- 修改：`backend/tests/test_registration_api.py`
- 修改：`backend/tests/test_auth_api.py`

- [ ] **步骤 1：写数据库 bcrypt 登录和管理员用户列表失败测试**

```python
def test_registered_user_logs_in_with_bcrypt_password(database_client):
    database_client.post("/api/auth/register", json={
        "username": "alice", "password": "correctpass", "password_confirmation": "correctpass",
    })

    login = database_client.post("/api/auth/login", json={"username": "alice", "password": "correctpass"})
    profile = database_client.get("/api/auth/me", headers={"Authorization": f"Bearer {login.json()['token']}"})

    assert login.status_code == 200
    assert profile.json()["username"] == "alice"
    assert profile.json()["user_id"].isdigit()


def test_users_endpoint_requires_can_grant_and_never_returns_password_hash(database_client):
    client, admin_token, kb = database_client
    denied = client.get("/api/users")
    allowed = client.get("/api/users", headers={"Authorization": f"Bearer {admin_token}"})

    assert denied.status_code == 401
    assert allowed.status_code == 200
    assert all("password_hash" not in item and "password" not in item for item in allowed.json()["items"])
```

补充：无任何 `can_grant` 的已登录用户得到 `403`；`DEFAULT_OWNER_ID` 不影响登录身份；内存模式 `admin/admin` 持续可用。

- [ ] **步骤 2：运行测试确认失败**

运行：`cd backend && pytest tests/test_registration_api.py tests/test_auth_api.py -v`

预期：FAIL，数据库认证尚未使用 `verify_password()`，`GET /api/users` 尚不存在。

- [ ] **步骤 3：实现认证和用户列表**

`AuthService` 的数据库回调类型为 `Callable[[str, str], User | None] | None`。在数据库应用中仅按精确用户名取用户并调用 `verify_password()`；不得以 `DEFAULT_OWNER_ID`、硬编码 `admin/admin` 或明文相等比较绕过认证。内存模式仍只接受 `admin/admin`。

新增：

```python
@app.get("/api/users")
def list_users(authorization: str | None = Header(default=None)) -> dict[str, list[dict[str, str]]]:
    session = require_session(app.state.session_store, authorization)
    user_id = int(resolve_session_user_id(app, session))
    has_grant = any(
        permission.can_grant
        for permission in app.state.db_session.query(KnowledgeBasePermission)
        .filter(KnowledgeBasePermission.user_id == user_id)
        .all()
    )
    if not has_grant:
        raise HTTPException(status_code=403, detail="Permission denied")
    return {"items": [
        {"id": str(user.id), "username": user.username, "role": user.role.name}
        for user in app.state.db_session.query(User).order_by(User.username).all()
    ]}
```

内存模式对 `/api/users` 返回 `501`。不要暴露密码字段。

- [ ] **步骤 4：运行认证和用户列表测试确认通过**

运行：`cd backend && pytest tests/test_registration_api.py tests/test_auth_api.py tests/test_kb_permissions_api.py -v`

预期：PASS。

- [ ] **步骤 5：提交任务**

```bash
git add backend/app/services/auth_service.py backend/app/main.py backend/tests/test_registration_api.py backend/tests/test_auth_api.py
git commit -m "feat: authenticate registered users and list users"
```

---

### 任务 3：为人工上传提供稳定解析状态

**文件：**
- 修改：`backend/app/main.py`
- 修改：`backend/app/services/ingestion_service.py`
- 修改：`backend/tests/test_database_mode_api.py`
- 修改：`backend/tests/test_document_download_api.py`

- [ ] **步骤 1：写 PPTX/XLSX 仅下载状态失败测试**

```python
def test_database_upload_keeps_pptx_downloadable_without_chunks(database_client):
    client, token, kb = database_client
    response = client.post(
        f"/api/kb/{kb.id}/documents/upload",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("方案.pptx", b"presentation", "application/vnd.openxmlformats-officedocument.presentationml.presentation")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "stored_unsupported"
    assert body["parse_available"] is False
    assert body["parse_status_label"] == "仅可下载（暂不支持内容解析）"
    assert body["chunk_count"] == 0
    assert body["download_available"] is True
```

添加 `.xlsx` 同等断言，以及已有 DOCX/PDF 上传的 `status="parsed"`、`parse_available=True` 断言。

- [ ] **步骤 2：运行状态测试确认失败**

运行：`cd backend && pytest tests/test_database_mode_api.py tests/test_document_download_api.py -v`

预期：FAIL，响应缺少解析状态字段或上传尝试调用不支持解析器。

- [ ] **步骤 3：实现状态序列化和上传分流**

在 `main.py` 添加：

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

让 `serialize_database_document()` 合并该字典。上传时：`.pptx`、`.xlsx` 保存后写 `doc.status="stored_unsupported"` 并提交，返回零 block/chunk，绝不调用 `IngestionService`，绝不删除保存的原件。`.docx`、`.pdf` 调用 ingestion；成功后 ingestion 将 `document.status="parsed"`，再提交。

- [ ] **步骤 4：运行上传/下载测试确认通过**

运行：`cd backend && pytest tests/test_database_mode_api.py tests/test_document_download_api.py tests/test_upload_ingest_flow.py -v`

预期：PASS。

- [ ] **步骤 5：提交任务**

```bash
git add backend/app/main.py backend/app/services/ingestion_service.py backend/tests/test_database_mode_api.py backend/tests/test_document_download_api.py
git commit -m "feat: distinguish parsed and download-only documents"
```

---

### 任务 4：建立共享深色视觉系统和登录注册界面

**文件：**
- 创建：`backend/app/static/app.css`
- 修改：`backend/app/static/login.html`
- 创建：`backend/app/static/login.js`
- 修改：`backend/app/static/admin.html`
- 修改：`backend/app/static/qa.html`
- 修改：`backend/tests/test_frontend_shell.py`

- [ ] **步骤 1：写页面样式与注册钩子失败测试**

```python
def test_shells_load_shared_workbench_styles_and_login_registration_script():
    client = TestClient(create_app())
    for path in ["/login", "/admin", "/qa", "/documents"]:
        assert '<link rel="stylesheet" href="/static/app.css"' in client.get(path).text

    login = client.get("/login").text
    assert 'id="login-form"' in login
    assert 'id="registration-form"' in login
    assert 'id="show-registration"' in login
    assert '<script src="/static/login.js"></script>' in login
    assert 'value="admin"' not in login
```

- [ ] **步骤 2：运行前端壳测试确认失败**

运行：`cd backend && pytest tests/test_frontend_shell.py::test_shells_load_shared_workbench_styles_and_login_registration_script -v`

预期：FAIL，当前页面没有共享 CSS 或专用注册脚本。

- [ ] **步骤 3：实现共享 CSS、登录和注册交互**

创建 `app.css`，定义 `--bg:#0b1220`、`--surface:#121d2f`、`--surface-raised:#18263c`、`--accent:#41c7e8`、`--text:#f1f5f9`、`--muted:#94a3b8`、`--success:#4ade80`、`--warning:#fbbf24`、`--danger:#fb7185`。实现 `.app-shell`、`.topbar`、`.panel`、`.button`、`.button--primary`、`.status-badge`、`.empty-state`、表单和 `@media (max-width:900px)` 单列规则，包含 `:focus-visible` 焦点环。

登录页包含切换按钮、无预填的登录表单和注册表单；`login.js` 调用 `/api/auth/login` 或 `/api/auth/register`，展示 API detail，注册成功后显示登录表单并保留用户名，登录成功将 token 写入 localStorage 后跳转 `/documents`。admin/qa 页面加载 CSS 并添加类名，不更改任何既有元素 ID 和 JS 入口。

- [ ] **步骤 4：运行前端壳测试确认通过**

运行：`cd backend && pytest tests/test_frontend_shell.py -v`

预期：PASS。

- [ ] **步骤 5：提交任务**

```bash
git add backend/app/static/app.css backend/app/static/login.html backend/app/static/login.js backend/app/static/admin.html backend/app/static/qa.html backend/tests/test_frontend_shell.py
git commit -m "feat: add registration interface and shared workbench style"
```

---

### 任务 5：实现权限感知文档工作台和授权用户选择器

**文件：**
- 修改：`backend/app/static/documents.html`
- 修改：`backend/app/static/documents.js`
- 修改：`backend/tests/test_frontend_shell.py`

- [ ] **步骤 1：写工作台结构失败测试**

```python
def test_documents_shell_contains_filters_empty_access_and_admin_user_controls():
    client = TestClient(create_app())
    page = client.get("/documents").text
    script = DOCUMENTS_JS_PATH.read_text(encoding="utf-8")

    assert 'id="documents-current-user"' in page
    assert 'id="documents-no-access"' in page
    assert 'id="documents-status-filter"' in page
    assert 'id="documents-visibility-filter"' in page
    assert 'id="documents-product-filter"' in page
    assert 'id="documents-admin-panel"' in page
    assert 'id="documents-permission-user"' in page
    assert "applyDocumentFilters" in script
    assert "loadRegisteredUsers" in script
    assert "saveWorkbenchPermission" in script
    assert "saveWorkbenchViewRule" in script
```

- [ ] **步骤 2：运行失败测试**

运行：`cd backend && pytest tests/test_frontend_shell.py::test_documents_shell_contains_filters_empty_access_and_admin_user_controls -v`

预期：FAIL，现有文档页是最小列表。

- [ ] **步骤 3：实现三栏工作台 HTML**

保留 `documents-kb-list`、`documents-list`、`documents-detail`、`download-document` 既有 ID。增加：账户区、登出按钮、无权限空状态、三个筛选器、管理员 `hidden` 面板、`documents-permission-user` select、四权限 checkbox、部门/产品线输入、visibility checkbox、密级 select、加载/保存/删除知识视图和保存权限按钮。

- [ ] **步骤 4：实现安全的数据流与权限编辑**

`documents.js`：

```javascript
function applyDocumentFilters() {
  const status = document.getElementById('documents-status-filter').value
  const visibility = document.getElementById('documents-visibility-filter').value
  const product = document.getElementById('documents-product-filter').value
  return allDocuments.filter((documentItem) =>
    (status === 'all' || documentItem.status === status) &&
    (!visibility || documentItem.visibility === visibility) &&
    (!product || documentItem.product === product || documentItem.product_line === product),
  )
}
```

未认证时跳 `/login`；`/api/kb` 为空时显示 `documents-no-access`；只从已授权 `allDocuments` 构建筛选项。详情显示 `parse_status_label` 和服务端 `download_available`。登出删除 token。

用选中知识库 `/permissions` 找到当前用户是否 `can_grant`，仅此时显示管理员面板。面板调用 `/api/users` 填用户 select，调用现有权限/知识视图 API 保存；撤销 `can_view` 成功后调用 DELETE 规则 API；保存后刷新文档、权限及详情。每一类 API 错误读取 `detail` 显示，客户端筛选不代替后端鉴权。

- [ ] **步骤 5：运行前端/API 回归测试**

运行：`cd backend && pytest tests/test_frontend_shell.py tests/test_registration_api.py tests/test_view_rule_api.py tests/test_document_download_api.py -v`

预期：PASS。

- [ ] **步骤 6：提交任务**

```bash
git add backend/app/static/documents.html backend/app/static/documents.js backend/tests/test_frontend_shell.py
git commit -m "feat: add permission-aware document workbench"
```

---

### 任务 6：修复历史文档 scope 回填并记录人工验收

**文件：**
- 修改：`backend/app/core/db.py`
- 修改：`backend/tests/test_runtime_database_mode.py`
- 修改：`docs/api/api-reference.md`
- 修改：`docs/implementation/tech-code-mapping.md`
- 创建：`docs/implementation/manual-permission-workbench-acceptance.md`

- [ ] **步骤 1：写历史 visibility scope 回填失败测试**

```python
def test_runtime_schema_backfills_legacy_scope_and_product_from_metadata(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE documents (id INTEGER PRIMARY KEY, kb_id INTEGER, title VARCHAR(512), file_type VARCHAR(16), status VARCHAR(16), department VARCHAR(100), product_line VARCHAR(100), visibility VARCHAR(30), security_level INTEGER, tags TEXT)"))
        connection.execute(text("INSERT INTO documents VALUES (1, 1, 'restricted.pdf', 'pdf', 'parsed', '', 'MCSTARS', 'restricted', 3, '')"))

    ensure_runtime_schema(engine)

    row = engine.connect().execute(text("SELECT scope, product FROM documents WHERE id = 1")).one()
    assert row == ("R", "MC")
```

并加 public/internal、未知产品 `GEN` 的断言。

- [ ] **步骤 2：运行迁移测试确认失败**

运行：`cd backend && pytest tests/test_runtime_database_mode.py::test_runtime_schema_backfills_legacy_scope_and_product_from_metadata -v`

预期：FAIL，新增列只使用默认 `I/GEN`，没有按已有元数据回填。

- [ ] **步骤 3：实现幂等 backfill**

在 `ensure_runtime_schema()` 的 ALTER 后对 SQLite/PostgreSQL 运行只更新新增/默认值的 SQL：

```sql
UPDATE documents
SET scope = CASE visibility
  WHEN 'public' THEN 'C'
  WHEN 'restricted' THEN 'R'
  ELSE 'I'
END
WHERE scope IS NULL OR scope = 'I';
```

仅在本次运行确实新增 `scope` 时执行此 scope backfill，避免覆盖用户后续人工修改。仅在新增 `product` 时，从 `product_line` 映射 `MCSTARS→MC`、`MINISERVER→MS`、`定位→LOC`、`POCSTARS-MNO→MNO`、`POCSTARS-PRO→PRO`、`POCSTARS-UC→UC`，其余 `GEN`。参数化常量，SQL 表/列名保持内部固定。

- [ ] **步骤 4：运行迁移和全量测试**

运行：`cd backend && pytest tests/test_runtime_database_mode.py -v && pytest -q`

预期：所有测试通过。

- [ ] **步骤 5：编写 API 与人工验收文档**

API 文档记录注册请求/响应、用户列表授权要求、解析状态字段。技术映射记录 bcrypt、注册和工作台。人工验收文档给出：管理员初始化前提、人工上传四种文件、注册用户、默认无权限、管理员授权/规则、用户刷新、下载与审计核对。不得提及 ZIP、预置账号或 `Demo123!`。

- [ ] **步骤 6：验证差异并提交**

运行：

```bash
git diff --check
git status --short
```

预期：无 whitespace 错误，没有数据库、上传文件或临时产物进入暂存区。

提交：

```bash
git add backend/app/core/db.py backend/tests/test_runtime_database_mode.py docs/api/api-reference.md docs/implementation/tech-code-mapping.md docs/implementation/manual-permission-workbench-acceptance.md
git commit -m "fix: backfill legacy document access metadata"
```

---

## 计划自检

- 注册、bcrypt 登录、用户列表和无默认权限：任务 1–2；
- DOCX/PDF 与 PPTX/XLSX 状态/下载：任务 3；
- 全部页面统一深色风格、登录注册与工作台：任务 4–5；
- 管理员为已注册用户授权、规则与撤销：任务 2、5；
- 历史 restricted 文档不被错误视为 internal：任务 6；
- 人工上传验收、无 ZIP/预置账户：任务 6。

所有字段名称统一使用 `password_confirmation`、`parse_available`、`parse_status_label`、`stored_unsupported` 和 `download_available`。