# 管理员工作台文档管理深化实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 在现有知识库管理员后台 v1 的基础上，把文档管理区从骨架推进到真正可操作：支持单文件上传、自动选中新上传文档、查看详情、删除文档并刷新页面状态。

**架构：** 继续沿用静态 HTML + 原生 JS 的管理员工作台结构，不新增后端接口，只复用现有文档上传、列表、详情、删除接口。前端通过最小状态变量（选中文档、详情、上传中状态、页面消息）驱动文档区的上传、列表、详情和删除闭环。

**技术栈：** FastAPI、pytest、原生 HTML/CSS/JS。

---

## 文件结构与职责

- 修改：`backend/app/static/admin.html` — 补齐上传区、文档详情区和更明确的文档管理结构。
- 修改：`backend/app/static/admin.js` — 实现单文件上传、上传后自动选中、详情加载、删除后刷新与详情清空、页面消息更新。
- 修改：`backend/tests/test_frontend_shell.py` — 验证上传区、详情区、消息区与交互入口骨架。
- 修改：`docs/api/api-reference.md` — 记录管理员工作台当前文档管理交互依赖的接口与页面行为。
- 修改：`docs/implementation/tech-code-mapping.md` — 记录文档管理深化后的前端工作台映射。

---

### 任务 1：补齐上传区与详情区页面骨架

**文件：**
- 修改：`backend/app/static/admin.html`
- 修改：`backend/tests/test_frontend_shell.py`

- [ ] **步骤 1：编写失败的测试**

```python
from fastapi.testclient import TestClient

from app.main import create_app


def test_admin_shell_contains_upload_and_document_detail_regions():
    client = TestClient(create_app())

    response = client.get("/admin")

    assert response.status_code == 200
    assert 'id="document-upload-form"' in response.text
    assert 'id="document-file"' in response.text
    assert 'id="upload-document"' in response.text
    assert 'id="document-detail"' in response.text
    assert 'id="document-detail-title"' in response.text
    assert 'id="document-detail-status"' in response.text
    assert 'id="document-detail-block-count"' in response.text
    assert 'id="document-detail-chunk-count"' in response.text
```

- [ ] **步骤 2：运行测试验证失败**

运行：`pytest tests/test_frontend_shell.py::test_admin_shell_contains_upload_and_document_detail_regions -v`
预期：FAIL，页面缺少上传区或详情区容器。

- [ ] **步骤 3：编写最少实现代码**

```html
<!-- backend/app/static/admin.html -->
<section id="document-upload">
  <form id="document-upload-form">
    <input id="document-file" name="file" type="file" />
    <button id="upload-document" type="submit">上传文档</button>
  </form>
</section>
<section id="document-list"></section>
<section id="document-detail">
  <h2 id="document-detail-title">未选择文档</h2>
  <p id="document-detail-type"></p>
  <p id="document-detail-status"></p>
  <p id="document-detail-block-count"></p>
  <p id="document-detail-chunk-count"></p>
</section>
```

只补结构，不在本任务中实现上传和详情联动逻辑。

- [ ] **步骤 4：运行测试验证通过**

运行：`pytest tests/test_frontend_shell.py::test_admin_shell_contains_upload_and_document_detail_regions -v`
预期：PASS

- [ ] **步骤 5：Commit**

```bash
git add backend/app/static/admin.html backend/tests/test_frontend_shell.py
git commit -m "feat: add admin document upload and detail regions"
```

---

### 任务 2：实现上传后刷新列表并自动选中新文档

**文件：**
- 修改：`backend/app/static/admin.js`
- 修改：`backend/app/static/admin.html`
- 修改：`backend/tests/test_frontend_shell.py`

- [ ] **步骤 1：编写失败的测试**

```python
from fastapi.testclient import TestClient

from app.main import create_app


def test_admin_shell_contains_upload_feedback_and_selection_state_hooks():
    client = TestClient(create_app())

    response = client.get("/admin")

    assert response.status_code == 200
    assert 'id="admin-message"' in response.text
    assert 'id="document-detail-title"' in response.text
    assert 'id="document-detail-type"' in response.text
```

- [ ] **步骤 2：运行测试验证失败**

运行：`pytest tests/test_frontend_shell.py::test_admin_shell_contains_upload_feedback_and_selection_state_hooks -v`
预期：如果详情字段或消息区未完整到位则 FAIL。

- [ ] **步骤 3：编写最少实现代码**

```javascript
// backend/app/static/admin.js
let selectedDocumentId = null
let selectedDocumentDetail = null
let uploading = false

function renderDocumentDetail(detail) {
  const titleNode = document.getElementById('document-detail-title')
  const typeNode = document.getElementById('document-detail-type')
  const statusNode = document.getElementById('document-detail-status')
  const blockNode = document.getElementById('document-detail-block-count')
  const chunkNode = document.getElementById('document-detail-chunk-count')
  if (!detail) {
    titleNode.textContent = '未选择文档'
    typeNode.textContent = ''
    statusNode.textContent = ''
    blockNode.textContent = ''
    chunkNode.textContent = ''
    return
  }
  titleNode.textContent = detail.title
  typeNode.textContent = `类型：${detail.file_type}`
  statusNode.textContent = `状态：${detail.status}`
  blockNode.textContent = `块数：${detail.block_count}`
  chunkNode.textContent = `切片数：${detail.chunk_count}`
}

async function loadDocumentDetail(docId) {
  if (!activeKbId || !docId) return
  const detail = await authorizedJson(`/api/kb/${activeKbId}/documents/${docId}`)
  selectedDocumentId = docId
  selectedDocumentDetail = detail
  renderDocumentDetail(detail)
}

async function handleDocumentUpload(event) {
  event.preventDefault()
  if (!activeKbId || uploading) return
  const input = document.getElementById('document-file')
  if (!input?.files?.length) {
    setAdminMessage('请先选择文件。')
    return
  }
  uploading = true
  const formData = new FormData()
  formData.append('file', input.files[0])
  const result = await authorizedJson(`/api/kb/${activeKbId}/documents/upload`, {
    method: 'POST',
    body: formData,
  })
  uploading = false
  if (!result?.id) {
    setAdminMessage('上传失败。')
    return
  }
  setAdminMessage('上传成功。')
  await refreshKnowledgeBaseView()
  await loadDocumentDetail(result.id)
  input.value = ''
}
```

