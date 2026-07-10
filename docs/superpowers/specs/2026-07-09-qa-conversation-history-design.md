# 查询用户页面会话历史侧栏设计

**日期：** 2026-07-09

**目标：** 在现有 `/qa` 查询用户页面基础上，增加会话历史侧栏和历史消息展示能力，让查询用户可以查看自己的历史问答，并在选中会话中继续追问。

---

## 1. 设计范围

本阶段继续沿用现有静态页面，不引入新的前端框架，也不新增后端业务 API。

复用现有接口：

```http
GET /api/qa/conversations?kb_id={kb_id}
GET /api/qa/conversations/{conversation_id}/messages
POST /api/qa/ask/sync
```

核心流程：

```text
进入 /qa
→ 选择知识库
→ 加载该知识库下当前用户的会话列表
→ 点击会话
→ 加载历史问答消息
→ 继续在该会话下追问
→ 提问成功后刷新会话列表和历史消息
```

---

## 2. 页面结构

在 `backend/app/static/qa.html` 增加两个区域：

```html
<section id="qa-conversation-list"></section>
<section id="qa-message-list"></section>
```

职责：

| 区域 | 说明 |
|------|------|
| `qa-conversation-list` | 展示当前知识库下当前用户的历史会话标题 |
| `qa-message-list` | 展示选中会话内的历史问答消息 |

页面暂不做复杂布局，只保证结构和交互可用。

---

## 3. 前端状态

在 `qa.js` 增加：

```javascript
let conversations = []
let messages = []
```

并复用已有状态：

```javascript
let activeKbId = null
let activeConversationId = null
let activeMessageId = null
```

状态含义：

| 状态 | 说明 |
|------|------|
| `conversations` | 当前知识库下当前用户会话列表 |
| `messages` | 当前选中会话的问答消息 |
| `activeConversationId` | 当前选中或当前追问的会话 ID |
| `activeMessageId` | 当前最后一条回答对应的 message ID，用于反馈 |

---

## 4. 前端行为

### 4.1 加载会话列表

以下时机调用 `loadConversations()`：

1. 页面初始化并加载知识库后；
2. 切换知识库后；
3. 提问成功后。

请求：

```http
GET /api/qa/conversations?kb_id={activeKbId}
```

行为：

- 成功后更新 `conversations`；
- 渲染到 `#qa-conversation-list`；
- 无会话时显示“暂无历史会话”。

### 4.2 点击历史会话

点击会话后调用 `loadConversationMessages(conversationId)`：

```http
GET /api/qa/conversations/{conversation_id}/messages
```

行为：

- 设置 `activeConversationId`；
- 更新 `messages`；
- 渲染到 `#qa-message-list`；
- 若存在消息，设置最后一条消息 ID 为 `activeMessageId`；
- 同步展示最后一条回答和来源到当前答案区。

### 4.3 提问成功后刷新历史

`submitQuestion()` 成功后：

- 更新 `activeConversationId`；
- 更新 `activeMessageId`；
- 刷新 `loadConversations()`；
- 若已有 `activeConversationId`，刷新 `loadConversationMessages(activeConversationId)`；
- 当前答案区继续展示最新回答。

### 4.4 切换知识库

切换知识库后：

- 更新 `activeKbId`；
- 清空 `activeConversationId`；
- 清空 `activeMessageId`；
- 清空 `messages`；
- 清空答案和来源；
- 加载新知识库的会话列表。

---

## 5. 本阶段不做

- 新建会话按钮；
- 删除会话；
- 会话重命名；
- 会话搜索；
- 分页；
- Markdown 渲染；
- 流式回答；
- 多轮上下文优化；
- 开源 Chat UI 集成。

开源 Chat UI 选型将作为后续独立任务处理。

---

## 6. 测试策略

扩展 `backend/tests/test_frontend_shell.py`：

1. `/qa` 页面包含：
   - `id="qa-conversation-list"`
   - `id="qa-message-list"`
2. `qa.js` 包含：
   - `let conversations = []`
   - `let messages = []`
   - `loadConversations`
   - `renderConversations`
   - `loadConversationMessages`
   - `renderMessages`
3. 继续运行：
   - `pytest tests/test_frontend_shell.py -v`
   - `pytest tests/test_qa_ops_api.py -v`
   - `pytest -q`

---

## 7. 验收标准

完成后应满足：

1. `/qa` 页面包含历史会话区域；
2. `/qa` 页面包含历史消息区域；
3. `qa.js` 可加载当前知识库会话列表；
4. `qa.js` 可点击会话并加载消息；
5. 切换知识库时重置当前会话和消息；
6. 提问成功后刷新会话列表和消息列表；
7. 当前反馈仍使用最新 `activeMessageId`；
8. 文档同步更新；
9. 前端壳测试、问答运营测试、完整回归通过。
