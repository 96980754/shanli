# 查询用户页面 v1 设计

**日期：** 2026-07-09

**目标：** 在已具备知识库权限、同步问答、问答记录、答案反馈和知识缺口闭环的基础上，为知识查询用户提供一个最小可用的 Web 问答入口。

---

## 1. 设计范围

本阶段建设 **查询用户页面 v1**。

核心流程为：

```text
登录
→ 进入 /qa
→ 选择自己可见的知识库
→ 输入问题
→ 查看答案和来源
→ 对答案反馈有用 / 无用
→ 负反馈进入知识缺口闭环
```

本阶段重点是把已完成的后端能力暴露给查询用户，而不是构建复杂聊天产品。

### 1.1 本阶段包含

1. 新增 `/qa` 静态页面入口。
2. 新增 `qa.html` 页面骨架。
3. 新增 `qa.js` 原生前端交互。
4. 加载当前用户信息。
5. 加载当前用户可见知识库。
6. 支持选择知识库并提交问题。
7. 展示回答内容。
8. 展示来源片段。
9. 支持提交有用 / 无用反馈。
10. 无用反馈复用阶段 2 的知识缺口闭环。
11. 同步 API 文档和技术映射文档。
12. 补充前端壳测试与回归验证。

### 1.2 本阶段不包含

- 完整聊天历史侧栏。
- 多会话管理 UI。
- Markdown 富文本渲染。
- SSE 流式输出。
- 来源跳转到文档详情。
- 用户上传文档。
- 图片 / 语音问答。
- 移动端适配。
- UI 框架重构。

---

## 2. 页面入口与文件

新增页面入口：

```http
GET /qa
```

新增文件：

```text
backend/app/static/qa.html
backend/app/static/qa.js
```

页面继续采用静态 HTML + 原生 JavaScript，与现有 `/login`、`/admin` 保持一致。

---

## 3. 页面结构设计

`qa.html` 使用最小结构：

```html
<h1>知识库问答</h1>

<section id="qa-user"></section>

<section id="qa-kb-selector">
  <select id="qa-kb-select"></select>
</section>

<section id="qa-compose">
  <textarea id="qa-question"></textarea>
  <button id="qa-submit">提问</button>
</section>

<section id="qa-answer">
  <h2>回答</h2>
  <div id="qa-answer-content"></div>
  <section id="qa-sources"></section>
</section>

<section id="qa-feedback">
  <textarea id="qa-feedback-text"></textarea>
  <button id="qa-feedback-helpful">有用</button>
  <button id="qa-feedback-unhelpful">无用</button>
</section>

<p id="qa-message"></p>
```

---

## 4. 前端状态设计

`qa.js` 维护以下状态：

```javascript
let authProfile = null
let knowledgeBases = []
let activeKbId = null
let activeConversationId = null
let activeMessageId = null
let asking = false
```

字段含义：

| 状态 | 说明 |
|------|------|
| `authProfile` | 当前登录用户信息 |
| `knowledgeBases` | 当前用户可见知识库 |
| `activeKbId` | 当前选择知识库 |
| `activeConversationId` | 当前会话 ID |
| `activeMessageId` | 最近一次回答对应的消息 ID |
| `asking` | 防止重复提交问题 |

---

## 5. 页面行为设计

### 5.1 页面加载

页面加载后执行：

1. 从 `localStorage` 读取 `session_token`。
2. 调用 `/api/auth/me` 获取当前用户。
3. 调用 `/api/kb` 获取当前用户可见知识库。
4. 默认选中第一个知识库。
5. 如果无知识库，显示“暂无可用知识库”。

### 5.2 提问

点击“提问”后：

1. 校验已选择知识库。
2. 校验问题非空。
3. 调用：

```http
POST /api/qa/ask/sync
```

请求头：

```text
Authorization: Bearer <token>
```

请求体：

```json
{
  "kb_id": "1",
  "question": "SOS 报警怎么关闭",
  "conversation_id": null
}
```

响应后：

- 展示 `answer`。
- 展示 `sources`。
- 保存 `conversation_id`。
- 保存 `message_id`。
- 清空反馈文本。
- 显示“回答已生成”。

### 5.3 来源展示

`sources` 最小展示：

- 文档标题 `doc_title`。
- 片段内容 `content`。
- 分数 `score`。

不做原文预览和来源跳转。

### 5.4 答案反馈

点击“有用”或“无用”后调用：

```http
POST /api/qa/feedback
```

请求头：

```text
Authorization: Bearer <token>
```

请求体：

```json
{
  "message_id": 12,
  "is_helpful": false,
  "feedback_text": "回答没有菜单路径"
}
```

行为：

- 如果没有 `activeMessageId`，提示“请先提问”。
- 有用反馈保存后显示“感谢反馈”。
- 无用反馈保存后显示“已记录问题，管理员会处理”。
- 后端自动生成知识缺口。

---

## 6. 权限策略

沿用现有权限模型。

| 行为 | 权限 |
|------|------|
| 获取知识库列表 | 只返回当前用户有 `can_view` 的知识库 |
| 提问 | `can_view` |
| 提交反馈 | 本人消息 + `can_view` |

本阶段不新增权限模型。

---

## 7. 测试策略

### 7.1 前端壳测试

扩展：

```text
backend/tests/test_frontend_shell.py
```

覆盖：

1. `/qa` 返回 200。
2. 页面包含：
   - `id="qa-user"`
   - `id="qa-kb-select"`
   - `id="qa-question"`
   - `id="qa-submit"`
   - `id="qa-answer-content"`
   - `id="qa-sources"`
   - `id="qa-feedback-helpful"`
   - `id="qa-feedback-unhelpful"`
   - `id="qa-message"`
3. `/static/qa.js` 可访问。
4. `qa.js` 包含：
   - `activeConversationId`
   - `activeMessageId`
   - `submitQuestion`
   - `submitFeedback`

### 7.2 后端回归

本阶段不新增后端业务 API，复用已有接口。

回归命令：

```bash
cd backend && pytest tests/test_frontend_shell.py -v
cd backend && pytest tests/test_qa_ops_api.py -v
cd backend && pytest -q
```

---

## 8. 验收标准

阶段 3 完成后，应满足：

1. `/qa` 页面可访问。
2. 查询用户可看到当前登录用户。
3. 查询用户可选择自己有 `can_view` 的知识库。
4. 查询用户可提交问题。
5. 页面可展示答案。
6. 页面可展示来源。
7. 页面可提交有用 / 无用反馈。
8. 无用反馈能进入已有知识缺口闭环。
9. API 文档和技术映射文档同步更新。
10. 前端壳测试、问答运营测试、完整回归通过。

---

## 9. 后续扩展方向

阶段 3 完成后，可继续推进：

1. 会话历史侧栏。
2. 多会话切换。
3. Markdown 渲染。
4. SSE 流式回答。
5. 来源跳转到文档详情。
6. 常见问题推荐。
7. 查询用户个人反馈历史。
8. 移动端适配。
