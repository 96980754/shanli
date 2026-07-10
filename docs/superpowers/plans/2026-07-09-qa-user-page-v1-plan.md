# 查询用户页面 v1 实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 新增 `/qa` 查询用户页面，让普通用户可以选择可见知识库、提问、查看答案和来源，并提交有用 / 无用反馈。

**架构：** 继续沿用 FastAPI 托管静态 HTML + 原生 JS 的轻量前端结构，不新增后端业务 API。页面复用现有 `/api/auth/me`、`/api/kb`、`/api/qa/ask/sync`、`/api/qa/feedback`，并通过最小前端状态维护当前知识库、会话和最近消息。

**技术栈：** FastAPI、pytest、原生 HTML/CSS/JS。

---

## 文件结构与职责

- 修改：`backend/app/main.py` — 增加 `/qa` 页面入口和 `QA_HTML` 静态文件引用。
- 创建：`backend/app/static/qa.html` — 查询用户页面骨架。
- 创建：`backend/app/static/qa.js` — 查询页面前端交互：加载用户、加载知识库、提问、渲染答案/来源、提交反馈。
- 修改：`backend/tests/test_frontend_shell.py` — 增加 `/qa` 页面和 `qa.js` 关键交互钩子测试。
- 修改：`docs/api/api-reference.md` — 记录 `/qa` 页面入口和查询用户页面依赖接口。
- 修改：`docs/implementation/tech-code-mapping.md` — 记录查询用户页面代码映射。

---

### 任务 1：新增 `/qa` 页面入口和页面骨架

**文件：**
- 修改：`backend/app/main.py`
- 创建：`backend/app/static/qa.html`
- 修改：`backend/tests/test_frontend_shell.py`

- [ ] **步骤 1：编写失败的测试**

在 `backend/tests/test_frontend_shell.py` 新增：

```python
def test_qa_page_is_served():
    client = TestClient(create_app())

    response = client.get("/qa")

    assert response.status_code == 200
    assert "知识库问答" in response.text


def test_qa_page_contains_minimal_query_regions():
    client = TestClient(create_app())

    response = client.get("/qa")

    assert response.status_code == 200
    assert 'id="qa-user"' in response.text
    assert 'id="qa-kb-select"' in response.text
    assert 'id="qa-question"' in response.text
    assert 'id="qa-submit"' in response.text
    assert 'id="qa-answer-content"' in response.text
    assert 'id="qa-sources"' in response.text
    assert 'id="qa-feedback-helpful"' in response.text
    assert 'id="qa-feedback-unhelpful"' in response.text
    assert 'id="qa-message"' in response.text
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
cd backend && pytest tests/test_frontend_shell.py::test_qa_page_is_served tests/test_frontend_shell.py::test_qa_page_contains_minimal_query_regions -v
```

预期：FAIL，`/qa` 返回 404 或页面文件不存在。

- [ ] **步骤 3：编写最少实现代码**

在 `backend/app/main.py` 增加：

```python
QA_HTML = STATIC_DIR / "qa.html"
```

在 `register_routes()` 中增加：

```python
@app.get("/qa", response_class=HTMLResponse)
def qa_page() -> str:
    return QA_HTML.read_text(encoding="utf-8")
```

创建 `backend/app/static/qa.html`：

```html
<!doctype html>
<html lang="zh-CN">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>知识库问答</title>
  </head>
  <body>
    <h1>知识库问答</h1>
    <section id="qa-user"></section>
    <section id="qa-kb-selector">
      <select id="qa-kb-select"></select>
    </section>
    <section id="qa-compose">
      <textarea id="qa-question"></textarea>
      <button id="qa-submit" type="button">提问</button>
    </section>
    <section id="qa-answer">
      <h2>回答</h2>
      <div id="qa-answer-content"></div>
      <section id="qa-sources"></section>
    </section>
    <section id="qa-feedback">
      <textarea id="qa-feedback-text"></textarea>
      <button id="qa-feedback-helpful" type="button">有用</button>
      <button id="qa-feedback-unhelpful" type="button">无用</button>
    </section>
    <p id="qa-message"></p>
    <script src="/static/qa.js"></script>
  </body>
</html>
```

