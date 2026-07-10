# 知识库管理员后台 v1 实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 为知识库系统补齐知识库管理员后台 v1，支持基于现有 `users` 表的知识库用户级可累加权限，以及知识库与文档的最小可操作管理闭环。

**架构：** 后端继续沿用 FastAPI 单体入口与内存/数据库双模式服务结构，在认证 session 中保留当前用户信息，并新增知识库用户权限模型、权限接口和现有业务接口的权限判断。前端继续使用静态 HTML + 原生 JS，把 `/admin` 升级为三段式管理员工作台，支持知识库选择、文档管理、权限编辑和页面内确认交互。

**技术栈：** FastAPI、Pydantic、SQLAlchemy、pytest、原生 HTML/CSS/JS。

---

## 文件结构与职责

- 修改：`backend/app/models/user.py` — 补齐知识库用户权限模型字段，支持 `can_view/can_upload/can_delete/can_grant`。
- 修改：`backend/app/services/session_store.py` — session 中保留当前登录用户标识信息。
- 修改：`backend/app/services/auth_service.py` — 登录成功时保留 admin 默认身份信息。
- 修改：`backend/app/main.py` — 新增权限读写接口；为知识库、文档接口接入权限控制；创建知识库后自动授予创建者 4 项权限。
- 修改：`backend/app/services/kb_service.py` — 内存模式支持知识库权限存储、可见性过滤、授权编辑。
- 修改：`backend/app/services/db_kb_service.py` — 数据库模式支持知识库授权查询、写入、删除与创建者默认授权。
- 修改：`backend/app/services/document_service.py` — 内存模式按权限语义配合文档列表、详情、删除。
- 修改：`backend/app/services/db_document_service.py` — 数据库模式配合权限后的文档操作与硬删除。
- 创建：`backend/tests/test_kb_permissions_api.py` — 权限接口、可见性过滤、默认授权与鉴权。
- 修改：`backend/tests/test_auth_api.py` — 认证成功后 `/api/auth/me` 返回用户身份数据。
- 修改：`backend/tests/test_kb_crud_api.py` — 知识库列表/详情/删除接入权限后的行为验证。
- 修改：`backend/tests/test_document_detail_api.py` — 文档详情接入权限后的行为验证。
- 修改：`backend/tests/test_document_delete_api.py` — 文档删除在用户级权限下的行为验证。
- 修改：`backend/app/static/admin.html` — 增加知识库区、文档区、权限区、删除确认区。
- 修改：`backend/app/static/admin.js` — 打通知识库切换、权限编辑、文档删除、列表刷新。
- 修改：`backend/tests/test_frontend_shell.py` — 验证管理员后台页面骨架。
- 修改：`docs/api/api-reference.md` — 记录知识库权限接口与基于权限的访问约束。
- 修改：`docs/implementation/tech-code-mapping.md` — 记录管理员后台 v1 的模型、接口和前端映射。

---

### 任务 1：落地知识库用户权限模型与默认授权

**文件：**
- 修改：`backend/app/models/user.py`
- 修改：`backend/app/services/db_kb_service.py`
- 修改：`backend/app/services/kb_service.py`
- 创建：`backend/tests/test_kb_permissions_api.py`

- [ ] **步骤 1：编写失败的测试**

```python
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.db import Base
from app.main import create_app
from app.models import KnowledgeBasePermission, Role, User


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


def test_create_knowledge_base_grants_creator_all_permissions_in_database_mode():
    app = build_database_app()
    client = TestClient(app)

    created = client.post(
        "/api/kb",
        json={"name": "管理员知识库", "description": "desc", "visibility": "department"},
    ).json()

    permission = (
        app.state.db_session.query(KnowledgeBasePermission)
        .filter(KnowledgeBasePermission.kb_id == created["id"])
        .one()
    )

    assert permission.can_view is True
    assert permission.can_upload is True
    assert permission.can_delete is True
    assert permission.can_grant is True
```

- [ ] **步骤 2：运行测试验证失败**

