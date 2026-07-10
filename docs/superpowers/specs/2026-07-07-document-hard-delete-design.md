# 文档硬删除与管理台安全删除交互设计

**日期：** 2026-07-07

**目标：** 在当前已具备登录、知识库 CRUD、文档上传与详情能力的基础上，补齐文档硬删除能力，并为管理台增加页面内二次确认删除交互，形成最小但安全的文档管理闭环。

---

## 1. 设计范围

本阶段只解决以下问题：

1. 管理员可以删除指定知识库下的单个文档。
2. 删除行为采用硬删除，关联入库数据一起清理。
3. 删除操作需要登录，并且要求用户级别（level）至少为 3。
4. 管理台页面内提供二次确认提示区，不使用浏览器原生 confirm。
5. 删除成功后，文档列表立即刷新，页面状态同步更新。

本阶段**不包含**以下内容：

- 完整 RBAC 或权限继承体系
- 知识库权限配置接口
- 文档软删除、恢复、回收站
- 批量删除
- 审计日志完善
- 前端完整表格化管理台重构

---

## 2. 当前现状

当前系统已经具备：

- `POST /api/auth/login` 与 `GET /api/auth/me`
- 知识库创建、查询、更新、删除
- 文档上传、文档列表、文档详情
- 最小前端页面：`/login`、`/admin`
- 数据库模式下的 `documents`、`document_chunks`、`parse_tasks`、`content_blocks`

当前缺口是：

- 上传后的文档无法删除
- 管理台只能查看页面壳，不能执行安全的管理操作
- 登录态只返回用户身份，不足以支持最小权限控制

---

## 3. 核心设计决策

### 3.1 删除语义：硬删除

删除文档时，直接物理删除下列数据：

- `documents`
- `document_chunks`
- `parse_tasks`
- `content_blocks`

并同步修正：

- `knowledge_bases.doc_count`

设计原则：

- 删除行为必须是“按文档定向清理”，不能做全局删除。
- `doc_count` 需要同步减少，并确保不会出现负数。

### 3.2 权限策略：level >= 3

本阶段采用最小权限规则：

- 未登录：拒绝删除，返回 `401`
- 已登录但 `level < 3`：拒绝删除，返回 `403`
- `level >= 3`：允许删除

该设计直接复用现有 `Role.level` 概念，不额外引入新的权限模型。

### 3.3 前端确认方式：页面内二次确认提示区

管理台删除交互不使用原生弹窗，而是在页面内显示一个确认区域，内容包括：

- 当前待删除文档标题
- 风险提示文案
- 「确认删除」按钮
- 「取消」按钮
- 操作结果提示区

这样既满足安全优先要求，也为后续扩展错误提示与权限提示保留空间。

---

## 4. 后端接口设计

### 4.1 新增接口

#### DELETE `/api/kb/{kb_id}/documents/{doc_id}`

用于删除指定知识库下的单个文档。

### 4.2 状态码语义

- `200`：删除成功
- `401`：未登录或缺少有效 token
- `403`：用户已登录，但级别不足
- `404`：文档不存在，或文档不属于该知识库

### 4.3 成功响应

```json
{
  "deleted": true
}
```

### 4.4 失败响应

```json
{
  "detail": "Document not found"
}
```

或：

```json
{
  "detail": "Insufficient permission level"
}
```

### 4.5 认证与权限实现

现有 session 数据需要从：

- `user_id`
- `username`

扩展为：

- `user_id`
- `username`
- `level`

因此：

1. `SessionStore` 需要保存 `level`
2. `AuthService.login()` 成功登录时，需要把默认 admin 的 `level=3` 一起写入 session
3. `GET /api/auth/me` 返回中应包含 `level`
4. 删除接口在路由层做最小权限判断

---

## 5. 数据删除策略

### 5.1 内存模式

内存模式下：

- 从指定知识库的文档列表中移除目标文档
- 同步减少知识库 `doc_count`

说明：

- 当前内存模式没有 `document_chunks` / `parse_tasks` / `content_blocks` 的独立存储，因此无需额外级联清理
- 如果文档不存在或不属于该知识库，返回 `None`，由路由转换为 `404`

### 5.2 数据库模式

数据库模式下按以下顺序清理：

