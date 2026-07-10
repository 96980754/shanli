# 知识库 CRUD 与最小登录壳实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 为当前知识库系统补齐可管理的知识库 CRUD、文档详情/状态可视化，以及一个可登录进入的最小前端管理壳。

**架构：** 保持现有 FastAPI 单体入口不大拆，新增小而专注的服务与静态页面文件。后端先补最小认证会话、知识库更新/删除、文档详情接口；前端使用 FastAPI 托管的静态 HTML + 原生 JS，实现登录页与管理台壳，避免在当前阶段引入独立前端构建链。 

**技术栈：** FastAPI、Pydantic、SQLAlchemy、pytest、原生 HTML/CSS/JS、Starlette StaticFiles/HTMLResponse。

---

## 文件结构与职责

- 创建：`backend/app/services/auth_service.py` — 最小用户名/密码校验、会话 token 生成与当前用户解析。
- 创建：`backend/app/services/session_store.py` — 进程内会话存储，保存 token → user_id/username。
- 创建：`backend/app/static/login.html` — 登录页。
- 创建：`backend/app/static/admin.html` — 管理台壳，展示知识库列表、详情、文档状态。
- 创建：`backend/app/static/admin.js` — 登录后拉取 `/api/auth/me`、`/api/kb`、`/api/kb/{id}`、`/api/kb/{id}/documents`、新详情接口。
- 修改：`backend/app/main.py` — 注册静态页面、认证接口、知识库更新/删除、文档详情接口，并在数据库模式下接认证服务。
- 修改：`backend/app/services/db_kb_service.py` — 增加 update/delete。
- 修改：`backend/app/services/kb_service.py` — 内存版增加 update/delete。
- 修改：`backend/app/services/db_document_service.py` — 增加按 `doc_id` 查询单文档详情。
- 修改：`backend/app/services/document_service.py` — 内存版增加按 `doc_id` 查询单文档详情。
- 测试：`backend/tests/test_auth_api.py` — 登录、获取当前用户、未登录拒绝。
- 测试：`backend/tests/test_kb_crud_api.py` — 更新、删除知识库。
- 测试：`backend/tests/test_document_detail_api.py` — 单文档详情、状态、block/chunk 统计。
- 测试：`backend/tests/test_frontend_shell.py` — 登录页和管理台壳静态可访问。
- 修改：`docs/api/api-reference.md` — 新增认证接口、知识库更新/删除、文档详情接口。
- 修改：`docs/implementation/tech-code-mapping.md` — 记录登录壳、认证服务、CRUD 增量实现。

---

### 任务 1：最小认证与会话壳

**文件：**
- 创建：`backend/app/services/session_store.py`
- 创建：`backend/app/services/auth_service.py`
- 修改：`backend/app/main.py`
- 测试：`backend/tests/test_auth_api.py`

- [ ] **步骤 1：编写失败的测试**

```python
from fastapi.testclient import TestClient

from app.main import create_app


def test_login_returns_session_token_for_default_admin():
    client = TestClient(create_app())

    response = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "admin"},
    )

    assert response.status_code == 200
    assert response.json()["token"]


def test_me_requires_valid_session_token():
    client = TestClient(create_app())

    response = client.get("/api/auth/me")

    assert response.status_code == 401
```

- [ ] **步骤 2：运行测试验证失败**

运行：`pytest tests/test_auth_api.py -v`
预期：FAIL，报错 `/api/auth/login` 或 `/api/auth/me` 不存在。

- [ ] **步骤 3：编写最少实现代码**

```python
# backend/app/services/session_store.py
from uuid import uuid4


class SessionStore:
    def __init__(self) -> None:
        self._sessions = {}

    def create(self, user_id: str, username: str) -> str:
        token = uuid4().hex
        self._sessions[token] = {"user_id": user_id, "username": username}
        return token

    def get(self, token: str):
        return self._sessions.get(token)
```

```python
# backend/app/services/auth_service.py
class AuthService:
    def __init__(self, session_store):
        self.session_store = session_store

    def login(self, username: str, password: str) -> str | None:
        if username == "admin" and password == "admin":
            return self.session_store.create(user_id="admin", username=username)
        return None
```

```python
# backend/app/main.py (新增片段)
app.state.session_store = SessionStore()
app.state.auth_service = AuthService(app.state.session_store)

@app.post("/api/auth/login")
def login(request: LoginRequest) -> dict[str, str]:
    token = app.state.auth_service.login(request.username, request.password)
    if token is None:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    return {"token": token}

@app.get("/api/auth/me")
def me(authorization: str | None = Header(default=None)) -> dict[str, str]:
    session = require_session(app.state.session_store, authorization)
    return session
```

- [ ] **步骤 4：运行测试验证通过**

运行：`pytest tests/test_auth_api.py -v`
预期：PASS

- [ ] **步骤 5：Commit**

```bash
git add backend/app/main.py backend/app/services/auth_service.py backend/app/services/session_store.py backend/tests/test_auth_api.py
git commit -m "feat: add minimal auth session shell"
```