- [ ] **步骤 4：运行测试验证通过**

运行：

```bash
cd backend && pytest tests/test_frontend_shell.py::test_qa_page_is_served tests/test_frontend_shell.py::test_qa_page_contains_minimal_query_regions -v
```

预期：PASS。

- [ ] **步骤 5：Commit**

```bash
git add backend/app/main.py backend/app/static/qa.html backend/tests/test_frontend_shell.py
git commit -m "feat: add qa user page shell"
```

---

### 任务 2：实现查询页面加载用户与知识库

**文件：**
- 创建：`backend/app/static/qa.js`
- 修改：`backend/tests/test_frontend_shell.py`

- [ ] **步骤 1：编写失败的测试**

在 `backend/tests/test_frontend_shell.py` 顶部增加：

```python
QA_JS_PATH = Path(__file__).resolve().parents[1] / "app" / "static" / "qa.js"
```

新增测试：

```python
def test_qa_static_js_contains_auth_and_kb_loading_hooks():
    client = TestClient(create_app())

    response = client.get("/static/qa.js")
    qa_js = QA_JS_PATH.read_text(encoding="utf-8")

    assert response.status_code == 200
    assert "let authProfile = null" in qa_js
    assert "let knowledgeBases = []" in qa_js
    assert "let activeKbId = null" in qa_js
    assert "loadQaShell" in qa_js
    assert "renderKnowledgeBases" in qa_js
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
cd backend && pytest tests/test_frontend_shell.py::test_qa_static_js_contains_auth_and_kb_loading_hooks -v
```

预期：FAIL，`qa.js` 不存在或缺少钩子。

- [ ] **步骤 3：编写最少实现代码**

创建 `backend/app/static/qa.js`：

```javascript
let authProfile = null
let knowledgeBases = []
let activeKbId = null
let activeConversationId = null
let activeMessageId = null
let asking = false

async function requestJson(path, options = {}) {
  const response = await fetch(path, options)
  if (!response.ok) {
    return null
  }
  return response.json()
}

async function authorizedJson(path, options = {}) {
  const token = localStorage.getItem('session_token')
  const headers = { ...(options.headers || {}) }
  if (token) {
    headers.Authorization = `Bearer ${token}`
  }
  return requestJson(path, { ...options, headers })
}

function setQaMessage(message) {
  const node = document.getElementById('qa-message')
  if (node) {
    node.textContent = message
  }
}

function renderUser(profile) {
  const node = document.getElementById('qa-user')
  if (node) {
    node.textContent = profile?.username ? `当前用户：${profile.username}` : '未登录'
  }
}

function renderKnowledgeBases(items) {
  const select = document.getElementById('qa-kb-select')
  if (!select) {
    return
  }
  select.innerHTML = ''
  for (const item of items) {
    const option = document.createElement('option')
    option.value = item.id
    option.textContent = item.name
    select.appendChild(option)
  }
  activeKbId = items[0]?.id || null
  if (!activeKbId) {
    setQaMessage('暂无可用知识库。')
  }
}

async function loadQaShell() {
  authProfile = await authorizedJson('/api/auth/me')
  renderUser(authProfile)
  const result = await authorizedJson('/api/kb')
  knowledgeBases = result?.items || []
  renderKnowledgeBases(knowledgeBases)
}

window.addEventListener('DOMContentLoaded', () => {
  const kbSelect = document.getElementById('qa-kb-select')
  if (kbSelect) {
    kbSelect.addEventListener('change', (event) => {
      activeKbId = event.target.value
      activeConversationId = null
      activeMessageId = null
    })
  }
  if (document.getElementById('qa-kb-select')) {
    void loadQaShell()
  }
})
```

- [ ] **步骤 4：运行测试验证通过**