运行：`pytest tests/test_kb_permissions_api.py::test_create_knowledge_base_grants_creator_all_permissions_in_database_mode -v`
预期：FAIL，报错权限字段不存在，或知识库创建后未生成默认授权。

- [ ] **步骤 3：编写最少实现代码**

```python
# backend/app/models/user.py
class KnowledgeBasePermission(Base):
    __tablename__ = "kb_permissions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    kb_id: Mapped[int] = mapped_column(ForeignKey("knowledge_bases.id"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    can_view: Mapped[bool] = mapped_column(Boolean, default=False)
    can_upload: Mapped[bool] = mapped_column(Boolean, default=False)
    can_delete: Mapped[bool] = mapped_column(Boolean, default=False)
    can_grant: Mapped[bool] = mapped_column(Boolean, default=False)
```

```python
# backend/app/services/db_kb_service.py
kb = KnowledgeBase(...)
self.session.add(kb)
self.session.commit()
self.session.refresh(kb)
permission = KnowledgeBasePermission(
    kb_id=kb.id,
    user_id=owner_id,
    can_view=True,
    can_upload=True,
    can_delete=True,
    can_grant=True,
)
self.session.add(permission)
self.session.commit()
return kb
```

```python
# backend/app/services/kb_service.py
item["permissions"] = {
    owner_id: {
        "can_view": True,
        "can_upload": True,
        "can_delete": True,
        "can_grant": True,
    }
}
```

- [ ] **步骤 4：运行测试验证通过**

运行：`pytest tests/test_kb_permissions_api.py::test_create_knowledge_base_grants_creator_all_permissions_in_database_mode -v`
预期：PASS

- [ ] **步骤 5：Commit**

```bash
git add backend/app/models/user.py backend/app/services/db_kb_service.py backend/app/services/kb_service.py backend/tests/test_kb_permissions_api.py
git commit -m "feat: add cumulative kb permission model"
```

---

### 任务 2：实现知识库权限查询/设置/移除接口

**文件：**
- 修改：`backend/app/main.py`
- 修改：`backend/app/services/db_kb_service.py`
- 修改：`backend/app/services/kb_service.py`
- 测试：`backend/tests/test_kb_permissions_api.py`

- [ ] **步骤 1：编写失败的测试**

```python
from fastapi.testclient import TestClient

from app.main import create_app


def login_default_admin(client: TestClient) -> str:
    response = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "admin"},
    )
    return response.json()["token"]


def test_grant_user_permissions_then_list_them():
    client = TestClient(create_app())
    token = login_default_admin(client)
    kb = client.post("/api/kb", json={"name": "授权库"}).json()

    grant_response = client.put(
        f"/api/kb/{kb['id']}/permissions/1001",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "can_view": True,
            "can_upload": True,
            "can_delete": False,
            "can_grant": False,
        },
    )

    list_response = client.get(
        f"/api/kb/{kb['id']}/permissions",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert grant_response.status_code == 200
    assert list_response.status_code == 200
    assert list_response.json()["items"][1]["user_id"] == 1001
    assert list_response.json()["items"][1]["can_upload"] is True


def test_remove_user_permissions_deletes_permission_record():
    client = TestClient(create_app())
    token = login_default_admin(client)
    kb = client.post("/api/kb", json={"name": "移除授权库"}).json()
    client.put(
        f"/api/kb/{kb['id']}/permissions/1002",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "can_view": True,
            "can_upload": False,
            "can_delete": False,
            "can_grant": False,
        },
    )

    delete_response = client.delete(
        f"/api/kb/{kb['id']}/permissions/1002",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert delete_response.status_code == 200
    assert delete_response.json() == {"deleted": True}
```

- [ ] **步骤 2：运行测试验证失败**

运行：`pytest tests/test_kb_permissions_api.py::test_grant_user_permissions_then_list_them tests/test_kb_permissions_api.py::test_remove_user_permissions_deletes_permission_record -v`
预期：FAIL，报错 route missing、405 或服务层未实现。