---

### 任务 2：知识库 CRUD 补齐

**文件：**
- 修改：`backend/app/services/kb_service.py`
- 修改：`backend/app/services/db_kb_service.py`
- 修改：`backend/app/main.py`
- 测试：`backend/tests/test_kb_crud_api.py`

- [ ] **步骤 1：编写失败的测试**

```python
from fastapi.testclient import TestClient

from app.main import app


def test_update_knowledge_base_changes_name_and_description():
    client = TestClient(app)
    created = client.post("/api/kb", json={"name": "旧名称"}).json()

    response = client.put(
        f"/api/kb/{created['id']}",
        json={"name": "新名称", "description": "新的描述", "visibility": "department"},
    )

    assert response.status_code == 200
    assert response.json()["name"] == "新名称"


def test_delete_knowledge_base_removes_it_from_list():
    client = TestClient(app)
    created = client.post("/api/kb", json={"name": "待删除库"}).json()

    response = client.delete(f"/api/kb/{created['id']}")

    assert response.status_code == 200
    ids = [item["id"] for item in client.get("/api/kb").json()["items"]]
    assert created["id"] not in ids
```

- [ ] **步骤 2：运行测试验证失败**

运行：`pytest tests/test_kb_crud_api.py -v`
预期：FAIL，报错 `405` 或 route missing。

- [ ] **步骤 3：编写最少实现代码**

```python
# backend/app/services/kb_service.py
class InMemoryKnowledgeBaseService:
    def update(self, kb_id, name, description, visibility):
        item = self._items.get(kb_id)
        if item is None:
            return None
        item.update({"name": name, "description": description, "visibility": visibility})
        return item

    def delete(self, kb_id):
        return self._items.pop(kb_id, None)
```

```python
# backend/app/main.py (新增片段)
@app.put("/api/kb/{kb_id}")
def update_knowledge_base(kb_id: str, request: KnowledgeBaseCreate) -> dict[str, Any]:
    ...

@app.delete("/api/kb/{kb_id}")
def delete_knowledge_base(kb_id: str) -> dict[str, bool]:
    ...
```

- [ ] **步骤 4：运行测试验证通过**

运行：`pytest tests/test_kb_crud_api.py -v`
预期：PASS

- [ ] **步骤 5：Commit**

```bash
git add backend/app/main.py backend/app/services/kb_service.py backend/app/services/db_kb_service.py backend/tests/test_kb_crud_api.py
git commit -m "feat: add knowledge base update and delete api"
```

---

### 任务 3：文档详情与入库状态可视化接口

**文件：**
- 修改：`backend/app/services/document_service.py`
- 修改：`backend/app/services/db_document_service.py`
- 修改：`backend/app/main.py`
- 测试：`backend/tests/test_document_detail_api.py`

- [ ] **步骤 1：编写失败的测试**

```python
from fastapi.testclient import TestClient

from app.main import create_app


def test_document_detail_returns_parse_and_chunk_counts(tmp_path):
    app = build_database_app()
    app.state.upload_root = tmp_path
    client = TestClient(app)
    kb = client.post("/api/kb", json={"name": "文档详情库", "visibility": "department"}).json()
    uploaded = client.post(
        f"/api/kb/{kb['id']}/documents/upload",
        files={"file": ("manual.txt", b"SOS alarm", "text/plain")},
    ).json()

    response = client.get(f"/api/kb/{kb['id']}/documents/{uploaded['id']}")

    assert response.status_code == 200
    assert response.json()["block_count"] == 1
    assert response.json()["chunk_count"] == 1
```

- [ ] **步骤 2：运行测试验证失败**

运行：`pytest tests/test_document_detail_api.py -v`
预期：FAIL，报错 route missing。

- [ ] **步骤 3：编写最少实现代码**

```python
# backend/app/services/db_document_service.py
class DbDocumentService:
    def get(self, kb_id: int, doc_id: int):
        return (
            self.session.query(Document)
            .filter(Document.kb_id == kb_id, Document.id == doc_id)
            .one_or_none()
        )
```

```python
# backend/app/main.py (新增片段)
@app.get("/api/kb/{kb_id}/documents/{doc_id}")
def get_document_detail(kb_id: str, doc_id: str) -> dict[str, Any]:
    ...
```

返回中包含：

```python
{
    "id": item.id,
    "title": item.title,
    "status": item.status,
    "file_type": item.file_type,
    "block_count": block_count,
    "chunk_count": chunk_count,
}
```

- [ ] **步骤 4：运行测试验证通过**

运行：`pytest tests/test_document_detail_api.py -v`
预期：PASS

- [ ] **步骤 5：Commit**

```bash
git add backend/app/main.py backend/app/services/document_service.py backend/app/services/db_document_service.py backend/tests/test_document_detail_api.py
git commit -m "feat: add document detail and ingestion status api"
```

---

### 任务 4：最小前端登录壳与管理台壳