并在 `DOMContentLoaded` 中绑定 `document-upload-form` 的提交事件。

- [ ] **步骤 4：运行测试验证通过**

运行：`pytest tests/test_frontend_shell.py::test_admin_shell_contains_upload_feedback_and_selection_state_hooks -v`
预期：PASS

- [ ] **步骤 5：Commit**

```bash
git add backend/app/static/admin.js backend/app/static/admin.html backend/tests/test_frontend_shell.py
git commit -m "feat: support admin single file upload flow"
```

---

### 任务 3：实现点击文档查看详情与删除后详情清空

**文件：**
- 修改：`backend/app/static/admin.js`
- 修改：`backend/tests/test_frontend_shell.py`

- [ ] **步骤 1：编写失败的测试**

```python
from fastapi.testclient import TestClient

from app.main import create_app


def test_admin_shell_contains_document_detail_fields_and_delete_feedback():
    client = TestClient(create_app())

    response = client.get("/admin")

    assert response.status_code == 200
    assert 'id="document-detail-status"' in response.text
    assert 'id="document-detail-block-count"' in response.text
    assert 'id="document-detail-chunk-count"' in response.text
    assert 'id="delete-message"' in response.text
```

- [ ] **步骤 2：运行测试验证失败**

运行：`pytest tests/test_frontend_shell.py::test_admin_shell_contains_document_detail_fields_and_delete_feedback -v`
预期：若详情字段未齐备则 FAIL。

- [ ] **步骤 3：编写最少实现代码**

```javascript
// backend/app/static/admin.js
function renderDocuments(items) {
  const container = document.getElementById('document-list')
  if (!container) return
  container.innerHTML = ''
  for (const item of items) {
    const row = document.createElement('div')
    const titleButton = document.createElement('button')
    titleButton.type = 'button'
    titleButton.textContent = item.title
    titleButton.addEventListener('click', () => {
      void loadDocumentDetail(item.id)
    })
    row.appendChild(titleButton)

    const deleteButton = document.createElement('button')
    deleteButton.type = 'button'
    deleteButton.textContent = '删除文档'
    deleteButton.addEventListener('click', () => {
      showDeletePanel('document', item.id, item.title)
    })
    row.appendChild(deleteButton)
    container.appendChild(row)
  }
}

async function confirmDelete() {
  if (pendingDeleteDocument && activeKbId) {
    const deletedId = pendingDeleteDocument
    const result = await authorizedJson(`/api/kb/${activeKbId}/documents/${deletedId}`, {
      method: 'DELETE',
    })
    if (!result?.deleted) {
      setAdminMessage('删除文档失败。')
      return
    }
    setAdminMessage('文档删除成功。')
    hideDeletePanel()
    await refreshKnowledgeBaseView()
    if (selectedDocumentId === deletedId) {
      selectedDocumentId = null
      selectedDocumentDetail = null
      renderDocumentDetail(null)
    }
    return
  }
  ...
}
```

- [ ] **步骤 4：运行测试验证通过**

运行：`pytest tests/test_frontend_shell.py::test_admin_shell_contains_document_detail_fields_and_delete_feedback -v`
预期：PASS

- [ ] **步骤 5：Commit**

```bash
git add backend/app/static/admin.js backend/tests/test_frontend_shell.py
git commit -m "feat: load admin document detail and clear it after delete"
```

---

### 任务 4：同步文档并跑回归验证

**文件：**
- 修改：`docs/api/api-reference.md`
- 修改：`docs/implementation/tech-code-mapping.md`
- 测试：`backend/tests/test_frontend_shell.py`

- [ ] **步骤 1：编写失败的测试**

```python
def test_plan_requires_admin_document_workspace_docs_are_updated():
    assert "单文件上传" in admin_backend_spec_text
    assert "上传成功后自动选中新文档" in admin_backend_spec_text
```

- [ ] **步骤 2：运行测试验证失败**

运行：`pytest tests/test_docs_sync.py -v`
预期：FAIL，文档尚未同步记录文档管理深化后的行为。

- [ ] **步骤 3：编写最少实现代码**

在 `docs/api/api-reference.md` 补充：

```markdown
- `/admin` 工作台当前支持单文件上传
- 上传成功后自动刷新列表并加载文档详情
- 删除当前选中文档后清空详情区
```

在 `docs/implementation/tech-code-mapping.md` 补充：

```markdown
- `backend/app/static/admin.html`：上传区、文档详情区
- `backend/app/static/admin.js`：上传后选中详情、删除后清空详情
```

- [ ] **步骤 4：运行测试验证通过**

运行：`pytest tests/test_frontend_shell.py -v && pytest -q`
预期：PASS，前端壳测试和完整后端测试集全绿。

- [ ] **步骤 5：Commit**

```bash
git add docs/api/api-reference.md docs/implementation/tech-code-mapping.md backend/tests backend/app/static/admin.html backend/app/static/admin.js
git commit -m "feat: deepen admin document workspace interactions"
```
