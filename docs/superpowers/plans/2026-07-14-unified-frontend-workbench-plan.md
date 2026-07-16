# 统一后台工作台前端优化实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 将 `/login`、`/documents`、`/admin`、`/qa` 优化为统一的原生后台工作台界面，提升真实样本包验收时的可读性和可操作性。

**架构：** 继续使用现有 FastAPI 静态 HTML/CSS/JS，不引入框架或构建工具。先扩展共享 CSS 设计系统，再逐页优化 HTML 结构，并尽量只做必要 JS 增强；必须保留现有 DOM ID 和 API 调用链路。

**技术栈：** FastAPI 静态文件、原生 HTML、原生 CSS、原生 JavaScript、pytest 前端壳测试。

---

## 文件结构与职责

| 文件 | 变更 | 职责 |
|---|---|---|
| `backend/app/static/app.css` | 修改 | 扩展统一后台布局、卡片、状态、按钮、响应式规则。 |
| `backend/app/static/login.html` | 修改 | 登录/注册页改为左右双栏正式入口，展示验收账号。 |
| `backend/app/static/documents.html` | 修改 | 文档工作台改为更清晰的三栏分类、列表、详情布局。 |
| `backend/app/static/documents.js` | 修改 | 增加标题搜索、状态样式、下载提示和更清晰的渲染文案。 |
| `backend/app/static/admin.html` | 修改 | 管理台改为分区卡片化后台布局，保留现有 ID。 |
| `backend/app/static/qa.html` | 修改 | 问答页改为知识库/会话、消息、来源与反馈布局。 |
| `backend/tests/test_frontend_shell.py` | 修改 | 增加统一布局和关键交互钩子测试。 |

---

### 任务 1：扩展共享 CSS 设计系统

**文件：**
- 修改：`backend/app/static/app.css`
- 测试：`backend/tests/test_frontend_shell.py`

- [ ] **步骤 1：写失败测试**

在 `backend/tests/test_frontend_shell.py` 增加：

```python
def test_shared_styles_define_dashboard_workbench_components():
    css = (Path(__file__).resolve().parents[1] / "app" / "static" / "app.css").read_text(encoding="utf-8")

    assert ".layout-dashboard" in css
    assert ".sidebar" in css
    assert ".toolbar" in css
    assert ".data-card" in css
    assert ".metric-card" in css
    assert ".status-badge--success" in css
    assert ".status-badge--warning" in css
    assert ".status-badge--danger" in css
    assert ".primary-action" in css
    assert ".field-list" in css
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
cd backend && pytest tests/test_frontend_shell.py::test_shared_styles_define_dashboard_workbench_components -v
```

预期：FAIL，缺少上述 CSS 类。

- [ ] **步骤 3：扩展 CSS**

在 `backend/app/static/app.css` 增加完整类：

```css
.layout-dashboard { display: grid; grid-template-columns: 280px minmax(0, 1fr); gap: 20px; align-items: start; }
.sidebar { position: sticky; top: 18px; display: grid; gap: 14px; }
.content-grid { display: grid; gap: 18px; }
.toolbar { display: flex; flex-wrap: wrap; gap: 12px; align-items: center; justify-content: space-between; }
.data-card, .metric-card { border: 1px solid var(--border); border-radius: var(--radius); background: var(--surface-raised); padding: 16px; }
.metric-card strong { display: block; font-size: 1.6rem; color: var(--text); }
.status-badge--success { background: rgb(74 222 128 / 14%); color: var(--success); }
.status-badge--warning { background: rgb(251 191 36 / 14%); color: var(--warning); }
.status-badge--danger { background: rgb(251 113 133 / 14%); color: var(--danger); }
.primary-action { background: var(--accent-strong); color: #06111a; font-weight: 700; }
.secondary-action { background: var(--surface-raised); color: var(--text); }
.danger-action { background: rgb(251 113 133 / 16%); color: var(--danger); }
.field-list { display: grid; gap: 10px; margin: 0; }
.field-list div { display: flex; justify-content: space-between; gap: 16px; border-bottom: 1px solid var(--border); padding-bottom: 8px; }
.field-list dt { color: var(--muted); }
.field-list dd { margin: 0; text-align: right; }
.split-panel { display: grid; grid-template-columns: 1fr 1fr; gap: 18px; }
```

并在已有 `@media (max-width: 900px)` 中加入：

```css
.layout-dashboard, .split-panel { grid-template-columns: 1fr; }
.sidebar { position: static; }
```

- [ ] **步骤 4：运行测试验证通过**

运行：

```bash
cd backend && pytest tests/test_frontend_shell.py::test_shared_styles_define_dashboard_workbench_components -v
```

预期：PASS。

---

### 任务 2：优化登录/注册页

