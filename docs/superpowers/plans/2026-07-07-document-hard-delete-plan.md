# 文档硬删除与管理台安全删除交互实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 为知识库系统补齐文档硬删除能力，并在管理台提供基于用户级别（`level >= 3`）的页面内二次确认删除交互。

**架构：** 后端继续沿用现有 FastAPI 单体入口与内存/数据库双模式服务结构，在认证 session 中增加 `level`，新增文档删除 API，并在数据库模式下级联清理 `document_chunks`、`parse_tasks` 与 `content_blocks`。前端继续使用静态 HTML + 原生 JS，在 `/admin` 页面补一个页面内删除确认区，通过 `/api/auth/me` 返回的 `level` 控制删除按钮展示与删除调用。

**技术栈：** FastAPI、Pydantic、SQLAlchemy、pytest、原生 HTML/CSS/JS。

---

## 文件结构与职责

- 修改：`backend/app/services/session_store.py` — 在 session 数据中保存 `level`。
- 修改：`backend/app/services/auth_service.py` — 默认 admin 登录时写入 `level=3`。
- 修改：`backend/app/main.py` — `GET /api/auth/me` 返回 `level`；新增 `DELETE /api/kb/{kb_id}/documents/{doc_id}`；做 `level >= 3` 权限判断。
- 修改：`backend/app/services/document_service.py` — 内存模式支持按知识库删除文档并修正 `doc_count`。
- 修改：`backend/app/services/db_document_service.py` — 数据库模式按文档硬删除，清理 `document_chunks`、`parse_tasks`、`content_blocks`，修正 `doc_count`。
- 创建：`backend/tests/test_document_delete_api.py` — 删除接口、权限控制、删除后状态、数据库级联清理。
- 修改：`backend/tests/test_auth_api.py` — `/api/auth/me` 返回 `level`。
- 修改：`backend/app/static/admin.html` — 增加页面内删除确认区与提示区。
- 修改：`backend/app/static/admin.js` — 读取 `level`，渲染删除按钮、确认区、删除后刷新列表。
- 修改：`backend/tests/test_frontend_shell.py` — 验证管理台包含删除确认区。
- 修改：`docs/api/api-reference.md` — 记录删除接口、`level` 字段、管理台静态交互变更。
- 修改：`docs/implementation/tech-code-mapping.md` — 记录文档删除能力与管理台删除交互映射。

---

### 任务 1：补齐 session level 与认证返回

**文件：**
- 修改：`backend/app/services/session_store.py`
- 修改：`backend/app/services/auth_service.py`
- 修改：`backend/app/main.py`
- 测试：`backend/tests/test_auth_api.py`

- [ ] **步骤 1：编写失败的测试**

```python
from fastapi.testclient import TestClient

from app.main import create_app


def test_login_then_me_returns_default_admin_level():
    client = TestClient(create_app())

    login_response = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "admin"},
    )
    token = login_response.json()["token"]

    me_response = client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert me_response.status_code == 200
    assert me_response.json()["level"] == 3
```

- [ ] **步骤 2：运行测试验证失败**

运行：`pytest tests/test_auth_api.py::test_login_then_me_returns_default_admin_level -v`
预期：FAIL，报错缺少 `level` 字段或断言不匹配。

- [ ] **步骤 3：编写最少实现代码**

```python
# backend/app/services/session_store.py
from typing import TypedDict
from uuid import uuid4


class SessionData(TypedDict):
    user_id: str
    username: str
    level: int


class SessionStore:
    def __init__(self) -> None:
        self._sessions: dict[str, SessionData] = {}

    def create(self, user_id: str, username: str, level: int) -> str:
        token = uuid4().hex
        self._sessions[token] = {"user_id": user_id, "username": username, "level": level}
        return token
```

```python
# backend/app/services/auth_service.py
from app.services.session_store import SessionStore


class AuthService:
    def __init__(self, session_store: SessionStore) -> None:
        self.session_store: SessionStore = session_store

    def login(self, username: str, password: str) -> str | None:
        if username == "admin" and password == "admin":
            return self.session_store.create(user_id="admin", username=username, level=3)
        return None
```

