# 问答运营闭环 v1 设计

**日期：** 2026-07-08

**目标：** 在现有知识库、文档、权限和同步问答能力基础上，补齐问答记录、用户反馈与知识缺口归集，让系统从“可问答”推进到“可运营”。

---

## 1. 设计范围

本阶段采用方案 B：**问答记录 + 用户反馈 + 知识缺口归集**。

核心闭环为：

```text
用户提问
→ 系统回答并保存问答记录
→ 用户反馈答案是否有用
→ 负反馈形成知识缺口
→ 管理员查看知识缺口
→ 管理员标记已处理或忽略
```

本阶段要把前面已经完成的能力串起来：

- 知识库管理
- 文档上传、解析、删除
- 知识库级用户权限
- 本地同步问答接口
- 管理员工作台

### 1.1 本阶段包含

1. 数据库模式下保存问答会话和问答消息。
2. `/api/qa/ask/sync` 返回 `conversation_id` 和 `message_id`。
3. 用户可查询自己的会话列表和会话消息。
4. 用户可对自己的问答消息提交有用 / 无用反馈。
5. 负反馈自动生成 `open` 知识缺口。
6. 管理员可查看知识缺口列表。
7. 管理员可将知识缺口标记为 `resolved` 或 `ignored`。
8. `/admin` 增加最小知识缺口展示和处理入口。

### 1.2 本阶段不包含

- 完整用户聊天页面。
- 多轮上下文增强。
- 管理员查看所有用户完整会话。
- issue 搜索、筛选、批量处理和负责人分派。
- 自动补知识向导。
- 复杂统计图和运营看板。
- 基于 `sources` 为空的自动缺口归集。

---

## 2. 数据模型设计

### 2.1 `conversations`

用于归集一组连续问答。

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | integer | 会话 ID |
| `kb_id` | integer | 所属知识库 |
| `user_id` | integer | 提问用户 |
| `title` | string | 会话标题，默认使用首个问题截断 |
| `created_at` | datetime | 创建时间 |
| `updated_at` | datetime | 更新时间 |

阶段 2 暂不依赖该表做复杂上下文，只用于历史记录和反馈归因。

### 2.2 `messages`

用于记录一次完整 QA 回合。

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | integer | 消息 ID |
| `conversation_id` | integer | 所属会话 |
| `kb_id` | integer | 所属知识库 |
| `user_id` | integer | 提问用户 |
| `question` | text | 用户问题 |
| `answer` | text | 系统答案 |
| `sources` | JSON/text | 引用来源快照 |
| `created_at` | datetime | 创建时间 |

一条 `messages` 记录对应一次完整问答，而不是拆成 user / assistant 两条消息。这样一期实现和运营查看更简单。

### 2.3 `message_feedback`

用于保存用户对答案的反馈。

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | integer | 反馈 ID |
| `message_id` | integer | 被反馈的问答消息 |
| `user_id` | integer | 反馈用户 |
| `is_helpful` | boolean | 是否有用 |
| `feedback_text` | text | 用户补充反馈 |
| `created_at` | datetime | 创建时间 |

同一用户对同一条消息重复反馈时，阶段 2 可采用覆盖更新，避免生成多条重复反馈。

### 2.4 `knowledge_issues`

当前已有 `/api/issues` 空端点，本阶段接入真实数据。

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | integer | 知识缺口 ID |
| `kb_id` | integer | 所属知识库 |
| `message_id` | integer | 来源问答消息 |
| `question` | text | 原始问题 |
| `reason` | string | 产生原因，阶段 2 使用 `negative_feedback` |
| `feedback_text` | text | 用户补充反馈 |
| `status` | string | `open` / `resolved` / `ignored` |
| `created_at` | datetime | 创建时间 |
| `updated_at` | datetime | 更新时间 |

阶段 2 不设计负责人、优先级、SLA 和分类字段。

---

## 3. API 设计

### 3.1 增强 `POST /api/qa/ask/sync`

现有同步问答接口继续作为问答入口。

增强行为：