- [ ] **步骤 3：编写最少实现代码**

```python
# backend/app/main.py
class KnowledgeBasePermissionUpdate(BaseModel):
    can_view: bool = False
    can_upload: bool = False
    can_delete: bool = False
    can_grant: bool = False


@app.get("/api/kb/{kb_id}/permissions")
def list_kb_permissions(...):
    ...


@app.put("/api/kb/{kb_id}/permissions/{user_id}")
def set_kb_permission(...):
    ...


@app.delete("/api/kb/{kb_id}/permissions/{user_id}")
def delete_kb_permission(...):
    ...
```

```python
# backend/app/services/kb_service.py
class InMemoryKnowledgeBaseService:
    ...

    def list_permissions(self, kb_id: str) -> list[dict[str, Any]] | None:
        ...

    def set_permission(self, kb_id: str, user_id: str, permission: dict[str, bool]) -> dict[str, Any] | None:
        ...

    def delete_permission(self, kb_id: str, user_id: str) -> dict[str, Any] | None:
        ...
```

```python
# backend/app/services/db_kb_service.py
class DbKnowledgeBaseService:
    ...

    def list_permissions(self, kb_id: int) -> list[KnowledgeBasePermission]:
        ...

    def set_permission(self, kb_id: int, user_id: int, payload: dict[str, bool]) -> KnowledgeBasePermission:
        ...

    def delete_permission(self, kb_id: int, user_id: int) -> KnowledgeBasePermission | None:
        ...
```

这一步只实现接口与服务，不在此任务里接入可见性过滤。

- [ ] **步骤 4：运行测试验证通过**

运行：`pytest tests/test_kb_permissions_api.py::test_grant_user_permissions_then_list_them tests/test_kb_permissions_api.py::test_remove_user_permissions_deletes_permission_record -v`
预期：PASS

- [ ] **步骤 5：Commit**

```bash
git add backend/app/main.py backend/app/services/kb_service.py backend/app/services/db_kb_service.py backend/tests/test_kb_permissions_api.py
git commit -m "feat: add kb permission management api"
```

---

### 任务 3：接入知识库与文档可见性和操作鉴权

**文件：**
- 修改：`backend/app/main.py`
- 修改：`backend/app/services/kb_service.py`
- 修改：`backend/app/services/db_kb_service.py`
- 修改：`backend/tests/test_kb_crud_api.py`
- 修改：`backend/tests/test_document_detail_api.py`
- 修改：`backend/tests/test_document_delete_api.py`

- [ ] **步骤 1：编写失败的测试**

```python
from fastapi.testclient import TestClient

from app.main import create_app


def login_default_admin(client: TestClient) -> str:
    response = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "admin"},
    )
    return response.json()["token"]


def test_ungranted_user_cannot_see_knowledge_base_in_list():
    owner_client = TestClient(create_app())
    owner_token = login_default_admin(owner_client)
    created = owner_client.post("/api/kb", json={"name": "私有库"}).json()
    owner_client.put(
        f"/api/kb/{created['id']}/permissions/2001",
        headers={"Authorization": f"Bearer {owner_token}"},
        json={
            "can_view": False,
            "can_upload": False,
            "can_delete": False,
            "can_grant": False,
        },
    )

    viewer_app = create_app()
    viewer_token = viewer_app.state.session_store.create(user_id="2001", username="viewer", level=1)
    viewer_client = TestClient(viewer_app)

    response = viewer_client.get(
        "/api/kb",
        headers={"Authorization": f"Bearer {viewer_token}"},
    )

    assert response.status_code == 200
    assert response.json()["items"] == []


def test_user_without_can_upload_gets_403_when_uploading_document():
    app = create_app()
    admin_token = app.state.session_store.create(user_id="admin", username="admin", level=3)
    client = TestClient(app)
    kb = client.post("/api/kb", json={"name": "上传权限库"}).json()
    client.put(
        f"/api/kb/{kb['id']}/permissions/3001",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "can_view": True,
            "can_upload": False,
            "can_delete": False,
            "can_grant": False,
        },
    )
    uploader_token = app.state.session_store.create(user_id="3001", username="editor", level=1)

    response = client.post(
        f"/api/kb/{kb['id']}/documents/upload",
        headers={"Authorization": f"Bearer {uploader_token}"},
        files={"file": ("manual.txt", b"content", "text/plain")},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "Permission denied"
```

