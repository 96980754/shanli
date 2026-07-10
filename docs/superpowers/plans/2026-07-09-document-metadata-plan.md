# 文档元数据字段设计与落库实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 在现有文档模型、上传链路、文档详情接口和管理员工作台基础上，补齐文档元数据字段落库和展示，为后续元数据硬过滤与图谱权限继承打基础。

**架构：** 保持当前 FastAPI + SQLAlchemy + 静态 HTML/JS 结构不变。先扩展 `Document` 模型和上传接口写入元数据，再让详情接口和 `/admin` 文档详情区可见这些字段，不在本阶段引入用户知识视图规则或检索过滤逻辑。

**技术栈：** FastAPI、SQLAlchemy、pytest、原生 HTML/JS。

---

## 文件结构与职责

- 修改：`backend/app/models/document.py` — 给 `Document` 增加元数据字段和默认值。
- 修改：`backend/app/services/document_service.py` — 内存版文档服务支持记录元数据。
- 修改：`backend/app/services/db_document_service.py` — 数据库版上传支持写入元数据。
- 修改：`backend/app/main.py` — 上传接口读取 multipart 元数据，文档详情接口返回元数据。
- 修改：`backend/app/static/admin.html` — 文档详情区补充元数据显示节点。
- 修改：`backend/app/static/admin.js` — 文档详情渲染元数据字段。
- 修改：`backend/tests/test_models_schema.py` — 验证文档元数据字段可落库。
- 修改：`backend/tests/test_document_api.py` — 覆盖内存模式上传与详情元数据行为。
- 修改：`backend/tests/test_database_mode_api.py` — 覆盖数据库模式上传与详情元数据行为。
- 修改：`backend/tests/test_frontend_shell.py` — 验证管理台文档详情区新增元数据显示节点。
- 修改：`docs/api/api-reference.md` — 同步上传与详情接口元数据字段。
- 修改：`docs/implementation/tech-code-mapping.md` — 同步文档元数据映射。

---

### 任务 1：补齐文档模型元数据字段

**文件：**
- 修改：`backend/app/models/document.py`
- 修改：`backend/tests/test_models_schema.py`

- [ ] **步骤 1：编写失败的测试**

在 `backend/tests/test_models_schema.py` 新增：

```python
def test_document_model_persists_metadata_fields_with_defaults():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    role = Role(name="知识库管理员", level=3)
    user = User(username="admin", password_hash="hash", role=role)
    kb = KnowledgeBase(name="产品知识库", visibility="department", owner=user)
    doc = Document(
        kb=kb,
        title="manual.txt",
        file_type="txt",
        status="pending",
        department="售后",
        product_line="P368",
        visibility="internal",
        security_level=2,
        tags="FAQ,报警",
    )
    session.add_all([role, user, kb, doc])
    session.commit()

    saved = session.query(Document).one()
    assert saved.department == "售后"
    assert saved.product_line == "P368"
    assert saved.visibility == "internal"
    assert saved.security_level == 2
    assert saved.tags == "FAQ,报警"
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
cd backend && pytest tests/test_models_schema.py::test_document_model_persists_metadata_fields_with_defaults -v
```

预期：FAIL，`Document` 还没有这些字段。

- [ ] **步骤 3：编写最少实现代码**

在 `backend/app/models/document.py` 的 `Document` 模型新增：

```python
department: Mapped[str] = mapped_column(String(100), default="")
product_line: Mapped[str] = mapped_column(String(100), default="")
visibility: Mapped[str] = mapped_column(String(30), default="internal")
security_level: Mapped[int] = mapped_column(Integer, default=1)
tags: Mapped[str] = mapped_column(Text, default="")
```

确保已导入 `Integer`。

- [ ] **步骤 4：运行测试验证通过**

运行：

```bash
cd backend && pytest tests/test_models_schema.py::test_document_model_persists_metadata_fields_with_defaults -v
```

预期：PASS。

- [ ] **步骤 5：Commit**

```bash
git add backend/app/models/document.py backend/tests/test_models_schema.py
git commit -m "feat: add document metadata fields"
```

---

### 任务 2：让内存版和数据库版上传支持元数据写入

**文件：**
- 修改：`backend/app/services/document_service.py`
- 修改：`backend/app/services/db_document_service.py`
- 修改：`backend/app/main.py`
- 修改：`backend/tests/test_document_api.py`
- 修改：`backend/tests/test_database_mode_api.py`

- [ ] **步骤 1：编写失败的测试**

在 `backend/tests/test_document_api.py` 新增：