运行：

```bash
cd backend && pytest tests/test_frontend_shell.py::test_qa_static_js_contains_auth_and_kb_loading_hooks -v
```

预期：PASS。

- [ ] **步骤 5：Commit**

```bash
git add backend/app/static/qa.js backend/tests/test_frontend_shell.py
git commit -m "feat: load user and knowledge bases on qa page"
```

---

### 任务 3：实现提问并渲染答案与来源

**文件：**
- 修改：`backend/app/static/qa.js`
- 修改：`backend/tests/test_frontend_shell.py`

- [ ] **步骤 1：编写失败的测试**

新增测试：

```python
def test_qa_static_js_contains_question_submission_hooks():
    qa_js = QA_JS_PATH.read_text(encoding="utf-8")

    assert "let activeConversationId = null" in qa_js
    assert "let activeMessageId = null" in qa_js
    assert "let asking = false" in qa_js
    assert "submitQuestion" in qa_js
    assert "renderAnswer" in qa_js
    assert "renderSources" in qa_js
    assert "/api/qa/ask/sync" in qa_js
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
cd backend && pytest tests/test_frontend_shell.py::test_qa_static_js_contains_question_submission_hooks -v
```

预期：FAIL，缺少提交和渲染函数。

- [ ] **步骤 3：编写最少实现代码**

在 `qa.js` 增加：

```javascript
function renderAnswer(answer) {
  const node = document.getElementById('qa-answer-content')
  if (node) {
    node.textContent = answer || ''
  }
}

function renderSources(items) {
  const container = document.getElementById('qa-sources')
  if (!container) {
    return
  }
  container.innerHTML = ''
  for (const item of items || []) {
    const row = document.createElement('div')
    const title = document.createElement('p')
    title.textContent = item.doc_title || '未知来源'
    row.appendChild(title)
    const content = document.createElement('p')
    content.textContent = item.content || ''
    row.appendChild(content)
    const score = document.createElement('p')
    score.textContent = item.score === undefined ? '' : `分数：${item.score}`
    row.appendChild(score)
    container.appendChild(row)
  }
}

async function submitQuestion() {
  if (!activeKbId || asking) {
    return
  }
  const input = document.getElementById('qa-question')
  const question = input?.value?.trim()
  if (!question) {
    setQaMessage('请输入问题。')
    return
  }
  asking = true
  const result = await authorizedJson('/api/qa/ask/sync', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      kb_id: String(activeKbId),
      question,
      conversation_id: activeConversationId,
    }),
  })
  asking = false
  if (!result?.answer) {
    setQaMessage('提问失败。')
    return
  }
  activeConversationId = result.conversation_id || activeConversationId
  activeMessageId = result.message_id || null
  renderAnswer(result.answer)
  renderSources(result.sources || [])
  const feedbackText = document.getElementById('qa-feedback-text')
  if (feedbackText) {
    feedbackText.value = ''
  }
  setQaMessage('回答已生成。')
}
```

在 `DOMContentLoaded` 中绑定：

```javascript
const submitButton = document.getElementById('qa-submit')
if (submitButton) {
  submitButton.addEventListener('click', () => {
    void submitQuestion()
  })
}
```

- [ ] **步骤 4：运行测试验证通过**

运行：

```bash
cd backend && pytest tests/test_frontend_shell.py::test_qa_static_js_contains_question_submission_hooks -v
```

预期：PASS。

- [ ] **步骤 5：Commit**

```bash
git add backend/app/static/qa.js backend/tests/test_frontend_shell.py
git commit -m "feat: submit qa questions and render answers"
```

---

### 任务 4：实现答案反馈提交

**文件：**
- 修改：`backend/app/static/qa.js`
- 修改：`backend/tests/test_frontend_shell.py`

- [ ] **步骤 1：编写失败的测试**

新增测试：