`backend/app/main.py` 无需新增逻辑，只需确保 `require_session()` 返回包含 `level` 的 session 数据，`GET /api/auth/me` 原样返回该结构。

- [ ] **步骤 4：运行测试验证通过**

运行：`pytest tests/test_auth_api.py::test_login_then_me_returns_default_admin_level -v`
预期：PASS

- [ ] **步骤 5：Commit**

```bash
git add backend/app/services/session_store.py backend/app/services/auth_service.py backend/app/main.py backend/tests/test_auth_api.py
git commit -m "feat: expose auth level in session profile"
```

---

### 任务 2：实现文档删除 API 与权限控制

**文件：**
- 修改：`backend/app/main.py`
- 修改：`backend/app/services/document_service.py`
- 修改：`backend/app/services/db_document_service.py`
- 创建：`backend/tests/test_document_delete_api.py`

- [ ] **步骤 1：编写失败的测试**

```python
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.db import Base
from app.main import create_app
from app.models import Role, User


def build_database_app():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    role = Role(name="管理员", level=3)
    owner = User(username="admin", password_hash="hash", role=role)
    session.add_all([role, owner])
    session.commit()
    app = create_app(mode="database", session=session)
    app.state.default_owner_id = owner.id
    return app


def login_default_admin(client: TestClient) -> str:
    response = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "admin"},
    )
    return response.json()["token"]


def test_delete_document_requires_login():
    client = TestClient(create_app())
    kb = client.post("/api/kb", json={"name": "删除库"}).json()
    uploaded = client.post(
        f"/api/kb/{kb['id']}/documents/upload",
        files={"file": ("manual.txt", b"content", "text/plain")},
    ).json()

    response = client.delete(f"/api/kb/{kb['id']}/documents/{uploaded['id']}")

    assert response.status_code == 401


def test_delete_document_removes_it_from_list_and_detail():
    client = TestClient(create_app())
    token = login_default_admin(client)
    kb = client.post("/api/kb", json={"name": "删除库"}).json()
    uploaded = client.post(
        f"/api/kb/{kb['id']}/documents/upload",
        files={"file": ("manual.txt", b"content", "text/plain")},
    ).json()

    response = client.delete(
        f"/api/kb/{kb['id']}/documents/{uploaded['id']}",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json() == {"deleted": True}
    assert client.get(f"/api/kb/{kb['id']}/documents/{uploaded['id']}").status_code == 404
    assert client.get(f"/api/kb/{kb['id']}/documents").json()["items"] == []
```

- [ ] **步骤 2：运行测试验证失败**

运行：`pytest tests/test_document_delete_api.py -v`
预期：FAIL，报错 route missing、405 或行为未实现。

- [ ] **步骤 3：编写最少实现代码**

```python
# backend/app/main.py

def require_level(session: dict[str, Any], minimum_level: int) -> None:
    if int(session.get("level", 0)) < minimum_level:
        raise HTTPException(status_code=403, detail="Insufficient permission level")


@app.delete("/api/kb/{kb_id}/documents/{doc_id}")
def delete_document(
    kb_id: str,
    doc_id: str,
    authorization: str | None = Header(default=None),
) -> dict[str, bool]:
    session = require_session(app.state.session_store, authorization)
    require_level(session, 3)
    service_kb_id = int(kb_id) if app.state.service_mode == "database" else kb_id
    service_doc_id = int(doc_id) if app.state.service_mode == "database" else doc_id
    deleted = app.state.document_service.delete(service_kb_id, service_doc_id)
    if deleted is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return {"deleted": True}
```

```python
# backend/app/services/document_service.py
class InMemoryDocumentService:
    ...

    def delete(self, kb_id: str, doc_id: str) -> dict[str, Any] | None:
        items = self._documents_by_kb.get(kb_id, [])
        for index, item in enumerate(items):
            if item["id"] == doc_id:
                deleted = items.pop(index)
                kb = self.kb_service.get(kb_id)
                if kb is not None:
                    kb["doc_count"] = max(0, kb["doc_count"] - 1)
                return deleted
        return None
```

数据库模式的 `delete()` 先只返回已删除文档对象；级联清理放到下一任务完成。

- [ ] **步骤 4：运行测试验证通过**