```python
def test_upload_document_records_metadata_fields():
    client = TestClient(create_app())
    token = client.post("/api/auth/login", json={"username": "admin", "password": "admin"}).json()["token"]
    kb = client.post("/api/kb", json={"name": "产品知识库", "visibility": "department"}).json()

    response = client.post(
        f"/api/kb/{kb['id']}/documents/upload",
        headers={"Authorization": f"Bearer {token}"},
        data={
            "department": "售后",
            "product_line": "P368",
            "visibility": "internal",
            "security_level": "2",
            "tags": "FAQ,报警",
        },
        files={"file": ("manual.txt", b"SOS alarm", "text/plain")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["department"] == "售后"
    assert body["product_line"] == "P368"
    assert body["visibility"] == "internal"
    assert body["security_level"] == 2
    assert body["tags"] == "FAQ,报警"
```

在 `backend/tests/test_database_mode_api.py` 新增：

```python
def test_database_mode_upload_persists_metadata_fields(tmp_path):
    app = build_database_app()
    app.state.upload_root = tmp_path
    client = TestClient(app)
    token = login_default_admin(client)
    kb = client.post("/api/kb", json={"name": "元数据知识库", "visibility": "department"}).json()

    response = client.post(
        f"/api/kb/{kb['id']}/documents/upload",
        headers={"Authorization": f"Bearer {token}"},
        data={
            "department": "售后",
            "product_line": "P368",
            "visibility": "internal",
            "security_level": "2",
            "tags": "FAQ,报警",
        },
        files={"file": ("manual.txt", b"SOS alarm", "text/plain")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["department"] == "售后"
    assert body["product_line"] == "P368"
    assert body["visibility"] == "internal"
    assert body["security_level"] == 2
    assert body["tags"] == "FAQ,报警"
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
cd backend && pytest tests/test_document_api.py::test_upload_document_records_metadata_fields tests/test_database_mode_api.py::test_database_mode_upload_persists_metadata_fields -v
```

预期：FAIL，上传接口还不接收这些字段。

- [ ] **步骤 3：编写最少实现代码**

在 `backend/app/main.py` 的上传接口签名扩展为接收表单字段：

```python
async def upload_document(
    kb_id: str,
    file: UploadFile,
    department: str = Form(default=""),
    product_line: str = Form(default=""),
    visibility: str = Form(default="internal"),
    security_level: int = Form(default=1),
    tags: str = Form(default=""),
    authorization: str | None = Header(default=None),
) -> dict[str, Any]:
```

并把这些字段传给 `document_service.upload(...)`。

修改 `InMemoryDocumentService.upload()` 和 `DbDocumentService.upload()`，增加参数：

```python
department: str = ""
product_line: str = ""
visibility: str = "internal"
security_level: int = 1
tags: str = ""
```

在返回结构中补这些字段。

数据库模式下创建 `Document` 时把这些字段写入模型。

- [ ] **步骤 4：运行测试验证通过**

运行：

```bash
cd backend && pytest tests/test_document_api.py::test_upload_document_records_metadata_fields tests/test_database_mode_api.py::test_database_mode_upload_persists_metadata_fields -v
```

预期：PASS。

- [ ] **步骤 5：Commit**

```bash
git add backend/app/main.py backend/app/services/document_service.py backend/app/services/db_document_service.py backend/tests/test_document_api.py backend/tests/test_database_mode_api.py
git commit -m "feat: persist document metadata on upload"
```

---

### 任务 3：文档详情接口返回元数据

**文件：**
- 修改：`backend/app/main.py`
- 修改：`backend/tests/test_document_api.py`
- 修改：`backend/tests/test_document_detail_api.py`

- [ ] **步骤 1：编写失败的测试**

在 `backend/tests/test_document_detail_api.py` 新增：

```python
def test_document_detail_returns_metadata_fields():
    app = build_database_app()
    client = TestClient(app)
    token = login_default_admin(client)
    kb = client.post("/api/kb", json={"name": "产品知识库", "visibility": "department"}).json()

    upload = client.post(
        f"/api/kb/{kb['id']}/documents/upload",
        headers={"Authorization": f"Bearer {token}"},
        data={
            "department": "售后",
            "product_line": "P368",
            "visibility": "internal",
            "security_level": "2",
            "tags": "FAQ,报警",
        },
        files={"file": ("manual.txt", b"SOS alarm", "text/plain")},
    ).json()

    response = client.get(
        f"/api/kb/{kb['id']}/documents/{upload['id']}",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    detail = response.json()
    assert detail["department"] == "售后"
    assert detail["product_line"] == "P368"
    assert detail["visibility"] == "internal"
    assert detail["security_level"] == 2
    assert detail["tags"] == "FAQ,报警"
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
cd backend && pytest tests/test_document_detail_api.py::test_document_detail_returns_metadata_fields -v
```

预期：FAIL，详情接口未返回这些字段。

- [ ] **步骤 3：编写最少实现代码**

在 `backend/app/main.py` 的 `get_document_detail()` 返回结构中增加：

数据库模式：

```python
"department": item.department,
"product_line": item.product_line,
"visibility": item.visibility,
"security_level": item.security_level,
"tags": item.tags,
```

内存模式：

```python
"department": item.get("department", ""),
"product_line": item.get("product_line", ""),
"visibility": item.get("visibility", "internal"),
"security_level": item.get("security_level", 1),
"tags": item.get("tags", ""),
```

