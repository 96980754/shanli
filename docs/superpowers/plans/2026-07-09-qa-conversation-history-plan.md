# 查询用户页面会话历史侧栏实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 在 `/qa` 查询用户页面增加会话历史列表和历史消息展示，让用户可以点击历史会话查看问答并继续追问。

**架构：** 不新增后端业务 API，复用现有问答运营接口。前端继续使用 `qa.html` + `qa.js`，通过 `conversations` / `messages` 两个最小状态管理历史列表和当前会话消息。

**技术栈：** FastAPI、pytest、原生 HTML/JS。

---

## 文件结构与职责

- 修改：`backend/app/static/qa.html` — 增加 `#qa-conversation-list` 和 `#qa-message-list` 区域。
- 修改：`backend/app/static/qa.js` — 增加会话列表加载、会话消息加载、历史消息渲染，以及提问成功后的历史刷新。
- 修改：`backend/tests/test_frontend_shell.py` — 增加会话历史区域和 JS 关键钩子测试。
- 修改：`docs/api/api-reference.md` — 补充 `/qa` 页面已支持会话历史展示。
- 修改：`docs/implementation/tech-code-mapping.md` — 补充查询页会话历史映射。

---

### 任务 1：补齐页面历史区域

**文件：**
- 修改：`backend/app/static/qa.html`
- 修改：`backend/tests/test_frontend_shell.py`

- [ ] **步骤 1：编写失败的测试**

在 `backend/tests/test_frontend_shell.py` 新增：

```python
def test_qa_page_contains_conversation_history_regions():
    client = TestClient(create_app())

    response = client.get("/qa")

    assert response.status_code == 200
    assert 'id="qa-conversation-list"' in response.text
    assert 'id="qa-message-list"' in response.text
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
cd backend && pytest tests/test_frontend_shell.py::test_qa_page_contains_conversation_history_regions -v
```

预期：FAIL，页面还没有历史区域。

- [ ] **步骤 3：编写最少实现代码**

在 `backend/app/static/qa.html` 的知识库选择区之后增加：

```html
<section id="qa-history">
  <h2>历史会话</h2>
  <section id="qa-conversation-list"></section>
</section>
<section id="qa-message-history">
  <h2>历史消息</h2>
  <section id="qa-message-list"></section>
</section>
```

- [ ] **步骤 4：运行测试验证通过**

运行：

```bash
cd backend && pytest tests/test_frontend_shell.py::test_qa_page_contains_conversation_history_regions -v
```

预期：PASS。

- [ ] **步骤 5：Commit**

```bash
git add backend/app/static/qa.html backend/tests/test_frontend_shell.py
git commit -m "feat: add qa conversation history regions"
```

---

### 任务 2：实现会话列表加载与渲染

**文件：**
- 修改：`backend/app/static/qa.js`
- 修改：`backend/tests/test_frontend_shell.py`

- [ ] **步骤 1：编写失败的测试**

在 `backend/tests/test_frontend_shell.py` 新增：

```python
def test_qa_static_js_contains_conversation_history_hooks():
    qa_js = QA_JS_PATH.read_text(encoding="utf-8")

    assert "let conversations = []" in qa_js
    assert "loadConversations" in qa_js
    assert "renderConversations" in qa_js
    assert "/api/qa/conversations?kb_id=" in qa_js
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
cd backend && pytest tests/test_frontend_shell.py::test_qa_static_js_contains_conversation_history_hooks -v
```

预期：FAIL，`qa.js` 缺少会话历史状态和函数。

- [ ] **步骤 3：编写最少实现代码**

在 `qa.js` 顶部增加：

```javascript
let conversations = []
```

新增：

```javascript
function renderConversations(items) {
  const container = document.getElementById('qa-conversation-list')
  if (!container) {
    return
  }
  container.innerHTML = ''
  if (!items.length) {
    container.textContent = '暂无历史会话。'
    return
  }
  for (const item of items) {
    const button = document.createElement('button')
    button.type = 'button'
    button.textContent = item.title
    button.addEventListener('click', () => {
      void loadConversationMessages(item.id)
    })
    container.appendChild(button)
  }
}

async function loadConversations() {
  if (!activeKbId) {
    conversations = []
    renderConversations(conversations)
    return
  }
  const result = await authorizedJson(`/api/qa/conversations?kb_id=${activeKbId}`)
  conversations = result?.items || []
  renderConversations(conversations)
}
```

在 `loadQaShell()` 调用 `renderKnowledgeBases()` 后增加：

```javascript
await loadConversations()
```

在知识库 select 的 change handler 中增加：

```javascript
void loadConversations()
```

- [ ] **步骤 4：运行测试验证通过**

运行：

```bash
cd backend && pytest tests/test_frontend_shell.py::test_qa_static_js_contains_conversation_history_hooks -v
```

预期：PASS。

- [ ] **步骤 5：Commit**

```bash
git add backend/app/static/qa.js backend/tests/test_frontend_shell.py
git commit -m "feat: load qa conversation history"
```

---

### 任务 3：实现历史消息加载与渲染

**文件：**
- 修改：`backend/app/static/qa.js`
- 修改：`backend/tests/test_frontend_shell.py`