运行：`pytest tests/test_document_delete_api.py::test_delete_document_requires_login tests/test_document_delete_api.py::test_delete_document_removes_it_from_list_and_detail -v`
预期：PASS

- [ ] **步骤 5：Commit**

```bash
git add backend/app/main.py backend/app/services/document_service.py backend/app/services/db_document_service.py backend/tests/test_document_delete_api.py
git commit -m "feat: add protected document delete api"
```

---

### 任务 3：补齐数据库硬删除级联清理

**文件：**
- 修改：`backend/app/services/db_document_service.py`
- 测试：`backend/tests/test_document_delete_api.py`

- [ ] **步骤 1：编写失败的测试**

```python
def test_database_delete_document_cascades_chunks_blocks_tasks_and_doc_count(tmp_path):
    app = build_database_app()
    app.state.upload_root = tmp_path
    client = TestClient(app)
    token = login_default_admin(client)
    kb = client.post("/api/kb", json={"name": "数据库删除库", "visibility": "department"}).json()
    uploaded = client.post(
        f"/api/kb/{kb['id']}/documents/upload",
        files={"file": ("manual.txt", b"SOS alarm", "text/plain")},
    ).json()

    response = client.delete(
        f"/api/kb/{kb['id']}/documents/{uploaded['id']}",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert client.get(f"/api/kb/{kb['id']}/documents/{uploaded['id']}").status_code == 404
    assert client.get(f"/api/kb/{kb['id']}").json()["doc_count"] == 0
    assert app.state.db_session.query(DocumentChunk).count() == 0
    assert app.state.db_session.query(ParseTask).count() == 0
    assert app.state.db_session.query(ContentBlock).count() == 0
```

- [ ] **步骤 2：运行测试验证失败**

运行：`pytest tests/test_document_delete_api.py::test_database_delete_document_cascades_chunks_blocks_tasks_and_doc_count -v`
预期：FAIL，报错 chunks / parse tasks / content blocks 未被清理，或 `doc_count` 未正确更新。

- [ ] **步骤 3：编写最少实现代码**

```python
# backend/app/services/db_document_service.py
from sqlalchemy.orm import Session

from app.models import ContentBlock, Document, DocumentChunk, KnowledgeBase, ParseTask


class DbDocumentService:
    ...

    def delete(self, kb_id: int, doc_id: int) -> Document | None:
        document = self.get(kb_id, doc_id)
        if document is None:
            return None

        parse_tasks = (
            self.session.query(ParseTask)
            .filter(ParseTask.document_id == document.id, ParseTask.kb_id == kb_id)
            .all()
        )
        parse_task_ids = [task.id for task in parse_tasks]
        if parse_task_ids:
            (
                self.session.query(ContentBlock)
                .filter(ContentBlock.parse_task_id.in_(parse_task_ids))
                .delete(synchronize_session=False)
            )
        (
            self.session.query(DocumentChunk)
            .filter(DocumentChunk.document_id == document.id)
            .delete(synchronize_session=False)
        )
        (
            self.session.query(ParseTask)
            .filter(ParseTask.id.in_(parse_task_ids) if parse_task_ids else False)
            .delete(synchronize_session=False)
        )

        kb = self.session.get(KnowledgeBase, kb_id)
        if kb is not None:
            kb.doc_count = max(0, kb.doc_count - 1)

        self.session.delete(document)
        self.session.commit()
        return document
```

- [ ] **步骤 4：运行测试验证通过**

运行：`pytest tests/test_document_delete_api.py::test_database_delete_document_cascades_chunks_blocks_tasks_and_doc_count -v`
预期：PASS

- [ ] **步骤 5：Commit**

```bash
git add backend/app/services/db_document_service.py backend/tests/test_document_delete_api.py
git commit -m "feat: cascade database document hard delete"
```

---

### 任务 4：实现前端页面内删除确认区

**文件：**
- 修改：`backend/app/static/admin.html`
- 修改：`backend/app/static/admin.js`
- 修改：`backend/tests/test_frontend_shell.py`

- [ ] **步骤 1：编写失败的测试**

```python
from fastapi.testclient import TestClient

from app.main import create_app


def test_admin_shell_contains_delete_confirmation_panel():
    client = TestClient(create_app())

    response = client.get("/admin")

    assert response.status_code == 200
    assert 'id="delete-panel"' in response.text
    assert "确认删除" in response.text
```