**文件：**
- 创建：`backend/app/static/login.html`
- 创建：`backend/app/static/admin.html`
- 创建：`backend/app/static/admin.js`
- 修改：`backend/app/main.py`
- 测试：`backend/tests/test_frontend_shell.py`

- [ ] **步骤 1：编写失败的测试**

```python
from fastapi.testclient import TestClient

from app.main import create_app


def test_login_page_is_served():
    client = TestClient(create_app())

    response = client.get("/login")

    assert response.status_code == 200
    assert "登录" in response.text


def test_admin_shell_is_served():
    client = TestClient(create_app())

    response = client.get("/admin")

    assert response.status_code == 200
    assert "知识库管理台" in response.text
```

- [ ] **步骤 2：运行测试验证失败**

运行：`pytest tests/test_frontend_shell.py -v`
预期：FAIL，报错 route missing。

- [ ] **步骤 3：编写最少实现代码**

```html
<!-- backend/app/static/login.html -->
<!doctype html>
<html lang="zh-CN">
  <body>
    <h1>登录</h1>
    <form id="login-form">
      <input name="username" value="admin" />
      <input name="password" type="password" value="admin" />
      <button type="submit">登录</button>
    </form>
    <script src="/static/admin.js"></script>
  </body>
</html>
```

```html
<!-- backend/app/static/admin.html -->
<!doctype html>
<html lang="zh-CN">
  <body>
    <h1>知识库管理台</h1>
    <section id="kb-list"></section>
    <section id="document-list"></section>
    <script src="/static/admin.js"></script>
  </body>
</html>
```

```python
# backend/app/main.py (新增片段)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

@app.get("/login", response_class=HTMLResponse)
def login_page() -> str:
    return LOGIN_HTML.read_text(encoding="utf-8")

@app.get("/admin", response_class=HTMLResponse)
def admin_page() -> str:
    return ADMIN_HTML.read_text(encoding="utf-8")
```

- [ ] **步骤 4：运行测试验证通过**

运行：`pytest tests/test_frontend_shell.py -v`
预期：PASS

- [ ] **步骤 5：Commit**

```bash
git add backend/app/main.py backend/app/static/login.html backend/app/static/admin.html backend/app/static/admin.js backend/tests/test_frontend_shell.py
git commit -m "feat: add login page and admin shell"
```

---

### 任务 5：文档与映射同步维护

**文件：**
- 修改：`docs/api/api-reference.md`
- 修改：`docs/implementation/tech-code-mapping.md`

- [ ] **步骤 1：编写失败的测试**

```python
def test_plan_requires_auth_and_crud_docs_are_updated():
    assert "/api/auth/login" in api_reference_text
    assert "/api/kb/{kb_id}/documents/{doc_id}" in api_reference_text
```

- [ ] **步骤 2：运行测试验证失败**

运行：`pytest tests/test_docs_sync.py -v`
预期：FAIL，找不到新接口文档。

- [ ] **步骤 3：编写最少实现代码**

```markdown
### POST `/api/auth/login`
### GET `/api/auth/me`
### PUT `/api/kb/{kb_id}`
### DELETE `/api/kb/{kb_id}`
### GET `/api/kb/{kb_id}/documents/{doc_id}`
```

并在技术映射文档补充：

```markdown
- `backend/app/services/auth_service.py`
- `backend/app/services/session_store.py`
- `backend/app/static/login.html`
- `backend/app/static/admin.html`
- `backend/app/static/admin.js`
```

- [ ] **步骤 4：运行测试验证通过**

运行：`pytest tests/test_docs_sync.py -v`
预期：PASS

- [ ] **步骤 5：Commit**

```bash
git add docs/api/api-reference.md docs/implementation/tech-code-mapping.md
git commit -m "docs: record auth crud and admin shell endpoints"
```

---

### 任务 6：完整回归验证

**文件：**
- 测试：`backend/tests/test_auth_api.py`
- 测试：`backend/tests/test_kb_crud_api.py`
- 测试：`backend/tests/test_document_detail_api.py`
- 测试：`backend/tests/test_frontend_shell.py`
- 测试：`backend/tests/test_database_mode_api.py`
- 测试：`backend/tests/test_parser_service.py`

- [ ] **步骤 1：编写失败的测试**

```python
def test_regression_suite_placeholder():
    assert True
```

- [ ] **步骤 2：运行测试验证失败**

运行：`pytest backend/tests/test_auth_api.py backend/tests/test_kb_crud_api.py backend/tests/test_document_detail_api.py backend/tests/test_frontend_shell.py -v`
预期：在实现前至少有一个 FAIL。

- [ ] **步骤 3：编写最少实现代码**

```python
# 本任务不新增生产代码；使用前五个任务的实现结果。
```

- [ ] **步骤 4：运行测试验证通过**

运行：`pytest -q`
预期：PASS，完整后端测试集全绿。

- [ ] **步骤 5：Commit**

```bash
git add backend/tests
git commit -m "test: verify auth crud admin shell regression suite"
```