**文件：**
- 修改：`backend/app/static/login.html`
- 测试：`backend/tests/test_frontend_shell.py`

- [ ] **步骤 1：写失败测试**

增加：

```python
def test_login_page_contains_product_intro_and_acceptance_accounts():
    client = TestClient(create_app())
    page = client.get("/login").text

    assert 'class="login-hero"' in page
    assert "权限分级" in page
    assert "原文件下载" in page
    assert "验收账号" in page
    assert "admin / Demo12345" in page
    assert "sales_cn / Demo12345" in page
    assert "finance_user / Demo12345" in page
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
cd backend && pytest tests/test_frontend_shell.py::test_login_page_contains_product_intro_and_acceptance_accounts -v
```

预期：FAIL，当前登录页没有介绍和验收账号区。

- [ ] **步骤 3：修改 `login.html`**

将主体改为：

```html
<main class="app-shell auth-layout login-hero">
  <section class="panel login-intro">
    <p class="status-badge status-badge--success">AI Knowledge Base</p>
    <h1>企业 AI 知识库</h1>
    <p>统一管理产品资料、权限访问、原文件下载与问答检索。</p>
    <div class="split-panel">
      <article class="data-card"><strong>权限分级</strong><span>按资料分类授权访问、下载与编辑。</span></article>
      <article class="data-card"><strong>原文件下载</strong><span>DOCX/PDF/PPTX/XLSX 均保留原文件下载。</span></article>
    </div>
    <section class="data-card acceptance-accounts">
      <h2>验收账号</h2>
      <p>admin / Demo12345</p>
      <p>sales_cn / Demo12345</p>
      <p>delivery_user / Demo12345</p>
      <p>finance_user / Demo12345</p>
    </section>
  </section>
  <section class="panel auth-card">
    ...保留现有 login-message、login-form、registration-form、show-registration、show-login...
  </section>
</main>
```

- [ ] **步骤 4：运行登录页测试**

运行：

```bash
cd backend && pytest tests/test_frontend_shell.py::test_login_page_contains_product_intro_and_acceptance_accounts tests/test_frontend_shell.py::test_shells_load_shared_workbench_styles_and_login_registration_script -v
```

预期：PASS。

---

### 任务 3：优化 `/documents` 主工作台

**文件：**
- 修改：`backend/app/static/documents.html`
- 修改：`backend/app/static/documents.js`
- 测试：`backend/tests/test_frontend_shell.py`

- [ ] **步骤 1：写失败测试**

增加：

```python
def test_documents_workbench_contains_search_metrics_and_download_guidance():
    client = TestClient(create_app())
    page = client.get("/documents").text
    script = (Path(__file__).resolve().parents[1] / "app" / "static" / "documents.js").read_text(encoding="utf-8")

    assert 'class="layout-dashboard documents-dashboard"' in page
    assert 'id="documents-search"' in page
    assert 'id="documents-total-count"' in page
    assert 'id="documents-download-hint"' in page
    assert "matchesDocumentSearch" in script
    assert "statusBadgeClass" in script
    assert "仅可下载，不进入问答索引" in script
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
cd backend && pytest tests/test_frontend_shell.py::test_documents_workbench_contains_search_metrics_and_download_guidance -v
```

预期：FAIL。

- [ ] **步骤 3：修改 `documents.html`**

保留所有现有 ID，新增：

```html
<section class="layout-dashboard documents-dashboard">
  <aside class="panel sidebar documents-sidebar">...</aside>
  <section class="content-grid">
    <section class="panel toolbar">
      <input id="documents-search" placeholder="搜索文件名、产品、资料分类" />
      <span id="documents-total-count" class="status-badge">0 份文档</span>
    </section>
    <section class="panel documents-main">...</section>
  </section>
  <aside class="panel documents-detail-panel">...
    <p id="documents-download-hint" class="message"></p>
  </aside>
</section>
```

- [ ] **步骤 4：修改 `documents.js`**

新增搜索和状态样式：

```javascript
function matchesDocumentSearch(documentItem) {
  const keyword = document.getElementById('documents-search')?.value?.trim().toLowerCase() || ''
  if (!keyword) return true
  return [documentItem.title, documentItem.product, documentItem.product_line, documentItem.file_type]
    .filter(Boolean)
    .some((value) => String(value).toLowerCase().includes(keyword))
}

function statusBadgeClass(item) {
  if (item.status === 'parsed') return 'status-badge status-badge--success'
  if (item.status === 'stored_unsupported') return 'status-badge status-badge--warning'
  return 'status-badge'
}
```

并让 `applyDocumentFilters()` 加上：

```javascript
matchesDocumentSearch(documentItem)
```

`renderDocuments()` 更新 `documents-total-count`。`renderDocumentDetail()` 设置：

