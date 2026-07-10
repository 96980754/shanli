# 用户知识视图规则实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 在现有知识库权限和文档元数据基础上，实现用户在单个知识库下的知识视图规则，包括模型、服务、管理 API 和管理员工作台最小配置入口。

**架构：** 新增 `KnowledgeViewRule` 模型和独立 `KnowledgeViewRuleService`。集合字段先以 JSON 字符串存入 Text，服务层负责序列化；API 继续使用现有 Bearer Session 和 `can_grant` 鉴权。本阶段不改造 `DbChunkLoader` 或 RAG 检索。

**技术栈：** FastAPI、SQLAlchemy、Pydantic、pytest、原生 HTML/JS。

---

## 文件结构与职责

- 修改：`backend/app/models/user.py` — 新增 `KnowledgeViewRule` 模型和唯一约束。
- 修改：`backend/app/models/__init__.py` — 导出 `KnowledgeViewRule`。
- 创建：`backend/app/services/view_rule_service.py` — 规则 CRUD、JSON 序列化和文档可见性纯逻辑。
- 修改：`backend/app/main.py` — 注入服务，新增规则查询、设置、删除 API。
- 修改：`backend/app/static/admin.html` — 增加知识视图规则配置区。
- 修改：`backend/app/static/admin.js` — 增加规则加载、保存和删除交互。
- 修改：`backend/tests/test_models_schema.py` — 模型字段和唯一约束测试。
- 创建：`backend/tests/test_view_rule_service.py` — 服务 CRUD、序列化与可见性判断测试。
- 创建：`backend/tests/test_view_rule_api.py` — API 鉴权和业务规则测试。
- 修改：`backend/tests/test_frontend_shell.py` — 管理台规则配置区域测试。
- 修改：`docs/api/api-reference.md` — 记录知识视图规则 API。
- 修改：`docs/implementation/tech-code-mapping.md` — 记录模型、服务和后台映射。

---

### 任务 1：新增 KnowledgeViewRule 模型

**文件：**
- 修改：`backend/app/models/user.py`
- 修改：`backend/app/models/__init__.py`
- 修改：`backend/tests/test_models_schema.py`

- [ ] **步骤 1：编写失败测试**

在 `backend/tests/test_models_schema.py` 新增：

```python
from app.models import KnowledgeViewRule


def test_knowledge_view_rule_persists_json_scopes_and_security_level():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    role = Role(name="管理员", level=3)
    user = User(username="viewer", password_hash="hash", role=role)
    kb = KnowledgeBase(name="产品知识库", visibility="department", owner=user)
    rule = KnowledgeViewRule(
        kb=kb,
        user=user,
        allowed_departments='["售后"]',
        allowed_product_lines='["P368"]',
        allowed_visibilities='["public", "internal"]',
        max_security_level=2,
    )
    session.add_all([role, user, kb, rule])
    session.commit()

    saved = session.query(KnowledgeViewRule).one()
    assert saved.allowed_departments == '["售后"]'
    assert saved.allowed_product_lines == '["P368"]'
    assert saved.allowed_visibilities == '["public", "internal"]'
    assert saved.max_security_level == 2
    assert saved.created_at is not None
    assert saved.updated_at is not None
```

新增唯一约束测试：

```python
def test_knowledge_view_rules_reject_duplicate_kb_user_pairs():
    ...
    first = KnowledgeViewRule(kb=kb, user=user)
    duplicate = KnowledgeViewRule(kb=kb, user=user)
    session.add_all([role, user, kb, first, duplicate])
    with pytest.raises(IntegrityError):
        session.commit()
```