- [ ] **步骤 2：运行测试验证失败**

运行：`pytest tests/test_frontend_shell.py::test_admin_shell_contains_delete_confirmation_panel -v`
预期：FAIL，报错页面中缺少删除确认区。

- [ ] **步骤 3：编写最少实现代码**

```html
<!-- backend/app/static/admin.html -->
<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>知识库管理台</title>
  </head>
  <body>
    <h1>知识库管理台</h1>
    <section id="kb-list"></section>
    <section id="document-list"></section>
    <section id="delete-panel" hidden>
      <h2>确认删除</h2>
      <p id="delete-target"></p>
      <p id="delete-message">删除后不可恢复。</p>
      <button id="confirm-delete" type="button">确认删除</button>
      <button id="cancel-delete" type="button">取消</button>
    </section>
    <script src="/static/admin.js"></script>
  </body>
</html>
```

```javascript
// backend/app/static/admin.js
let authProfile = null
let selectedDelete = null

function showDeletePanel(documentItem) {
  selectedDelete = documentItem
  const panel = document.getElementById('delete-panel')
  const target = document.getElementById('delete-target')
  if (!panel || !target) return
  target.textContent = `即将删除：${documentItem.title}`
  panel.hidden = false
}

function hideDeletePanel() {
  selectedDelete = null
  const panel = document.getElementById('delete-panel')
  if (panel) {
    panel.hidden = true
  }
}

window.addEventListener('DOMContentLoaded', () => {
  const cancelButton = document.getElementById('cancel-delete')
  if (cancelButton) {
    cancelButton.addEventListener('click', hideDeletePanel)
  }
})
```

本任务只要求页面与基础交互容器到位；真正删除调用放到下一任务实现。

- [ ] **步骤 4：运行测试验证通过**

运行：`pytest tests/test_frontend_shell.py::test_admin_shell_contains_delete_confirmation_panel -v`
预期：PASS

- [ ] **步骤 5：Commit**

```bash
git add backend/app/static/admin.html backend/app/static/admin.js backend/tests/test_frontend_shell.py
git commit -m "feat: add admin delete confirmation panel"
```

---

### 任务 5：打通管理台删除交互与列表刷新

**文件：**
- 修改：`backend/app/static/admin.js`
- 测试：`backend/tests/test_document_delete_api.py`
- 修改：`backend/tests/test_frontend_shell.py`

- [ ] **步骤 1：编写失败的测试**

```python
def test_login_then_me_returns_default_admin_level():
    client = TestClient(create_app())

    login_response = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "admin"},
    )
    token = login_response.json()["token"]
    me_response = client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert me_response.status_code == 200
    assert me_response.json()["level"] == 3
```

```python
def test_delete_document_with_invalid_level_returns_403(monkeypatch):
    from app.main import create_app

    app = create_app()
    token = app.state.session_store.create(user_id="u1", username="editor", level=2)
    client = TestClient(app)
    kb = client.post("/api/kb", json={"name": "删除库"}).json()
    uploaded = client.post(
        f"/api/kb/{kb['id']}/documents/upload",
        files={"file": ("manual.txt", b"content", "text/plain")},
    ).json()

    response = client.delete(
        f"/api/kb/{kb['id']}/documents/{uploaded['id']}",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "Insufficient permission level"
```

- [ ] **步骤 2：运行测试验证失败**

运行：`pytest tests/test_document_delete_api.py::test_delete_document_with_invalid_level_returns_403 -v`
预期：FAIL，当前尚未做 level 权限拒绝。

- [ ] **步骤 3：编写最少实现代码**

