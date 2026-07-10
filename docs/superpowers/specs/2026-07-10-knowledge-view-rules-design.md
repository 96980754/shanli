# 用户知识视图规则设计

**日期：** 2026-07-10

**目标：** 在现有知识库级权限和文档元数据基础上，引入用户在单个知识库内的知识视图规则，为下一阶段 RAG 检索前元数据硬过滤提供可配置、可审计的数据基础。

---

## 1. 背景

当前系统已经具备：

- 知识库级用户权限：`can_view/can_upload/can_delete/can_grant`；
- 文档级元数据：`department/product_line/visibility/security_level/tags`；
- 文档上传、详情、问答和反馈闭环。

当前权限粒度仍然是：

> 用户能否进入某个知识库。

下一步需要表达：

> 用户进入知识库后，可以查看其中哪些文档。

本阶段只建设规则模型、管理 API 和后台配置入口，不立即改造 RAG 检索。

---

## 2. 权限层级

最终文档访问判断分两层：

```text
第一层：kb_permissions.can_view
        ↓
第二层：KnowledgeViewRule 元数据范围
```

规则必须满足：

1. `KnowledgeViewRule` 不能自行授予知识库访问权；
2. 用户没有 `can_view` 时，即使存在视图规则，也不能访问知识库；
3. 用户有 `can_view` 且没有视图规则时，兼容当前行为：可查看该知识库全部文档；
4. 用户有 `can_view` 且存在规则时，文档必须满足所有非空限制维度。

---

## 3. 数据模型

新增表：

```text
kb_knowledge_view_rules
```

建议模型：

```python
class KnowledgeViewRule(Base):
    __tablename__ = "kb_knowledge_view_rules"
    __table_args__ = (
        UniqueConstraint(
            "kb_id",
            "user_id",
            name="uq_kb_knowledge_view_rules_kb_user",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    kb_id: Mapped[int] = mapped_column(ForeignKey("knowledge_bases.id"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    allowed_departments: Mapped[str] = mapped_column(Text, default="[]")
    allowed_product_lines: Mapped[str] = mapped_column(Text, default="[]")
    allowed_visibilities: Mapped[str] = mapped_column(Text, default="[]")
    max_security_level: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
```

### 3.1 唯一约束

一个用户在一个知识库下最多一条规则：

```text
(kb_id, user_id)
```

### 3.2 集合存储

当前测试与骨架同时支持 SQLite，集合字段先以 JSON 字符串写入 `Text`：

```json
["售后", "交付"]
```

服务层负责序列化和反序列化。

正式使用 PostgreSQL 后，可以保留 Text JSON，也可以迁移为 JSONB。

---

## 4. 规则语义

### 4.1 无规则记录

含义：

> 只要用户拥有知识库 `can_view`，就可以查看该知识库内全部文档。

这是为了兼容系统当前行为，避免规则功能上线后现有用户突然失去访问能力。

### 4.2 规则中的空集合

某个维度为空集合，表示该维度不限制。

示例：

```json
{
  "allowed_departments": ["售后"],
  "allowed_product_lines": [],
  "allowed_visibilities": ["public", "internal"],
  "max_security_level": 2
}
```

含义：

- 仅限售后部门；
- 产品线不限；
- 可见范围限 public/internal；
- 密级最高为 2。

### 4.3 维度组合

不同维度之间使用 AND：

```text
department 匹配
AND product_line 匹配
AND visibility 匹配
AND security_level <= max_security_level
```

同一维度多个允许值之间使用 OR。

### 4.4 标签规则

`tags` 不进入硬权限规则。

标签只能用于：

- 用户主动筛选；
- 检索增强；
- 运营分类；
- 前端展示。

标签过滤不得绕过部门、产品线、visibility 和密级限制。

---

## 5. 可见性判断

推荐的服务层判断逻辑：

```python
def can_view_document(document, rule):
    if rule is None:
        return True

    if rule.allowed_departments and document.department not in rule.allowed_departments:
        return False

    if rule.allowed_product_lines and document.product_line not in rule.allowed_product_lines:
        return False

    if rule.allowed_visibilities and document.visibility not in rule.allowed_visibilities:
        return False

    if rule.max_security_level is not None and document.security_level > rule.max_security_level:
        return False

    return True
```

本阶段可以在服务层实现为可单测的纯逻辑，但暂不接入 `DbChunkLoader`。

---

## 6. API 设计

所有接口都要求当前用户拥有目标知识库 `can_grant`。

### 6.1 查询指定用户规则

```http
GET /api/kb/{kb_id}/view-rules/{user_id}
```

有规则响应：

```json
{
  "kb_id": 1,
  "user_id": 1001,
  "allowed_departments": ["售后"],
  "allowed_product_lines": ["P368"],
  "allowed_visibilities": ["public", "internal"],
  "max_security_level": 2,
  "effective_scope": "restricted"
}
```

无规则响应：

```json
{
  "kb_id": 1,
  "user_id": 1001,
  "rule": null,
  "effective_scope": "all_documents"
}
```

### 6.2 设置规则