- [ ] **步骤 4：运行测试验证通过**

运行：

```bash
cd backend && pytest tests/test_document_detail_api.py::test_document_detail_returns_metadata_fields -v
```

预期：PASS。

- [ ] **步骤 5：Commit**

```bash
git add backend/app/main.py backend/tests/test_document_detail_api.py
git commit -m "feat: return document metadata in detail api"
```

---

### 任务 4：管理台文档详情区展示元数据

**文件：**
- 修改：`backend/app/static/admin.html`
- 修改：`backend/app/static/admin.js`
- 修改：`backend/tests/test_frontend_shell.py`

- [ ] **步骤 1：编写失败的测试**

在 `backend/tests/test_frontend_shell.py` 新增：

```python
def test_admin_shell_contains_document_metadata_detail_fields():
    client = TestClient(create_app())
    response = client.get("/admin")
    admin_js = ADMIN_JS_PATH.read_text(encoding="utf-8")

    assert response.status_code == 200
    assert 'id="document-detail-department"' in response.text
    assert 'id="document-detail-product-line"' in response.text
    assert 'id="document-detail-visibility"' in response.text
    assert 'id="document-detail-security-level"' in response.text
    assert 'id="document-detail-tags"' in response.text
    assert "document-detail-department" in admin_js
    assert "document-detail-product-line" in admin_js
    assert "document-detail-visibility" in admin_js
    assert "document-detail-security-level" in admin_js
    assert "document-detail-tags" in admin_js
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
cd backend && pytest tests/test_frontend_shell.py::test_admin_shell_contains_document_metadata_detail_fields -v
```

预期：FAIL，管理台还没有这些字段。

- [ ] **步骤 3：编写最少实现代码**

在 `admin.html` 的文档详情区增加：

```html
<p id="document-detail-department"></p>
<p id="document-detail-product-line"></p>
<p id="document-detail-visibility"></p>
<p id="document-detail-security-level"></p>
<p id="document-detail-tags"></p>
```

在 `admin.js` 的 `renderDocumentDetail(detail)` 中补：

```javascript
const departmentNode = document.getElementById('document-detail-department')
const productLineNode = document.getElementById('document-detail-product-line')
const visibilityNode = document.getElementById('document-detail-visibility')
const securityLevelNode = document.getElementById('document-detail-security-level')
const tagsNode = document.getElementById('document-detail-tags')
```

在空状态时清空这些字段；有详情时设置：

```javascript
departmentNode.textContent = `部门：${detail.department || ''}`
productLineNode.textContent = `产品线：${detail.product_line || ''}`
visibilityNode.textContent = `可见范围：${detail.visibility || ''}`
securityLevelNode.textContent = `密级：${detail.security_level ?? ''}`
tagsNode.textContent = `标签：${detail.tags || ''}`
```

- [ ] **步骤 4：运行测试验证通过**

运行：

```bash
cd backend && pytest tests/test_frontend_shell.py::test_admin_shell_contains_document_metadata_detail_fields -v
```

预期：PASS。

- [ ] **步骤 5：Commit**

```bash
git add backend/app/static/admin.html backend/app/static/admin.js backend/tests/test_frontend_shell.py
git commit -m "feat: show document metadata in admin detail"
```

---

### 任务 5：同步文档并回归验证

**文件：**
- 修改：`docs/api/api-reference.md`
- 修改：`docs/implementation/tech-code-mapping.md`

- [ ] **步骤 1：文档同步检查红灯**

运行：

```bash
grep -R "security_level" docs/api/api-reference.md || true
```

预期：没有或缺少当前阶段新增字段的完整说明。

- [ ] **步骤 2：更新 API 文档**

在上传接口和文档详情接口文档中补充字段：

- `department`
- `product_line`
- `visibility`
- `security_level`
- `tags`

说明其默认值和当前阶段用途（后续权限过滤底座）。

- [ ] **步骤 3：更新技术映射文档**

在 `docs/implementation/tech-code-mapping.md` 文档上传 / 文档管理区域补充：

- `Document` 模型已具备文档元数据字段；
- `/admin` 文档详情区已显示文档元数据；
- 这些字段用于后续元数据硬过滤和图谱权限继承。

- [ ] **步骤 4：运行定向验证**

运行：

```bash
cd backend && pytest tests/test_models_schema.py -v
cd backend && pytest tests/test_document_api.py -v
cd backend && pytest tests/test_document_detail_api.py -v
cd backend && pytest tests/test_database_mode_api.py -v
cd backend && pytest tests/test_frontend_shell.py -v
```

预期：全部 PASS。

- [ ] **步骤 5：运行完整回归**

运行：

```bash
cd backend && pytest -q
```

预期：全部 PASS。

- [ ] **步骤 6：Commit**

```bash
git add backend docs
git commit -m "feat: add document metadata foundation"
```