```python
def test_qa_static_js_contains_feedback_hooks():
    qa_js = QA_JS_PATH.read_text(encoding="utf-8")

    assert "submitFeedback" in qa_js
    assert "/api/qa/feedback" in qa_js
    assert "qa-feedback-helpful" in qa_js
    assert "qa-feedback-unhelpful" in qa_js
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
cd backend && pytest tests/test_frontend_shell.py::test_qa_static_js_contains_feedback_hooks -v
```

预期：FAIL，缺少反馈提交函数或绑定。

- [ ] **步骤 3：编写最少实现代码**

在 `qa.js` 增加：

```javascript
async function submitFeedback(isHelpful) {
  if (!activeMessageId) {
    setQaMessage('请先提问。')
    return
  }
  const feedbackText = document.getElementById('qa-feedback-text')
  const result = await authorizedJson('/api/qa/feedback', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      message_id: activeMessageId,
      is_helpful: isHelpful,
      feedback_text: feedbackText?.value || '',
    }),
  })
  if (!result?.saved) {
    setQaMessage('反馈提交失败。')
    return
  }
  setQaMessage(isHelpful ? '感谢反馈。' : '已记录问题，管理员会处理。')
}
```

在 `DOMContentLoaded` 中绑定：

```javascript
const helpfulButton = document.getElementById('qa-feedback-helpful')
if (helpfulButton) {
  helpfulButton.addEventListener('click', () => {
    void submitFeedback(true)
  })
}
const unhelpfulButton = document.getElementById('qa-feedback-unhelpful')
if (unhelpfulButton) {
  unhelpfulButton.addEventListener('click', () => {
    void submitFeedback(false)
  })
}
```

- [ ] **步骤 4：运行测试验证通过**

运行：

```bash
cd backend && pytest tests/test_frontend_shell.py::test_qa_static_js_contains_feedback_hooks -v
```

预期：PASS。

- [ ] **步骤 5：Commit**

```bash
git add backend/app/static/qa.js backend/tests/test_frontend_shell.py
git commit -m "feat: submit qa answer feedback"
```

---

### 任务 5：同步文档并运行回归

**文件：**
- 修改：`docs/api/api-reference.md`
- 修改：`docs/implementation/tech-code-mapping.md`
- 测试：`backend/tests/test_frontend_shell.py`
- 测试：`backend/tests/test_qa_ops_api.py`

- [ ] **步骤 1：文档同步检查红灯**

运行：

```bash
grep -R "GET /qa" docs/api/api-reference.md || true
```

预期：没有输出。

- [ ] **步骤 2：更新 API 文档**

在 `docs/api/api-reference.md` 前端页面入口章节补充：

```markdown
### GET `/qa`

返回知识查询用户问答页面。

当前页面文件：`backend/app/static/qa.html`，交互脚本：`backend/app/static/qa.js`。

页面支持：
- 加载当前用户；
- 加载当前用户可见知识库；
- 提交问题到 `/api/qa/ask/sync`；
- 展示答案和来源；
- 提交有用 / 无用反馈到 `/api/qa/feedback`。
```

- [ ] **步骤 3：更新技术映射文档**

在 `docs/implementation/tech-code-mapping.md` 总体对应表补充：

```markdown
| 查询用户问答页 | `backend/app/static/qa.html`, `backend/app/static/qa.js`, `backend/app/main.py` | ✅ 阶段 3 v1 | 后续接会话历史侧栏、Markdown、SSE 流式输出和来源跳转 |
```

在测试对应关系表补充：

```markdown
| `test_frontend_shell.py` | 登录页、管理台壳、查询用户问答页静态结构和 JS 关键交互钩子 |
```

- [ ] **步骤 4：运行定向验证**

运行：

```bash
cd backend && pytest tests/test_frontend_shell.py -v
cd backend && pytest tests/test_qa_ops_api.py -v
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
git add backend/app/main.py backend/app/static/qa.html backend/app/static/qa.js backend/tests/test_frontend_shell.py docs/api/api-reference.md docs/implementation/tech-code-mapping.md
git commit -m "feat: add qa user page"
```