```http
PUT /api/kb/{kb_id}/view-rules/{user_id}
```

请求：

```json
{
  "allowed_departments": ["售后"],
  "allowed_product_lines": ["P368", "MCSTARS"],
  "allowed_visibilities": ["public", "internal"],
  "max_security_level": 2
}
```

语义：

- 整体覆盖该用户当前规则；
- 若不存在则创建；
- 若已存在则更新。

### 6.3 删除规则

```http
DELETE /api/kb/{kb_id}/view-rules/{user_id}
```

删除后：

> 用户若仍拥有 `can_view`，恢复为可查看该知识库全部文档。

---

## 7. 授权校验

### 7.1 当前操作人

查询、设置、删除视图规则均要求：

```text
当前操作人拥有目标知识库 can_grant
```

### 7.2 目标用户

设置规则时要求：

- 目标用户存在；
- 目标知识库存在；
- 目标用户对该知识库拥有 `can_view`。

如果目标用户没有 `can_view`，返回 422 或 409，建议统一使用：

```json
{
  "detail": "Target user requires can_view permission"
}
```

### 7.3 规则不能授权

即使目标用户有规则，只要 `can_view=false`：

- 不能出现在知识库列表；
- 不能查看文档；
- 不能问答；
- 后续检索过滤也不会执行。

---

## 8. 服务层设计

建议新增：

```text
backend/app/services/view_rule_service.py
```

职责：

- 查询规则；
- 创建或覆盖规则；
- 删除规则；
- JSON 字段序列化 / 反序列化；
- 判断文档是否满足规则；
- 后续为 RAG 过滤提供统一接口。

建议接口：

```python
class KnowledgeViewRuleService:
    def get_rule(self, kb_id: int, user_id: int) -> KnowledgeViewRule | None:
        ...

    def set_rule(self, kb_id: int, user_id: int, payload: dict) -> KnowledgeViewRule:
        ...

    def delete_rule(self, kb_id: int, user_id: int) -> KnowledgeViewRule | None:
        ...

    def serialize_rule(self, rule: KnowledgeViewRule) -> dict:
        ...

    def can_view_document(self, document: Document, rule: KnowledgeViewRule | None) -> bool:
        ...
```

---

## 9. 管理台设计

在现有 `permission-editor` 下增加最小规则配置区：

- 允许部门：逗号分隔输入；
- 允许产品线：逗号分隔输入；
- 允许 visibility：复选框；
- 最大密级：下拉框；
- 保存知识视图规则按钮；
- 删除知识视图规则按钮。

一期最小实现可使用普通输入框，不立即引入动态标签选择组件。

保存流程：

1. 管理员输入目标用户 ID；
2. 先确保用户拥有 `can_view`；
3. 填写知识视图范围；
4. 调用 PUT 规则接口；
5. 显示规则保存结果。

---

## 10. 与 RAG 的下一阶段衔接

本阶段完成后，下一阶段检索链路应改造为：

```text
校验 kb_permissions.can_view
→ 获取 KnowledgeViewRule
→ 过滤 Document
→ 只加载可见 DocumentChunk
→ 构建 BM25 / 向量检索候选
→ rerank
→ 返回答案和来源
```

后续接 Milvus 时，映射为 metadata expression。

后续接 Neo4j / GraphRAG 时：

- 由可见文档集合推导可见实体和关系；
- 图谱可见性继承文档可见性；
- 不新增独立节点 / 边 ACL。

---

## 11. 本阶段不包含

- 修改 `DbChunkLoader`；
- RAG 元数据过滤；
- Milvus filter expression；
- Neo4j 子图过滤；
- 组织和部门继承授权；
- 标签硬权限；
- 批量用户规则配置。

---

## 12. 测试策略

### 12.1 模型测试

覆盖：

- 模型字段；
- 默认值；
- `(kb_id, user_id)` 唯一约束。

### 12.2 服务测试

覆盖：

- 创建规则；
- 覆盖规则；
- 删除规则；
- JSON 序列化；
- 无规则返回全量语义；
- 空集合不限制；
- 多维度 AND；
- 同维度 OR；
- 密级上限。

### 12.3 API 测试

覆盖：

- `can_grant` 用户可查询、设置、删除规则；
- 无 `can_grant` 返回 403；
- 目标用户无 `can_view` 时禁止设置规则；
- 无规则返回 `all_documents`；
- 删除规则后恢复 `all_documents`。

### 12.4 前端壳测试

覆盖：

- 规则配置区域存在；
- 输入控件存在；
- `admin.js` 含查询、保存和删除规则钩子。

---

## 13. 验收标准

1. 每个用户在每个知识库下最多一条知识视图规则；
2. `can_grant` 用户可查询、设置和删除规则；
3. 无 `can_grant` 用户不能操作规则；
4. 目标用户必须拥有 `can_view` 才能设置规则；
5. 无规则表示全部文档；
6. 空维度表示该维度不限制；
7. 多维度使用 AND，同维度多个值使用 OR；
8. 标签不参与硬权限；
9. 管理台可配置最小规则；
10. 测试、文档和完整回归通过。