- [ ] **步骤 2：运行测试验证失败**

运行：`pytest tests/test_kb_crud_api.py tests/test_document_detail_api.py tests/test_document_delete_api.py -v`
预期：FAIL，当前知识库和文档接口尚未按知识库权限过滤和拒绝。

- [ ] **步骤 3：编写最少实现代码**

```python
# backend/app/main.py

def require_kb_permission(app: FastAPI, kb_id: str | int, user_id: str, permission: str) -> None:
    allowed = app.state.kb_service.has_permission(kb_id, user_id, permission)
    if not allowed:
        raise HTTPException(status_code=403, detail="Permission denied")
```

将以下接口接入权限判断：

- `GET /api/kb`：只返回当前用户拥有 `can_view` 的知识库
- `GET /api/kb/{kb_id}`：要求 `can_view`
- `GET /api/kb/{kb_id}/documents`：要求 `can_view`
- `GET /api/kb/{kb_id}/documents/{doc_id}`：要求 `can_view`
- `POST /api/kb/{kb_id}/documents/upload`：要求 `can_upload`
- `DELETE /api/kb/{kb_id}/documents/{doc_id}`：要求 `can_delete`
- `DELETE /api/kb/{kb_id}`：要求 `can_grant`
- 权限接口：要求 `can_grant`

同时在 `kb_service.py` 和 `db_kb_service.py` 中实现：

```python
def has_permission(self, kb_id, user_id, permission: str) -> bool:
    ...


def list_for_user(self, user_id: str | int) -> list[dict[str, Any]] | list[KnowledgeBase]:
    ...
```

- [ ] **步骤 4：运行测试验证通过**

运行：`pytest tests/test_kb_permissions_api.py tests/test_kb_crud_api.py tests/test_document_detail_api.py tests/test_document_delete_api.py -v`
预期：PASS

- [ ] **步骤 5：Commit**

```bash
git add backend/app/main.py backend/app/services/kb_service.py backend/app/services/db_kb_service.py backend/tests/test_kb_crud_api.py backend/tests/test_document_detail_api.py backend/tests/test_document_delete_api.py backend/tests/test_kb_permissions_api.py
git commit -m "feat: enforce kb scoped permissions on kb and document apis"
```

---

### 任务 4：升级管理员工作台页面骨架

**文件：**
- 修改：`backend/app/static/admin.html`
- 修改：`backend/app/static/admin.js`
- 修改：`backend/tests/test_frontend_shell.py`

- [ ] **步骤 1：编写失败的测试**

```python
from fastapi.testclient import TestClient

from app.main import create_app


def test_admin_shell_contains_kb_document_permission_and_delete_regions():
    client = TestClient(create_app())

    response = client.get("/admin")

    assert response.status_code == 200
    assert 'id="kb-list"' in response.text
    assert 'id="document-list"' in response.text
    assert 'id="permission-list"' in response.text
    assert 'id="delete-panel"' in response.text
```

- [ ] **步骤 2：运行测试验证失败**

运行：`pytest tests/test_frontend_shell.py::test_admin_shell_contains_kb_document_permission_and_delete_regions -v`
预期：FAIL，页面中缺少权限区或删除区结构。

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
    <main>
      <section id="kb-list"></section>
      <section id="document-list"></section>
      <aside id="permission-list"></aside>
    </main>
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
let activeKbId = null
let knowledgeBases = []
let documents = []
let permissions = []
let pendingDeleteDocument = null
let pendingDeleteKnowledgeBase = null