1. 执行原有 RAG 问答逻辑。
2. 数据库模式下写入 `conversations` 和 `messages`。
3. 请求不带 `conversation_id` 时，自动创建新会话。
4. 请求带 `conversation_id` 时，追加到已有会话。
5. 响应新增 `conversation_id` 和 `message_id`。

响应示例：

```json
{
  "answer": "SOS 报警可以在设置中关闭。",
  "sources": [],
  "conversation_id": 1,
  "message_id": 12
}
```

权限要求：

- 用户必须拥有目标知识库的 `can_view`。

当前内存模式可保持兼容：允许不持久化记录，但响应字段可为空或省略。数据库模式必须返回真实 ID。

### 3.2 `GET /api/qa/conversations?kb_id=1`

返回当前用户在某知识库下的会话列表。

响应示例：

```json
{
  "items": [
    {
      "id": 1,
      "kb_id": 1,
      "title": "SOS 报警怎么关闭",
      "created_at": "2026-07-08T10:00:00",
      "updated_at": "2026-07-08T10:05:00"
    }
  ]
}
```

权限要求：

- 用户必须对该知识库拥有 `can_view`。
- 只返回当前用户自己的会话。

### 3.3 `GET /api/qa/conversations/{conversation_id}/messages`

返回某会话下的问答消息列表。

响应示例：

```json
{
  "items": [
    {
      "id": 12,
      "question": "SOS 报警怎么关闭",
      "answer": "SOS 报警可以在设置中关闭。",
      "sources": [],
      "created_at": "2026-07-08T10:00:00"
    }
  ]
}
```

权限要求：

- 当前用户必须是会话所属用户。
- 当前用户必须对会话所属知识库拥有 `can_view`。

阶段 2 暂不提供管理员查看所有用户会话接口。

### 3.4 `POST /api/qa/feedback`

保存答案反馈。

请求示例：

```json
{
  "message_id": 12,
  "is_helpful": false,
  "feedback_text": "回答没有说清楚具体菜单路径"
}
```

行为：

1. 校验当前用户是该消息的提问者。
2. 校验当前用户仍拥有消息所属知识库的 `can_view`。
3. 保存或覆盖 `message_feedback`。
4. 当 `is_helpful=false` 时，创建或更新一条 `knowledge_issues`：
   - `reason=negative_feedback`
   - `status=open`

响应示例：

```json
{
  "saved": true,
  "issue_id": 5
}
```

当 `is_helpful=true` 时，不创建知识缺口，`issue_id` 返回 `null`。

### 3.5 增强 `GET /api/issues?kb_id=1&status=open`

返回知识缺口列表。

响应示例：

```json
{
  "items": [
    {
      "id": 5,
      "kb_id": 1,
      "message_id": 12,
      "question": "SOS 报警怎么关闭",
      "reason": "negative_feedback",
      "feedback_text": "回答没有说清楚具体菜单路径",
      "status": "open",
      "created_at": "2026-07-08T10:00:00"
    }
  ]
}
```

权限要求：

- 用户必须对该知识库拥有 `can_grant`。
- 阶段 2 中，`can_grant` 表示该用户可处理知识缺口。

### 3.6 `PUT /api/issues/{issue_id}`

更新知识缺口状态。

请求示例：

```json
{
  "status": "resolved"
}
```

允许状态：

- `open`
- `resolved`
- `ignored`

响应示例：

```json
{
  "id": 5,
  "status": "resolved"
}
```

权限要求：

- 用户必须对 issue 所属知识库拥有 `can_grant`。

---

## 4. 管理台页面设计

本阶段不做完整用户聊天页面，只在 `/admin` 增加最小知识缺口区域。

### 4.1 新增区域

在管理员工作台中增加：

```html
<section id="issue-list"></section>
```

显示内容：

- 原始问题
- 产生原因
- 用户反馈文本
- 状态
- 标记已处理按钮
- 忽略按钮

### 4.2 前端行为

1. 切换知识库后加载 `open` issues。
2. 点击“标记已处理”调用 `PUT /api/issues/{issue_id}`，状态设为 `resolved`。
3. 点击“忽略”调用 `PUT /api/issues/{issue_id}`，状态设为 `ignored`。
4. 更新成功后刷新 issue 列表。
5. 如果当前用户没有 `can_grant`，接口会返回 403；前端阶段 2 只显示错误提示，不做复杂按钮显隐。