```javascript
const hint = detail.status === 'stored_unsupported'
  ? '仅可下载，不进入问答索引。'
  : detail.download_available ? '可下载原始文件。' : '原文件当前不可下载。'
```

- [ ] **步骤 5：运行文档工作台测试**

运行：

```bash
cd backend && pytest tests/test_frontend_shell.py::test_documents_workbench_contains_search_metrics_and_download_guidance tests/test_frontend_shell.py::test_documents_shell_contains_filters_empty_access_and_admin_user_controls -v
```

预期：PASS。

---

### 任务 4：优化 `/admin` 管理台布局

**文件：**
- 修改：`backend/app/static/admin.html`
- 测试：`backend/tests/test_frontend_shell.py`

- [ ] **步骤 1：写失败测试**

增加：

```python
def test_admin_page_contains_dashboard_layout_sections():
    client = TestClient(create_app())
    page = client.get("/admin").text

    assert 'class="layout-dashboard admin-dashboard"' in page
    assert 'class="admin-kb-panel"' in page
    assert 'class="admin-document-panel"' in page
    assert 'class="admin-detail-panel"' in page
    assert 'class="admin-permission-panel"' in page
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
cd backend && pytest tests/test_frontend_shell.py::test_admin_page_contains_dashboard_layout_sections -v
```

预期：FAIL。

- [ ] **步骤 3：修改 `admin.html`**

保留所有已有 ID，将结构包进：

```html
<main class="app-shell">
  <header class="topbar">...</header>
  <section class="layout-dashboard admin-dashboard">
    <aside class="panel sidebar admin-kb-panel">...kb-create 和 kb-list...</aside>
    <section class="content-grid admin-document-panel">...document-upload 和 document-list...</section>
    <aside class="panel admin-detail-panel">...document-detail 和 delete-panel...</aside>
  </section>
  <section class="panel admin-permission-panel split-panel">...permission-editor 和 view-rule-editor...</section>
</main>
```

- [ ] **步骤 4：运行管理台测试**

运行：

```bash
cd backend && pytest tests/test_frontend_shell.py::test_admin_page_contains_dashboard_layout_sections tests/test_frontend_shell.py::test_admin_shell_contains_kb_document_permission_and_delete_regions -v
```

预期：PASS。

---

### 任务 5：优化 `/qa` 问答页布局

**文件：**
- 修改：`backend/app/static/qa.html`
- 测试：`backend/tests/test_frontend_shell.py`

- [ ] **步骤 1：写失败测试**

增加：

```python
def test_qa_page_contains_chat_workbench_layout():
    client = TestClient(create_app())
    page = client.get("/qa").text

    assert 'class="layout-dashboard qa-dashboard"' in page
    assert 'class="qa-sidebar"' in page
    assert 'class="qa-chat-panel"' in page
    assert 'class="qa-source-panel"' in page
    assert 'id="qa-source-panel"' in page
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
cd backend && pytest tests/test_frontend_shell.py::test_qa_page_contains_chat_workbench_layout -v
```

预期：FAIL。

- [ ] **步骤 3：修改 `qa.html`**

保留所有现有 ID，将页面改为：

```html
<main class="app-shell">
  <header class="topbar">...</header>
  <section class="layout-dashboard qa-dashboard">
    <aside class="panel sidebar qa-sidebar">...qa-user、qa-kb-select、qa-conversation-list...</aside>
    <section class="panel qa-chat-panel">...qa-message-list、qa-question、qa-submit、qa-answer-content...</section>
    <aside id="qa-source-panel" class="panel qa-source-panel">...qa-sources、qa-feedback...</aside>
  </section>
</main>
```

- [ ] **步骤 4：运行问答页测试**

运行：

```bash
cd backend && pytest tests/test_frontend_shell.py::test_qa_page_contains_chat_workbench_layout tests/test_frontend_shell.py::test_qa_page_contains_minimal_query_regions -v
```

预期：PASS。

---

### 任务 6：回归验证与收尾

**文件：**
- 修改：无

- [ ] **步骤 1：运行前端壳测试**

运行：

```bash
cd backend && pytest tests/test_frontend_shell.py -v
```

预期：全部 PASS。

- [ ] **步骤 2：运行全量测试**

运行：

```bash
cd backend && pytest -q
```

预期：全部 PASS。

- [ ] **步骤 3：检查 whitespace 和工作区**

运行：

```bash
git diff --check
git status --short
```

预期：无 whitespace 错误；上传文件、数据库、临时目录不应加入提交。

---

## 自检

- 规格覆盖：全局 CSS、登录、文档、管理台、问答页、测试和验收均有任务覆盖。
- 范围控制：未引入框架、构建工具、图标库、文档预览、文档级权限。
- 风险控制：每页保留既有 ID，优先改 HTML/CSS 和少量 JS 展示逻辑。