function renderKnowledgeBases(items) {
  const container = document.getElementById('kb-list')
  if (!container) return
  container.innerHTML = ''
  for (const item of items) {
    const button = document.createElement('button')
    button.type = 'button'
    button.textContent = item.name
    container.appendChild(button)
  }
}

function renderDocuments(items) {
  const container = document.getElementById('document-list')
  if (!container) return
  container.innerHTML = ''
  for (const item of items) {
    const row = document.createElement('div')
    row.textContent = item.title
    container.appendChild(row)
  }
}

function renderPermissions(items) {
  const container = document.getElementById('permission-list')
  if (!container) return
  container.innerHTML = ''
  for (const item of items) {
    const row = document.createElement('div')
    row.textContent = item.username || String(item.user_id)
    container.appendChild(row)
  }
}
```

- [ ] **步骤 4：运行测试验证通过**

运行：`pytest tests/test_frontend_shell.py::test_admin_shell_contains_kb_document_permission_and_delete_regions -v`
预期：PASS

- [ ] **步骤 5：Commit**

```bash
git add backend/app/static/admin.html backend/app/static/admin.js backend/tests/test_frontend_shell.py
git commit -m "feat: scaffold admin backend v1 workspace"
```

---

### 任务 5：打通管理台权限编辑与文档管理交互

**文件：**
- 修改：`backend/app/static/admin.js`
- 修改：`backend/app/static/admin.html`
- 测试：`backend/tests/test_auth_api.py`
- 测试：`backend/tests/test_document_delete_api.py`
- 测试：`backend/tests/test_frontend_shell.py`

- [ ] **步骤 1：编写失败的测试**

```python
from fastapi.testclient import TestClient

from app.main import create_app


def test_login_then_me_returns_default_admin_profile_with_user_identity():
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
    assert me_response.json()["user_id"] == "admin"
    assert me_response.json()["username"] == "admin"