### 4.3 页面边界

阶段 2 不做：

- issue 搜索
- issue 负责人
- 批量处理
- 缺口统计图
- 一键补文档向导
- 聊天用户页面

---

## 5. 自动归集规则

阶段 2 只启用一条规则：

### 5.1 负反馈生成知识缺口

当用户提交：

```json
{
  "is_helpful": false
}
```

系统创建或更新知识缺口：

```text
reason = negative_feedback
status = open
```

### 5.2 暂不启用无来源自动归集

虽然可以根据 `sources` 为空创建 `reason=no_sources` 的 issue，但阶段 2 暂不启用。

原因：

1. 当前 `SimpleLLM` 和本地检索仍是 MVP 骨架。
2. `sources` 为空不一定等于答案不可用。
3. 由用户负反馈触发更可靠，误报更少。

---

## 6. 权限策略

本阶段沿用知识库级权限模型。

| 操作 | 权限要求 |
|------|----------|
| 同步问答并记录消息 | `can_view` |
| 查看自己的会话列表 | `can_view` |
| 查看自己的会话消息 | `can_view` + 会话本人 |
| 提交答案反馈 | `can_view` + 消息本人 |
| 查看知识缺口 | `can_grant` |
| 更新知识缺口状态 | `can_grant` |

阶段 2 不引入系统管理员全局权限，也不引入角色继承和文档级权限。

---

## 7. 测试策略

### 7.1 后端测试

新增测试文件：

```text
backend/tests/test_qa_ops_api.py
```

覆盖：

1. 问答后创建 conversation 和 message。
2. 带 `conversation_id` 的第二次提问追加到同一会话。
3. 当前用户可以查询自己的会话列表。
4. 当前用户可以查询自己的会话消息。
5. 正反馈只保存反馈，不创建 issue。
6. 负反馈会创建 `open` issue。
7. 管理员可按知识库查看 `open` issues。
8. 管理员可把 issue 标记为 `resolved`。
9. 管理员可把 issue 标记为 `ignored`。
10. 无 `can_grant` 用户不能查看 issue。
11. 非提问者不能反馈别人的 message。

### 7.2 前端壳测试

扩展：

```text
backend/tests/test_frontend_shell.py
```

覆盖：

- `/admin` 包含 `id="issue-list"`。
- `admin.js` 包含加载 issue 的函数。
- `admin.js` 包含更新 issue 状态的函数。

### 7.3 文档同步

更新：

- `docs/api/api-reference.md`
- `docs/implementation/tech-code-mapping.md`

新增：

- `docs/superpowers/plans/2026-07-08-qa-ops-loop-v1-plan.md`

---

## 8. 验收标准

若满足以下条件，则阶段 2：问答运营闭环 v1 视为完成：

1. `/api/qa/ask/sync` 在数据库模式下返回 `conversation_id` 和 `message_id`。
2. 问答记录可持久化。
3. 用户可以查询自己的会话列表。
4. 用户可以查询自己的会话消息。
5. 用户可以对自己的答案提交反馈。
6. 正反馈不生成知识缺口。
7. 负反馈生成 `open` 知识缺口。
8. 管理员可以查看知识缺口。
9. 管理员可以把知识缺口标记为 `resolved` 或 `ignored`。
10. 管理台存在最小知识缺口展示和处理入口。
11. API 文档和技术映射文档同步更新。
12. 新增测试和完整回归通过。

---

## 9. 后续扩展方向

阶段 2 完成后，可继续推进：

1. 查询用户页面 v1：知识库选择、提问、答案来源、反馈按钮。
2. 知识缺口处理工作台：搜索、筛选、负责人、批量处理。
3. 自动补知识入口：从 issue 跳转上传文档或补充 FAQ。
4. 运营看板：高频问题、负反馈率、知识缺口数量、处理时长。
5. 无来源自动归集：在检索链路稳定后再启用 `reason=no_sources`。
6. 真实向量库和知识图谱接入后，将缺口归因到文档、chunk 或图谱实体。