```javascript
// backend/app/static/admin.js
let authProfile = null
let activeKbId = null
let activeDocuments = []
let selectedDelete = null

async function authorizedJson(path, options = {}) {
  const token = localStorage.getItem('session_token')
  const headers = { ...(options.headers || {}) }
  if (token) {
    headers.Authorization = `Bearer ${token}`
  }
  return requestJson(path, { ...options, headers })
}

function renderDocuments(items) {
  activeDocuments = items
  const container = document.getElementById('document-list')
  if (!container) return
  container.innerHTML = ''
  for (const item of items) {
    const row = document.createElement('div')
    row.textContent = item.title
    if (authProfile && authProfile.level >= 3) {
      const button = document.createElement('button')
      button.type = 'button'
      button.textContent = '删除'
      button.addEventListener('click', () => showDeletePanel(item))
      row.appendChild(button)
    }
    container.appendChild(row)
  }
}

async function refreshDocuments() {
  if (!activeKbId) return
  const result = await authorizedJson(`/api/kb/${activeKbId}/documents`)
  renderDocuments(result?.items || [])
}

async function confirmDelete() {
  if (!selectedDelete || !activeKbId) return
  const result = await authorizedJson(`/api/kb/${activeKbId}/documents/${selectedDelete.id}`, {
    method: 'DELETE',
  })
  const message = document.getElementById('delete-message')
  if (!result?.deleted) {
    if (message) {
      message.textContent = '删除失败，请检查权限或文档状态。'
    }
    return
  }
  hideDeletePanel()
  if (message) {
    message.textContent = '删除成功。'
  }
  await refreshDocuments()
}

async function loadAdminShell() {
  authProfile = await authorizedJson('/api/auth/me')
  const kbResult = await authorizedJson('/api/kb')
  const items = kbResult?.items || []
  activeKbId = items[0]?.id || null
  if (activeKbId) {
    await refreshDocuments()
  }
}

window.addEventListener('DOMContentLoaded', () => {
  const confirmButton = document.getElementById('confirm-delete')
  if (confirmButton) {
    confirmButton.addEventListener('click', () => {
      void confirmDelete()
    })
  }
})
```

同时在后端保持 `require_level(session, 3)` 的 `403` 分支。

- [ ] **步骤 4：运行测试验证通过**

运行：`pytest tests/test_document_delete_api.py::test_delete_document_with_invalid_level_returns_403 tests/test_auth_api.py::test_login_then_me_returns_default_admin_level -v`
预期：PASS

- [ ] **步骤 5：Commit**

```bash
git add backend/app/static/admin.js backend/tests/test_document_delete_api.py backend/tests/test_auth_api.py
git commit -m "feat: wire admin document delete interaction"
```

---

### 任务 6：同步文档并做回归验证

**文件：**
- 修改：`docs/api/api-reference.md`
- 修改：`docs/implementation/tech-code-mapping.md`
- 测试：`backend/tests/test_auth_api.py`
- 测试：`backend/tests/test_document_delete_api.py`
- 测试：`backend/tests/test_frontend_shell.py`

- [ ] **步骤 1：编写失败的测试**

```python
def test_plan_requires_document_delete_docs_are_updated():
    assert "DELETE `/api/kb/{kb_id}/documents/{doc_id}`" in api_reference_text
    assert "level" in api_reference_text
```

- [ ] **步骤 2：运行测试验证失败**

运行：`pytest tests/test_docs_sync.py -v`
预期：FAIL，文档尚未记录删除接口和 level 字段。

- [ ] **步骤 3：编写最少实现代码**

在 `docs/api/api-reference.md` 中补充：

```markdown
### DELETE `/api/kb/{kb_id}/documents/{doc_id}`
- 需要 Bearer Token
- 需要 `level >= 3`
- 成功返回 `{ "deleted": true }`
```

并更新：

```markdown
### GET `/api/auth/me`
{
  "user_id": "admin",
  "username": "admin",
  "level": 3
}
```

在 `docs/implementation/tech-code-mapping.md` 中补充：

```markdown
- `backend/app/services/session_store.py`
- `backend/app/services/auth_service.py`
- `backend/app/services/db_document_service.py`
- `backend/app/static/admin.js`
```

- [ ] **步骤 4：运行测试验证通过**

运行：`pytest tests/test_auth_api.py tests/test_document_delete_api.py tests/test_frontend_shell.py -v && pytest -q`
预期：PASS，新增测试与全量后端测试全绿。

- [ ] **步骤 5：Commit**

```bash
git add docs/api/api-reference.md docs/implementation/tech-code-mapping.md backend/tests
git commit -m "feat: add secure document hard delete workflow"
```