```

```python
def test_delete_document_without_can_delete_returns_403():
    app = create_app()
    admin_token = app.state.session_store.create(user_id="admin", username="admin", level=3)
    client = TestClient(app)
    kb = client.post("/api/kb", json={"name": "删除权限库"}).json()
    client.put(
        f"/api/kb/{kb['id']}/permissions/4001",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "can_view": True,
            "can_upload": True,
            "can_delete": False,
            "can_grant": False,
        },
    )
    limited_token = app.state.session_store.create(user_id="4001", username="u4001", level=1)
    uploaded = client.post(
        f"/api/kb/{kb['id']}/documents/upload",
        headers={"Authorization": f"Bearer {admin_token}"},
        files={"file": ("manual.txt", b"content", "text/plain")},
    ).json()

    response = client.delete(
        f"/api/kb/{kb['id']}/documents/{uploaded['id']}",
        headers={"Authorization": f"Bearer {limited_token}"},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "Permission denied"
```

- [ ] **步骤 2：运行测试验证失败**

运行：`pytest tests/test_auth_api.py::test_login_then_me_returns_default_admin_profile_with_user_identity tests/test_document_delete_api.py::test_delete_document_without_can_delete_returns_403 -v`
预期：FAIL，前端依赖的用户身份和删除权限限制还未完全串通。

- [ ] **步骤 3：编写最少实现代码**

```javascript
// backend/app/static/admin.js
async function authorizedJson(path, options = {}) {
  const token = localStorage.getItem('session_token')
  const headers = { ...(options.headers || {}) }
  if (token) {
    headers.Authorization = `Bearer ${token}`
  }
  return requestJson(path, { ...options, headers })
}

async function loadAdminShell() {
  authProfile = await authorizedJson('/api/auth/me')
  const kbResult = await authorizedJson('/api/kb')
  knowledgeBases = kbResult?.items || []
  renderKnowledgeBases(knowledgeBases)
  activeKbId = knowledgeBases[0]?.id || null
  if (activeKbId) {
    await refreshKnowledgeBaseView()
  }
}

async function refreshKnowledgeBaseView() {
  const documentResult = await authorizedJson(`/api/kb/${activeKbId}/documents`)
  documents = documentResult?.items || []
  renderDocuments(documents)
  if (authProfile?.can_grant || authProfile?.level === 3) {
    const permissionResult = await authorizedJson(`/api/kb/${activeKbId}/permissions`)
    permissions = permissionResult?.items || []
    renderPermissions(permissions)
  }
}
```

```html
<!-- backend/app/static/admin.html -->
<section id="permission-editor">
  <label>用户 ID <input id="permission-user-id" /></label>
  <label><input id="perm-view" type="checkbox" />查看</label>
  <label><input id="perm-upload" type="checkbox" />上传</label>
  <label><input id="perm-delete" type="checkbox" />删除</label>
  <label><input id="perm-grant" type="checkbox" />授权</label>
  <button id="save-permission" type="button">保存权限</button>
</section>
```

并在 JS 中添加：
- 保存权限调用 `PUT /api/kb/{kb_id}/permissions/{user_id}`
- 删除文档后刷新列表
- 删除知识库后刷新知识库列表
- 页面内提示成功/失败信息

- [ ] **步骤 4：运行测试验证通过**

运行：`pytest tests/test_auth_api.py tests/test_document_delete_api.py tests/test_frontend_shell.py -v`
预期：PASS

- [ ] **步骤 5：Commit**

```bash
git add backend/app/static/admin.js backend/app/static/admin.html backend/tests/test_auth_api.py backend/tests/test_document_delete_api.py backend/tests/test_frontend_shell.py
git commit -m "feat: wire admin workspace permission and document actions"
```

---

### 任务 6：同步文档并做完整回归

**文件：**
- 修改：`docs/api/api-reference.md`
- 修改：`docs/implementation/tech-code-mapping.md`
- 测试：`backend/tests/test_kb_permissions_api.py`
- 测试：`backend/tests/test_kb_crud_api.py`
- 测试：`backend/tests/test_document_detail_api.py`
- 测试：`backend/tests/test_document_delete_api.py`
- 测试：`backend/tests/test_frontend_shell.py`

- [ ] **步骤 1：编写失败的测试**

```python
def test_plan_requires_admin_backend_permission_docs_are_updated():
    assert "GET `/api/kb/{kb_id}/permissions`" in api_reference_text
    assert "PUT `/api/kb/{kb_id}/permissions/{user_id}`" in api_reference_text
    assert "DELETE `/api/kb/{kb_id}/permissions/{user_id}`" in api_reference_text
```

- [ ] **步骤 2：运行测试验证失败**

运行：`pytest tests/test_docs_sync.py -v`
预期：FAIL，文档尚未记录权限接口与管理员后台工作台。

- [ ] **步骤 3：编写最少实现代码**

在 `docs/api/api-reference.md` 中补充：

```markdown
### GET `/api/kb/{kb_id}/permissions`
### PUT `/api/kb/{kb_id}/permissions/{user_id}`
### DELETE `/api/kb/{kb_id}/permissions/{user_id}`
```

并记录：
- `can_view/can_upload/can_delete/can_grant`
- 未授权用户看不到知识库
- 创建者默认拥有 4 项权限

在 `docs/implementation/tech-code-mapping.md` 中补充：

```markdown
- `backend/app/models/user.py`
- `backend/app/services/db_kb_service.py`
- `backend/app/services/kb_service.py`
- `backend/app/static/admin.html`
- `backend/app/static/admin.js`
```

- [ ] **步骤 4：运行测试验证通过**

运行：`pytest tests/test_kb_permissions_api.py tests/test_kb_crud_api.py tests/test_document_detail_api.py tests/test_document_delete_api.py tests/test_frontend_shell.py -v && pytest -q`
预期：PASS，新增测试与完整后端测试集全绿。

- [ ] **步骤 5：Commit**

```bash
git add docs/api/api-reference.md docs/implementation/tech-code-mapping.md backend/tests
git commit -m "feat: add admin backend v1 permission workflow"
```