- [ ] **步骤 1：编写失败的测试**

在 `backend/tests/test_frontend_shell.py` 新增：

```python
def test_qa_static_js_contains_conversation_message_hooks():
    qa_js = QA_JS_PATH.read_text(encoding="utf-8")

    assert "let messages = []" in qa_js
    assert "loadConversationMessages" in qa_js
    assert "renderMessages" in qa_js
    assert "/api/qa/conversations/${conversationId}/messages" in qa_js
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
cd backend && pytest tests/test_frontend_shell.py::test_qa_static_js_contains_conversation_message_hooks -v
```

预期：FAIL，`qa.js` 缺少消息状态和加载函数。

- [ ] **步骤 3：编写最少实现代码**

在 `qa.js` 顶部增加：

```javascript
let messages = []
```

新增：

```javascript
function renderMessages(items) {
  const container = document.getElementById('qa-message-list')
  if (!container) {
    return
  }
  container.innerHTML = ''
  if (!items.length) {
    container.textContent = '暂无历史消息。'
    return
  }
  for (const item of items) {
    const row = document.createElement('div')
    const question = document.createElement('p')
    question.textContent = `问：${item.question}`
    row.appendChild(question)
    const answer = document.createElement('p')
    answer.textContent = `答：${item.answer}`
    row.appendChild(answer)
    container.appendChild(row)
  }
}

async function loadConversationMessages(conversationId) {
  const result = await authorizedJson(`/api/qa/conversations/${conversationId}/messages`)
  messages = result?.items || []
  activeConversationId = conversationId
  renderMessages(messages)
  const lastMessage = messages[messages.length - 1]
  activeMessageId = lastMessage?.id || null
  renderAnswer(lastMessage?.answer || '')
  renderSources(lastMessage?.sources || [])
}
```

在知识库切换 handler 中增加：

```javascript
messages = []
renderMessages(messages)
```

- [ ] **步骤 4：运行测试验证通过**

运行：

```bash
cd backend && pytest tests/test_frontend_shell.py::test_qa_static_js_contains_conversation_message_hooks -v
```

预期：PASS。

- [ ] **步骤 5：Commit**

```bash
git add backend/app/static/qa.js backend/tests/test_frontend_shell.py
git commit -m "feat: load qa conversation messages"
```

---

### 任务 4：提问成功后刷新会话历史

**文件：**
- 修改：`backend/app/static/qa.js`
- 修改：`backend/tests/test_frontend_shell.py`

- [ ] **步骤 1：编写失败的测试**

在 `backend/tests/test_frontend_shell.py` 新增：

```python
def test_qa_static_js_refreshes_history_after_question():
    qa_js = QA_JS_PATH.read_text(encoding="utf-8")

    assert "await loadConversations()" in qa_js
    assert "await loadConversationMessages(activeConversationId)" in qa_js
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
cd backend && pytest tests/test_frontend_shell.py::test_qa_static_js_refreshes_history_after_question -v
```

预期：FAIL，提问成功后尚未刷新历史。

- [ ] **步骤 3：编写最少实现代码**

在 `submitQuestion()` 成功设置 `activeConversationId` 和 `activeMessageId` 后增加：

```javascript
await loadConversations()
if (activeConversationId) {
  await loadConversationMessages(activeConversationId)
}
```

确保这段代码在 `setQaMessage('回答已生成。')` 之前或之后均可，但必须在提问成功分支内。

- [ ] **步骤 4：运行测试验证通过**

运行：

```bash
cd backend && pytest tests/test_frontend_shell.py::test_qa_static_js_refreshes_history_after_question -v
```

预期：PASS。

- [ ] **步骤 5：Commit**

```bash
git add backend/app/static/qa.js backend/tests/test_frontend_shell.py
git commit -m "feat: refresh qa history after answering"
```

---

### 任务 5：同步文档并回归

**文件：**
- 修改：`docs/api/api-reference.md`
- 修改：`docs/implementation/tech-code-mapping.md`

- [ ] **步骤 1：文档检查红灯**

运行：

```bash
grep -R "会话历史侧栏" docs/api/api-reference.md || true
```

预期：没有输出。

- [ ] **步骤 2：更新 API 文档**

在 `docs/api/api-reference.md` 的 `/qa` 页面说明中补充：

```markdown
- 页面已支持会话历史侧栏：加载当前知识库下当前用户的历史会话；
- 点击历史会话后，调用 `/api/qa/conversations/{conversation_id}/messages` 展示历史问答；
- 用户在选中历史会话后继续提问，会复用当前 `conversation_id`。
```

- [ ] **步骤 3：更新技术映射文档**

在 `docs/implementation/tech-code-mapping.md` 查询用户问答页对应关系中补充：

```markdown
已支持当前用户加载、可见知识库选择、同步提问、答案/来源展示、有用/无用反馈、会话历史侧栏和历史消息展示。
```

- [ ] **步骤 4：运行定向测试**

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
git add backend/app/static/qa.html backend/app/static/qa.js backend/tests/test_frontend_shell.py docs/api/api-reference.md docs/implementation/tech-code-mapping.md
git commit -m "feat: add qa conversation history"
```