若测试文件当前未导入 `pytest`，补充导入。

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
cd backend && pytest tests/test_models_schema.py::test_knowledge_view_rule_persists_json_scopes_and_security_level tests/test_models_schema.py::test_knowledge_view_rules_reject_duplicate_kb_user_pairs -v
```

预期：FAIL，无法导入 `KnowledgeViewRule`。

- [ ] **步骤 3：实现最少模型代码**

在 `backend/app/models/user.py` 补充导入：

```python
from datetime import datetime
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
```

新增：

```python
class KnowledgeViewRule(Base):
    __tablename__ = "kb_knowledge_view_rules"
    __table_args__ = (
        UniqueConstraint("kb_id", "user_id", name="uq_kb_knowledge_view_rules_kb_user"),
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

    kb: Mapped["KnowledgeBase"] = relationship()
    user: Mapped[User] = relationship()
```

在 `backend/app/models/__init__.py` 导入并加入 `__all__`。

- [ ] **步骤 4：运行测试验证通过**

```bash
cd backend && pytest tests/test_models_schema.py -v
```

预期：全部 PASS。

- [ ] **步骤 5：Commit**

```bash
git add backend/app/models/user.py backend/app/models/__init__.py backend/tests/test_models_schema.py
git commit -m "feat: add knowledge view rule model"
```

---

### 任务 2：实现 KnowledgeViewRuleService

**文件：**
- 创建：`backend/app/services/view_rule_service.py`
- 创建：`backend/tests/test_view_rule_service.py`

- [ ] **步骤 1：编写失败测试**

创建 `backend/tests/test_view_rule_service.py`，复用 SQLite StaticPool 构造 session，覆盖：

```python
def test_set_rule_serializes_scopes_and_get_rule_deserializes_them():
    service = KnowledgeViewRuleService(session)
    saved = service.set_rule(
        kb_id=kb.id,
        user_id=user.id,
        allowed_departments=["售后"],
        allowed_product_lines=["P368", "MCSTARS"],
        allowed_visibilities=["public", "internal"],
        max_security_level=2,
    )

    assert service.serialize_rule(saved)["allowed_departments"] == ["售后"]
    assert service.serialize_rule(saved)["allowed_product_lines"] == ["P368", "MCSTARS"]
```

```python
def test_set_rule_overwrites_existing_rule_instead_of_creating_duplicate():
    ...
    service.set_rule(... allowed_departments=["售后"])
    service.set_rule(... allowed_departments=["交付"])
    assert session.query(KnowledgeViewRule).count() == 1
    assert service.serialize_rule(service.get_rule(kb.id, user.id))["allowed_departments"] == ["交付"]
```

```python
def test_delete_rule_restores_no_rule_state():
    ...
    assert service.delete_rule(kb.id, user.id) is not None
    assert service.get_rule(kb.id, user.id) is None
```

文档可见性测试至少覆盖：

```python
def test_no_rule_allows_document(): ...
def test_empty_rule_dimensions_do_not_restrict_document(): ...
def test_rule_uses_or_within_dimension_and_and_across_dimensions(): ...
def test_rule_blocks_document_above_max_security_level(): ...
```

- [ ] **步骤 2：运行测试验证失败**

```bash
cd backend && pytest tests/test_view_rule_service.py -v
```

预期：FAIL，服务模块不存在。

- [ ] **步骤 3：实现最少服务代码**

创建 `backend/app/services/view_rule_service.py`：

```python
from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy.orm import Session

from app.models import Document, KnowledgeViewRule


class KnowledgeViewRuleService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_rule(self, kb_id: int, user_id: int) -> KnowledgeViewRule | None:
        return (
            self.session.query(KnowledgeViewRule)
            .filter(KnowledgeViewRule.kb_id == kb_id, KnowledgeViewRule.user_id == user_id)
            .one_or_none()
        )

    def set_rule(
        self,
        kb_id: int,
        user_id: int,
        allowed_departments: list[str],
        allowed_product_lines: list[str],
        allowed_visibilities: list[str],
        max_security_level: int | None,
    ) -> KnowledgeViewRule:
        rule = self.get_rule(kb_id, user_id)
        if rule is None:
            rule = KnowledgeViewRule(kb_id=kb_id, user_id=user_id)
            self.session.add(rule)
        rule.allowed_departments = json.dumps(allowed_departments, ensure_ascii=False)
        rule.allowed_product_lines = json.dumps(allowed_product_lines, ensure_ascii=False)
        rule.allowed_visibilities = json.dumps(allowed_visibilities, ensure_ascii=False)
        rule.max_security_level = max_security_level
        rule.updated_at = datetime.utcnow()
        self.session.commit()
        self.session.refresh(rule)
        return rule

    def delete_rule(self, kb_id: int, user_id: int) -> KnowledgeViewRule | None:
        rule = self.get_rule(kb_id, user_id)
        if rule is None:
            return None
        self.session.delete(rule)
        self.session.commit()
        return rule

    def serialize_rule(self, rule: KnowledgeViewRule) -> dict:
        return {
            "kb_id": rule.kb_id,
            "user_id": rule.user_id,
            "allowed_departments": json.loads(rule.allowed_departments or "[]"),
            "allowed_product_lines": json.loads(rule.allowed_product_lines or "[]"),
            "allowed_visibilities": json.loads(rule.allowed_visibilities or "[]"),
            "max_security_level": rule.max_security_level,
        }

    def can_view_document(self, document: Document, rule: KnowledgeViewRule | None) -> bool:
        if rule is None:
            return True
        payload = self.serialize_rule(rule)
        if payload["allowed_departments"] and document.department not in payload["allowed_departments"]:
            return False
        if payload["allowed_product_lines"] and document.product_line not in payload["allowed_product_lines"]:
            return False
        if payload["allowed_visibilities"] and document.visibility not in payload["allowed_visibilities"]:
            return False
        max_level = payload["max_security_level"]
        return max_level is None or document.security_level <= max_level
```

- [ ] **步骤 4：运行测试验证通过**

```bash
cd backend && pytest tests/test_view_rule_service.py -v
```

预期：全部 PASS。

- [ ] **步骤 5：Commit**

```bash
git add backend/app/services/view_rule_service.py backend/tests/test_view_rule_service.py
git commit -m "feat: add knowledge view rule service"
```

---

### 任务 3：实现知识视图规则 API

**文件：**
- 修改：`backend/app/main.py`
- 创建：`backend/tests/test_view_rule_api.py`

- [ ] **步骤 1：编写失败测试**

创建 `backend/tests/test_view_rule_api.py`，使用数据库模式 app，至少覆盖：

```python
def test_can_grant_user_can_set_get_and_delete_view_rule():
    ...
    grant = client.put(
        f"/api/kb/{kb['id']}/permissions/{viewer.id}",
        headers=admin_headers,
        json={"can_view": True, "can_upload": False, "can_delete": False, "can_grant": False},
    )
    assert grant.status_code == 200

    saved = client.put(
        f"/api/kb/{kb['id']}/view-rules/{viewer.id}",
        headers=admin_headers,
        json={
            "allowed_departments": ["售后"],
            "allowed_product_lines": ["P368"],
            "allowed_visibilities": ["public", "internal"],
            "max_security_level": 2,
        },
    )
    assert saved.status_code == 200
    assert saved.json()["effective_scope"] == "restricted"

    fetched = client.get(...)
    assert fetched.json()["allowed_departments"] == ["售后"]

    deleted = client.delete(...)
    assert deleted.status_code == 200
    fetched_after = client.get(...)
    assert fetched_after.json()["effective_scope"] == "all_documents"
```

另外覆盖：

```python
def test_user_without_can_grant_cannot_manage_view_rules(): ...
def test_target_user_requires_can_view_before_rule_can_be_set(): ...
def test_get_missing_rule_returns_all_documents_scope(): ...
```

- [ ] **步骤 2：运行测试验证失败**

```bash
cd backend && pytest tests/test_view_rule_api.py -v
```

预期：FAIL，API 不存在。

- [ ] **步骤 3：实现请求模型、服务注入和 API**

在 `backend/app/main.py` 增加：

```python
from app.services.view_rule_service import KnowledgeViewRuleService
```

请求模型：

```python
class KnowledgeViewRuleUpdate(BaseModel):
    allowed_departments: list[str] = []
    allowed_product_lines: list[str] = []
    allowed_visibilities: list[str] = []
    max_security_level: int | None = None
```

为避免可变默认值，可使用 Pydantic `Field(default_factory=list)`，并导入 `Field`。

在 database service provider 中加入：

```python
"view_rule_service": KnowledgeViewRuleService(session),
```

memory 分支加入 `None`，`create_app()` 设置 `app.state.view_rule_service`。

新增路由：

```python
GET /api/kb/{kb_id}/view-rules/{user_id}
PUT /api/kb/{kb_id}/view-rules/{user_id}
DELETE /api/kb/{kb_id}/view-rules/{user_id}
```

共同规则：

1. `require_session()`；
2. `require_kb_permission(..., "can_grant")`；
3. database 模式使用 `KnowledgeViewRuleService`；
4. memory 模式可返回最小兼容结构或明确 501，但测试与文档必须保持一致；推荐 memory 模式返回内存兼容结构会扩大范围，因此本阶段返回 501 `Knowledge view rules require database mode`。

PUT 前验证目标用户拥有 `can_view`：

```python
if not app.state.kb_service.has_permission(int(kb_id), int(user_id), "can_view"):
    raise HTTPException(status_code=422, detail="Target user requires can_view permission")
```

GET 无规则响应：

```python
{
    "kb_id": int(kb_id),
    "user_id": int(user_id),
    "rule": None,
    "effective_scope": "all_documents",
}
```

GET/PUT 有规则时返回服务序列化结果，并增加：

```python
"effective_scope": "restricted"
```

DELETE 无记录也可幂等返回：

```python
{"deleted": False, "effective_scope": "all_documents"}
```

- [ ] **步骤 4：运行测试验证通过**

```bash
cd backend && pytest tests/test_view_rule_api.py -v
```

预期：全部 PASS。

- [ ] **步骤 5：Commit**

```bash
git add backend/app/main.py backend/tests/test_view_rule_api.py
git commit -m "feat: add knowledge view rule APIs"
```

---

### 任务 4：接入管理员工作台配置入口

**文件：**
- 修改：`backend/app/static/admin.html`
- 修改：`backend/app/static/admin.js`
- 修改：`backend/tests/test_frontend_shell.py`

- [ ] **步骤 1：编写失败测试**

在 `backend/tests/test_frontend_shell.py` 新增：

```python
def test_admin_shell_contains_knowledge_view_rule_editor():
    client = TestClient(create_app())
    response = client.get("/admin")
    admin_js = ADMIN_JS_PATH.read_text(encoding="utf-8")

    assert response.status_code == 200
    assert 'id="view-rule-editor"' in response.text
    assert 'id="view-rule-departments"' in response.text
    assert 'id="view-rule-product-lines"' in response.text
    assert 'id="view-rule-public"' in response.text
    assert 'id="view-rule-internal"' in response.text
    assert 'id="view-rule-restricted"' in response.text
    assert 'id="view-rule-max-security-level"' in response.text
    assert 'id="save-view-rule"' in response.text
    assert 'id="delete-view-rule"' in response.text
    assert "loadViewRule" in admin_js
    assert "saveViewRule" in admin_js
    assert "deleteViewRule" in admin_js
```

- [ ] **步骤 2：运行测试验证失败**

```bash
cd backend && pytest tests/test_frontend_shell.py::test_admin_shell_contains_knowledge_view_rule_editor -v
```

预期：FAIL，页面没有规则编辑区。

- [ ] **步骤 3：实现最少页面与交互**

在 `permission-editor` 后增加：

```html
<section id="view-rule-editor">
  <label>允许部门 <input id="view-rule-departments" placeholder="售后,交付" /></label>
  <label>允许产品线 <input id="view-rule-product-lines" placeholder="P368,MCSTARS" /></label>
  <label><input id="view-rule-public" type="checkbox" />public</label>
  <label><input id="view-rule-internal" type="checkbox" />internal</label>
  <label><input id="view-rule-restricted" type="checkbox" />restricted</label>
  <label>最大密级
    <select id="view-rule-max-security-level">
      <option value="">不限制</option>
      <option value="1">1</option>
      <option value="2">2</option>
      <option value="3">3</option>
    </select>
  </label>
  <button id="load-view-rule" type="button">加载知识视图</button>
  <button id="save-view-rule" type="button">保存知识视图</button>
  <button id="delete-view-rule" type="button">删除知识视图</button>
</section>
```

在 `admin.js` 新增辅助函数：

```javascript
function parseCommaList(value) {
  return value.split(',').map((item) => item.trim()).filter(Boolean)
}
```

`loadViewRule()` 使用现有 `permission-user-id` 作为目标用户 ID，调用 GET 接口并回填控件；无规则时清空控件。

`saveViewRule()` 调用 PUT，payload：

```javascript
{
  allowed_departments: parseCommaList(...),
  allowed_product_lines: parseCommaList(...),
  allowed_visibilities: ['public', 'internal', 'restricted'].filter(...),
  max_security_level: selectedValue ? Number(selectedValue) : null,
}
```

`deleteViewRule()` 调用 DELETE，并清空表单。

在 DOMContentLoaded 中绑定 3 个按钮。

- [ ] **步骤 4：运行测试验证通过**

```bash
cd backend && pytest tests/test_frontend_shell.py::test_admin_shell_contains_knowledge_view_rule_editor -v
```

预期：PASS。

- [ ] **步骤 5：Commit**

```bash
git add backend/app/static/admin.html backend/app/static/admin.js backend/tests/test_frontend_shell.py
git commit -m "feat: add knowledge view rule editor"
```

---

### 任务 5：同步文档并完整回归

**文件：**
- 修改：`docs/api/api-reference.md`
- 修改：`docs/implementation/tech-code-mapping.md`

- [ ] **步骤 1：文档检查红灯**

```bash
grep -R "view-rules" docs/api/api-reference.md || true
```

预期：没有输出或缺少本阶段 API 说明。

- [ ] **步骤 2：更新 API 文档**

记录：

- GET/PUT/DELETE `/api/kb/{kb_id}/view-rules/{user_id}`；
- `can_grant` 要求；
- 目标用户需有 `can_view`；
- 无规则代表 `all_documents`；
- 空集合代表该维度不限制；
- 本阶段尚未接入 RAG 过滤。

- [ ] **步骤 3：更新技术映射**

补充：

- `KnowledgeViewRule` 模型；
- `KnowledgeViewRuleService`；
- 管理台规则编辑区；
- 当前状态是“规则可配置但尚未进入检索链路”。

测试映射新增：

```markdown
| `test_view_rule_service.py` | 知识视图规则 CRUD、序列化和文档可见性判断 |
| `test_view_rule_api.py` | 知识视图规则 API、can_grant 和 can_view 边界 |
```

- [ ] **步骤 4：运行定向验证**

```bash
cd backend && pytest tests/test_models_schema.py -v
cd backend && pytest tests/test_view_rule_service.py -v
cd backend && pytest tests/test_view_rule_api.py -v
cd backend && pytest tests/test_frontend_shell.py -v
```

预期：全部 PASS。

- [ ] **步骤 5：运行完整回归**

```bash
cd backend && pytest -q
```

预期：全部 PASS。

- [ ] **步骤 6：Commit**

```bash
git add backend docs
git commit -m "feat: add configurable knowledge view rules"
```