1. 校验文档存在且归属于指定 `kb_id`
2. 查询该文档关联的 `parse_tasks`
3. 删除这些 `parse_tasks` 关联的 `content_blocks`
4. 删除该文档关联的 `document_chunks`
5. 删除 `parse_tasks`
6. 删除 `documents` 记录
7. 更新所属知识库 `doc_count`
8. 提交事务

设计要求：

- 事务必须保证删除一致性
- 任一环节失败时，不应出现“文档删了但关联数据残留”的不一致状态

---

## 6. 前端管理台交互设计

### 6.1 页面结构

在 `admin.html` 中新增一个删除确认区域，例如：

- `#delete-panel`
- `#delete-target`
- `#delete-message`
- `#confirm-delete`
- `#cancel-delete`

同时保留现有：

- `#kb-list`
- `#document-list`

### 6.2 页面行为

管理台加载时：

1. 读取本地 `session_token`
2. 请求 `/api/auth/me`
3. 记录当前用户 `level`
4. 加载知识库列表与当前知识库下文档列表

删除交互流程：

1. 用户点击某个文档的「删除」按钮
2. 页面内展示二次确认区
3. 展示待删除文档标题与风险提示
4. 用户点击「确认删除」
5. 前端调用 `DELETE /api/kb/{kb_id}/documents/{doc_id}`
6. 删除成功后：
   - 清空确认区
   - 刷新文档列表
   - 显示成功提示
7. 删除失败后：
   - 保留当前页面状态
   - 在页面内显示错误提示
8. 用户点击「取消」则关闭确认区

### 6.3 删除按钮显示规则

- `level >= 3`：显示删除操作
- `level < 3`：不显示删除按钮，或显示不可用状态

本阶段推荐直接“不显示删除按钮”，减少误操作与额外提示复杂度。

---

## 7. 测试设计

### 7.1 认证与会话测试

补充测试：

- 登录后 `/api/auth/me` 返回 `level`
- session 中包含 `level`

### 7.2 删除接口测试

覆盖以下场景：

#### 内存模式

- 未登录删除返回 `401`
- 级别不足删除返回 `403`
- 管理员删除成功
- 删除后文档列表中消失
- 删除后文档详情返回 `404`
- 删除后 `doc_count` 减少

#### 数据库模式

- 管理员删除成功
- 删除后 `documents` 被删除
- 删除后 `document_chunks` 被删除
- 删除后 `parse_tasks` 被删除
- 删除后 `content_blocks` 被删除
- 删除后 `doc_count` 减少
- 删除不存在文档返回 `404`
- 删除其他知识库下文档返回 `404`

### 7.3 前端壳测试

补充最小页面测试：

- `/admin` 页面包含删除确认区容器或相关标识
- 页面仍能正常返回 `200`
- `login.html` / `admin.html` 引用静态资源路径正确

---

## 8. 影响文件

### 后端

- `backend/app/main.py`
- `backend/app/services/auth_service.py`
- `backend/app/services/session_store.py`
- `backend/app/services/document_service.py`
- `backend/app/services/db_document_service.py`

### 前端

- `backend/app/static/admin.html`
- `backend/app/static/admin.js`

### 测试

- `backend/tests/test_auth_api.py`
- `backend/tests/test_document_detail_api.py`
- 新增：`backend/tests/test_document_delete_api.py`
- `backend/tests/test_frontend_shell.py`

### 文档

- `docs/api/api-reference.md`
- `docs/implementation/tech-code-mapping.md`

---

## 9. 风险与约束

### 风险 1：`main.py` 继续膨胀

当前路由仍集中在 `main.py`。本阶段继续沿用现有模式，不在今天拆分 router，避免扩大范围。

### 风险 2：前端状态仍较原始

当前管理台是静态 HTML + 原生 JS。为了保持范围可控，本阶段只增加最小状态管理，不引入前端框架。

### 风险 3：删除后的检索缓存一致性

当前数据库问答是按请求即时加载 `document_chunks`，因此文档删除后不会长期污染主查询路径。内存态若有前端缓存，则通过刷新文档列表解决页面一致性。

---

## 10. 验收标准

满足以下条件则本阶段完成：

1. 管理员（`level >= 3`）可以删除文档。
2. 未登录用户无法删除文档。
3. 低级别用户无法删除文档。
4. 删除后文档详情返回 `404`。
5. 删除后文档列表中不再出现该文档。
6. 数据库模式下的 `chunks` / `blocks` / `parse_tasks` 被一并清理。
7. 管理台使用页面内确认区执行删除。
8. 所有新增/修改测试通过。
