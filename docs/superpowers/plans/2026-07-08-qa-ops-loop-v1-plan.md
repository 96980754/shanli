# 问答运营闭环 v1 实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 superpowers:subagent-driven-development（推荐）或 superpowers:executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）语法来跟踪进度。

**目标：** 在数据库模式下补齐问答记录、答案反馈和知识缺口归集，并在管理员工作台提供最小知识缺口处理入口。

**架构：** 继续保留 `backend/app/main.py` 当前集中路由结构，新增专注的 `QaOpsService` 承担会话、消息、反馈和 issue 状态流转。数据库模型放在现有 `backend/app/models/kg_ops.py` 中，避免过早拆分目录；内存模式保持原有空状态兼容。

**技术栈：** FastAPI、SQLAlchemy、SQLite 测试模式、pytest、原生 HTML/JS。

---

## 文件结构与职责

- 修改：`backend/app/models/kg_ops.py` — 补齐 `Conversation`、`Message`、`MessageFeedback`，扩展 `KnowledgeIssue` 字段。
- 修改：`backend/app/models/__init__.py` — 导出新增模型。
- 创建：`backend/app/services/qa_ops_service.py` — 封装会话、消息、反馈、知识缺口查询和状态更新。
- 修改：`backend/app/main.py` — 接入 `QaOpsService`，增强 `/api/qa/ask/sync`，新增会话、消息、反馈和 issue 更新接口。
- 修改：`backend/app/static/admin.html` — 增加 `#issue-list` 最小知识缺口区域。
- 修改：`backend/app/static/admin.js` — 加载当前知识库 open issues，支持 resolved / ignored 状态更新。
- 创建：`backend/tests/test_qa_ops_api.py` — 覆盖问答记录、反馈、知识缺口和权限行为。
- 修改：`backend/tests/test_frontend_shell.py` — 验证管理台 issue 区域和 JS 钩子。
- 修改：`docs/api/api-reference.md` — 同步新增 / 增强 API。
- 修改：`docs/implementation/tech-code-mapping.md` — 同步阶段 2 技术映射。

---

### 任务 1：补齐问答运营数据库模型

**文件：**
- 修改：`backend/app/models/kg_ops.py`
- 修改：`backend/app/models/__init__.py`
- 测试：`backend/tests/test_models_schema.py`

- [ ] **步骤 1：编写失败的测试**

在 `backend/tests/test_models_schema.py` 新增：

```python
from app.models import Conversation, KnowledgeBase, Message, MessageFeedback, Role, User


def test_qa_ops_models_can_persist_conversation_message_feedback_and_issue(db_session):
    role = Role(name="管理员", level=3)
    user = User(username="admin", password_hash="hash", role=role)
    kb = KnowledgeBase(name="问答知识库", owner_id=1, visibility="department")
    db_session.add_all([role, user, kb])
    db_session.commit()

    conversation = Conversation(kb_id=kb.id, user_id=user.id, title="SOS 报警怎么关闭")
    db_session.add(conversation)
    db_session.commit()

    message = Message(
        conversation_id=conversation.id,
        kb_id=kb.id,
        user_id=user.id,
        question="SOS 报警怎么关闭",
        answer="在设置中关闭。",
        sources="[]",
    )
    db_session.add(message)
    db_session.commit()

    feedback = MessageFeedback(
        message_id=message.id,
        user_id=user.id,
        is_helpful=False,
        feedback_text="没有菜单路径",
    )
    db_session.add(feedback)
    db_session.commit()

    assert conversation.id is not None
    assert message.id is not None
    assert feedback.id is not None
```

如果当前 `test_models_schema.py` 没有 `db_session` fixture，则在测试文件内复用已有 `create_engine("sqlite://", poolclass=StaticPool)` 模式创建本地 session。

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
cd backend && pytest tests/test_models_schema.py::test_qa_ops_models_can_persist_conversation_message_feedback_and_issue -v
```

预期：FAIL，错误包含 `ImportError` 或 `cannot import name 'Conversation'`。

- [ ] **步骤 3：编写最少实现代码**

在 `backend/app/models/kg_ops.py` 中增加：

```python
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship
```

并新增模型：

```python
class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    kb_id: Mapped[int] = mapped_column(ForeignKey("knowledge_bases.id"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    title: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    kb: Mapped["KnowledgeBase"] = relationship()
    user: Mapped["User"] = relationship()


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id"))
    kb_id: Mapped[int] = mapped_column(ForeignKey("knowledge_bases.id"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text)
    sources: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    conversation: Mapped[Conversation] = relationship()
    kb: Mapped["KnowledgeBase"] = relationship()
    user: Mapped["User"] = relationship()


class MessageFeedback(Base):
    __tablename__ = "message_feedback"
    __table_args__ = (UniqueConstraint("message_id", "user_id", name="uq_message_feedback_message_id_user_id"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    message_id: Mapped[int] = mapped_column(ForeignKey("messages.id"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    is_helpful: Mapped[bool] = mapped_column(Boolean, default=True)
    feedback_text: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    message: Mapped[Message] = relationship()
    user: Mapped["User"] = relationship()
```

扩展 `KnowledgeIssue`：

```python
class KnowledgeIssue(Base):
    __tablename__ = "knowledge_issues"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    kb_id: Mapped[int | None] = mapped_column(ForeignKey("knowledge_bases.id"), nullable=True)
    message_id: Mapped[int | None] = mapped_column(ForeignKey("messages.id"), nullable=True)
    question: Mapped[str] = mapped_column(Text, default="")
    reason: Mapped[str] = mapped_column(String(50), default="")
    feedback_text: Mapped[str] = mapped_column(Text, default="")
    user_query: Mapped[str] = mapped_column(Text, default="")
    classification: Mapped[str] = mapped_column(String(30), default="")
    status: Mapped[str] = mapped_column(String(20), default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    kb: Mapped["KnowledgeBase"] = relationship()
    message: Mapped[Message] = relationship()
```

在 `backend/app/models/__init__.py` 中导出：

```python
from app.models.kg_ops import (
    AuditLog,
    ConflictLog,
    ContentBlock,
    Conversation,
    KnowledgeIssue,
    Message,
    MessageFeedback,
    ParseTask,
    ReviewQueue,
)
```

并在 `__all__` 加入：

```python
"Conversation",
"Message",
"MessageFeedback",
```

- [ ] **步骤 4：运行测试验证通过**

运行：

```bash
cd backend && pytest tests/test_models_schema.py::test_qa_ops_models_can_persist_conversation_message_feedback_and_issue -v
```

预期：PASS。

- [ ] **步骤 5：Commit**

```bash
git add backend/app/models/kg_ops.py backend/app/models/__init__.py backend/tests/test_models_schema.py
git commit -m "feat: add qa operations persistence models"
```

---

### 任务 2：实现 QaOpsService 的问答记录能力

**文件：**
- 创建：`backend/app/services/qa_ops_service.py`
- 测试：`backend/tests/test_qa_ops_api.py`

- [ ] **步骤 1：编写失败的测试**

创建 `backend/tests/test_qa_ops_api.py`，包含测试夹具和首个测试：

```python
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from app.core.db import Base
from app.main import create_app
from app.models import Role, User


def build_database_app():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    role = Role(name="管理员", level=3)
    owner = User(username="admin", password_hash="hash", role=role)
    session.add_all([role, owner])
    session.commit()
    app = create_app(mode="database", session=session)
    app.state.default_owner_id = owner.id
    return app


def login_default_admin(client: TestClient) -> str:
    response = client.post("/api/auth/login", json={"username": "admin", "password": "admin"})
    return response.json()["token"]


def create_kb(client: TestClient) -> dict:
    return client.post("/api/kb", json={"name": "问答运营知识库", "visibility": "department"}).json()


def test_ask_sync_persists_conversation_and_message_in_database_mode():
    client = TestClient(build_database_app())
    token = login_default_admin(client)
    kb = create_kb(client)

    response = client.post(
        "/api/qa/ask/sync",
        headers={"Authorization": f"Bearer {token}"},
        json={"kb_id": str(kb["id"]), "question": "SOS 报警怎么关闭"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["conversation_id"] is not None
    assert body["message_id"] is not None

    conversations = client.get(
        f"/api/qa/conversations?kb_id={kb['id']}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert conversations.status_code == 200
    assert conversations.json()["items"][0]["title"] == "SOS 报警怎么关闭"
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
cd backend && pytest tests/test_qa_ops_api.py::test_ask_sync_persists_conversation_and_message_in_database_mode -v
```

预期：FAIL，`/api/qa/ask/sync` 还没有认证头处理或响应没有 `conversation_id`。

- [ ] **步骤 3：编写最少实现代码**

创建 `backend/app/services/qa_ops_service.py`：

```python
from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models import Conversation, KnowledgeIssue, Message, MessageFeedback


class QaOpsService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def record_answer(
        self,
        kb_id: int,
        user_id: int,
        question: str,
        answer: str,
        sources: list[dict[str, Any]],
        conversation_id: int | None = None,
    ) -> tuple[Conversation, Message]:
        if conversation_id is None:
            conversation = Conversation(
                kb_id=kb_id,
                user_id=user_id,
                title=question[:200],
            )
            self.session.add(conversation)
            self.session.flush()
        else:
            conversation = (
                self.session.query(Conversation)
                .filter(Conversation.id == conversation_id, Conversation.kb_id == kb_id, Conversation.user_id == user_id)
                .one_or_none()
            )
            if conversation is None:
                raise ValueError("Conversation not found")
            conversation.updated_at = datetime.utcnow()

        message = Message(
            conversation_id=conversation.id,
            kb_id=kb_id,
            user_id=user_id,
            question=question,
            answer=answer,
            sources=json.dumps(sources, ensure_ascii=False),
        )
        conversation.updated_at = datetime.utcnow()
        self.session.add(message)
        self.session.commit()
        self.session.refresh(conversation)
        self.session.refresh(message)
        return conversation, message

    def list_conversations(self, kb_id: int, user_id: int) -> list[Conversation]:
        return (
            self.session.query(Conversation)
            .filter(Conversation.kb_id == kb_id, Conversation.user_id == user_id)
            .order_by(Conversation.updated_at.desc(), Conversation.id.desc())
            .all()
        )
```

修改 `backend/app/main.py`：

```python
from app.services.qa_ops_service import QaOpsService
```

在 `build_app_state_services()` database 分支返回：

```python
"qa_ops_service": QaOpsService(session),
```

在 memory 分支返回：

```python
"qa_ops_service": None,
```

在 `create_app()` 设置：

```python
app.state.qa_ops_service = services["qa_ops_service"]
```

增强 `ask_sync`：

```python
@app.post("/api/qa/ask/sync")
async def ask_sync(request: AskRequest, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    session_data = require_session(app.state.session_store, authorization)
    user_id = resolve_session_user_id(app, session_data)
    service_kb_id = int(request.kb_id) if app.state.service_mode == "database" else request.kb_id
    require_kb_permission(app, service_kb_id, user_id, "can_view")
    if app.state.service_mode == "database":
        chunks = DbChunkLoader(app.state.db_session).load_chunks(kb_id=int(request.kb_id))
        app.state.rag_service.tools.vector_chunks_by_kb[request.kb_id] = chunks
        app.state.rag_service.tools.build_bm25_index(request.kb_id, chunks)
    result = await app.state.rag_service.ask(
        question=request.question,
        kb_id=request.kb_id,
        conversation_id=request.conversation_id,
    )
    if app.state.service_mode == "database":
        conversation, message = app.state.qa_ops_service.record_answer(
            kb_id=int(request.kb_id),
            user_id=int(user_id),
            question=request.question,
            answer=result["answer"],
            sources=result.get("sources", []),
            conversation_id=int(request.conversation_id) if request.conversation_id else None,
        )
        return {**result, "conversation_id": conversation.id, "message_id": message.id}
    return result
```

新增会话列表路由：

```python
@app.get("/api/qa/conversations")
def list_qa_conversations(kb_id: str, authorization: str | None = Header(default=None)) -> dict[str, list[dict[str, Any]]]:
    session_data = require_session(app.state.session_store, authorization)
    user_id = resolve_session_user_id(app, session_data)
    service_kb_id = int(kb_id) if app.state.service_mode == "database" else kb_id
    require_kb_permission(app, service_kb_id, user_id, "can_view")
    if app.state.service_mode != "database":
        return {"items": []}
    items = app.state.qa_ops_service.list_conversations(int(kb_id), int(user_id))
    return {
        "items": [
            {
                "id": item.id,
                "kb_id": item.kb_id,
                "title": item.title,
                "created_at": item.created_at.isoformat(),
                "updated_at": item.updated_at.isoformat(),
            }
            for item in items
        ]
    }
```

- [ ] **步骤 4：运行测试验证通过**

运行：

```bash
cd backend && pytest tests/test_qa_ops_api.py::test_ask_sync_persists_conversation_and_message_in_database_mode -v
```

预期：PASS。

- [ ] **步骤 5：Commit**

```bash
git add backend/app/services/qa_ops_service.py backend/app/main.py backend/tests/test_qa_ops_api.py
git commit -m "feat: persist qa conversations and messages"
```

---

### 任务 3：实现会话追加与消息查询

**文件：**
- 修改：`backend/app/services/qa_ops_service.py`
- 修改：`backend/app/main.py`
- 修改：`backend/tests/test_qa_ops_api.py`

- [ ] **步骤 1：编写失败的测试**

在 `backend/tests/test_qa_ops_api.py` 新增：

```python
def test_ask_sync_appends_message_to_existing_conversation():
    client = TestClient(build_database_app())
    token = login_default_admin(client)
    kb = create_kb(client)

    first = client.post(
        "/api/qa/ask/sync",
        headers={"Authorization": f"Bearer {token}"},
        json={"kb_id": str(kb["id"]), "question": "第一个问题"},
    ).json()
    second = client.post(
        "/api/qa/ask/sync",
        headers={"Authorization": f"Bearer {token}"},
        json={"kb_id": str(kb["id"]), "question": "第二个问题", "conversation_id": str(first["conversation_id"])},
    ).json()

    assert second["conversation_id"] == first["conversation_id"]

    messages = client.get(
        f"/api/qa/conversations/{first['conversation_id']}/messages",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert messages.status_code == 200
    assert [item["question"] for item in messages.json()["items"]] == ["第一个问题", "第二个问题"]
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
cd backend && pytest tests/test_qa_ops_api.py::test_ask_sync_appends_message_to_existing_conversation -v
```

预期：FAIL，消息查询接口不存在。

- [ ] **步骤 3：编写最少实现代码**

在 `QaOpsService` 添加：

```python
    def get_conversation(self, conversation_id: int) -> Conversation | None:
        return self.session.get(Conversation, conversation_id)

    def list_messages(self, conversation_id: int, user_id: int) -> list[Message] | None:
        conversation = self.get_conversation(conversation_id)
        if conversation is None or conversation.user_id != user_id:
            return None
        return (
            self.session.query(Message)
            .filter(Message.conversation_id == conversation_id)
            .order_by(Message.id)
            .all()
        )
```

在 `main.py` 增加：

```python
import json
```

新增路由：

```python
@app.get("/api/qa/conversations/{conversation_id}/messages")
def list_qa_messages(conversation_id: str, authorization: str | None = Header(default=None)) -> dict[str, list[dict[str, Any]]]:
    session_data = require_session(app.state.session_store, authorization)
    user_id = resolve_session_user_id(app, session_data)
    if app.state.service_mode != "database":
        return {"items": []}
    conversation = app.state.qa_ops_service.get_conversation(int(conversation_id))
    if conversation is None or conversation.user_id != int(user_id):
        raise HTTPException(status_code=404, detail="Conversation not found")
    require_kb_permission(app, conversation.kb_id, int(user_id), "can_view")
    items = app.state.qa_ops_service.list_messages(int(conversation_id), int(user_id))
    if items is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {
        "items": [
            {
                "id": item.id,
                "question": item.question,
                "answer": item.answer,
                "sources": json.loads(item.sources or "[]"),
                "created_at": item.created_at.isoformat(),
            }
            for item in items
        ]
    }
```

- [ ] **步骤 4：运行测试验证通过**

运行：

```bash
cd backend && pytest tests/test_qa_ops_api.py::test_ask_sync_appends_message_to_existing_conversation -v
```

预期：PASS。

- [ ] **步骤 5：Commit**

```bash
git add backend/app/services/qa_ops_service.py backend/app/main.py backend/tests/test_qa_ops_api.py
git commit -m "feat: list qa conversation messages"
```

---

### 任务 4：实现答案反馈与负反馈生成知识缺口

**文件：**
- 修改：`backend/app/services/qa_ops_service.py`
- 修改：`backend/app/main.py`
- 修改：`backend/tests/test_qa_ops_api.py`

- [ ] **步骤 1：编写失败的测试**

在 `backend/tests/test_qa_ops_api.py` 新增：

```python
def test_positive_feedback_does_not_create_issue():
    client = TestClient(build_database_app())
    token = login_default_admin(client)
    kb = create_kb(client)
    answer = client.post(
        "/api/qa/ask/sync",
        headers={"Authorization": f"Bearer {token}"},
        json={"kb_id": str(kb["id"]), "question": "SOS 报警"},
    ).json()

    feedback = client.post(
        "/api/qa/feedback",
        headers={"Authorization": f"Bearer {token}"},
        json={"message_id": answer["message_id"], "is_helpful": True, "feedback_text": "有帮助"},
    )

    assert feedback.status_code == 200
    assert feedback.json() == {"saved": True, "issue_id": None}


def test_negative_feedback_creates_open_issue():
    client = TestClient(build_database_app())
    token = login_default_admin(client)
    kb = create_kb(client)
    answer = client.post(
        "/api/qa/ask/sync",
        headers={"Authorization": f"Bearer {token}"},
        json={"kb_id": str(kb["id"]), "question": "SOS 报警"},
    ).json()

    feedback = client.post(
        "/api/qa/feedback",
        headers={"Authorization": f"Bearer {token}"},
        json={"message_id": answer["message_id"], "is_helpful": False, "feedback_text": "没有菜单路径"},
    )

    assert feedback.status_code == 200
    assert feedback.json()["saved"] is True
    assert feedback.json()["issue_id"] is not None
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
cd backend && pytest tests/test_qa_ops_api.py::test_positive_feedback_does_not_create_issue tests/test_qa_ops_api.py::test_negative_feedback_creates_open_issue -v
```

预期：FAIL，`/api/qa/feedback` 不存在。

- [ ] **步骤 3：编写最少实现代码**

在 `main.py` 增加请求模型：

```python
class QaFeedbackRequest(BaseModel):
    message_id: int
    is_helpful: bool
    feedback_text: str = ""
```

在 `QaOpsService` 添加：

```python
    def get_message(self, message_id: int) -> Message | None:
        return self.session.get(Message, message_id)

    def save_feedback(self, message_id: int, user_id: int, is_helpful: bool, feedback_text: str) -> tuple[MessageFeedback, KnowledgeIssue | None]:
        message = self.get_message(message_id)
        if message is None or message.user_id != user_id:
            raise ValueError("Message not found")
        feedback = (
            self.session.query(MessageFeedback)
            .filter(MessageFeedback.message_id == message_id, MessageFeedback.user_id == user_id)
            .one_or_none()
        )
        if feedback is None:
            feedback = MessageFeedback(message_id=message_id, user_id=user_id)
            self.session.add(feedback)
        feedback.is_helpful = is_helpful
        feedback.feedback_text = feedback_text

        issue = None
        if not is_helpful:
            issue = (
                self.session.query(KnowledgeIssue)
                .filter(KnowledgeIssue.message_id == message_id, KnowledgeIssue.reason == "negative_feedback")
                .one_or_none()
            )
            if issue is None:
                issue = KnowledgeIssue(
                    kb_id=message.kb_id,
                    message_id=message.id,
                    question=message.question,
                    user_query=message.question,
                    reason="negative_feedback",
                    classification="negative_feedback",
                    status="open",
                )
                self.session.add(issue)
            issue.feedback_text = feedback_text
            issue.status = "open"
            issue.updated_at = datetime.utcnow()

        self.session.commit()
        self.session.refresh(feedback)
        if issue is not None:
            self.session.refresh(issue)
        return feedback, issue
```

在 `main.py` 新增路由：

```python
@app.post("/api/qa/feedback")
def save_qa_feedback(request: QaFeedbackRequest, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    session_data = require_session(app.state.session_store, authorization)
    user_id = resolve_session_user_id(app, session_data)
    if app.state.service_mode != "database":
        return {"saved": True, "issue_id": None}
    message = app.state.qa_ops_service.get_message(request.message_id)
    if message is None or message.user_id != int(user_id):
        raise HTTPException(status_code=404, detail="Message not found")
    require_kb_permission(app, message.kb_id, int(user_id), "can_view")
    _, issue = app.state.qa_ops_service.save_feedback(
        message_id=request.message_id,
        user_id=int(user_id),
        is_helpful=request.is_helpful,
        feedback_text=request.feedback_text,
    )
    return {"saved": True, "issue_id": issue.id if issue is not None else None}
```

- [ ] **步骤 4：运行测试验证通过**

运行：

```bash
cd backend && pytest tests/test_qa_ops_api.py::test_positive_feedback_does_not_create_issue tests/test_qa_ops_api.py::test_negative_feedback_creates_open_issue -v
```

预期：PASS。

- [ ] **步骤 5：Commit**

```bash
git add backend/app/services/qa_ops_service.py backend/app/main.py backend/tests/test_qa_ops_api.py
git commit -m "feat: save qa feedback and create knowledge issues"
```

---

### 任务 5：实现知识缺口查询与状态更新 API

**文件：**
- 修改：`backend/app/services/qa_ops_service.py`
- 修改：`backend/app/main.py`
- 修改：`backend/tests/test_qa_ops_api.py`

- [ ] **步骤 1：编写失败的测试**

在 `backend/tests/test_qa_ops_api.py` 新增：

```python
def create_negative_feedback_issue(client: TestClient, token: str, kb: dict) -> int:
    answer = client.post(
        "/api/qa/ask/sync",
        headers={"Authorization": f"Bearer {token}"},
        json={"kb_id": str(kb["id"]), "question": "SOS 报警"},
    ).json()
    feedback = client.post(
        "/api/qa/feedback",
        headers={"Authorization": f"Bearer {token}"},
        json={"message_id": answer["message_id"], "is_helpful": False, "feedback_text": "没有菜单路径"},
    ).json()
    return feedback["issue_id"]


def test_admin_can_list_open_issues_and_resolve_one():
    client = TestClient(build_database_app())
    token = login_default_admin(client)
    kb = create_kb(client)
    issue_id = create_negative_feedback_issue(client, token, kb)

    issues = client.get(
        f"/api/issues?kb_id={kb['id']}&status=open",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert issues.status_code == 200
    assert issues.json()["items"][0]["id"] == issue_id
    assert issues.json()["items"][0]["status"] == "open"

    updated = client.put(
        f"/api/issues/{issue_id}",
        headers={"Authorization": f"Bearer {token}"},
        json={"status": "resolved"},
    )

    assert updated.status_code == 200
    assert updated.json()["status"] == "resolved"
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
cd backend && pytest tests/test_qa_ops_api.py::test_admin_can_list_open_issues_and_resolve_one -v
```

预期：FAIL，`GET /api/issues` 仍返回内存空列表，`PUT /api/issues/{issue_id}` 不存在。

- [ ] **步骤 3：编写最少实现代码**

在 `main.py` 增加请求模型：

```python
class KnowledgeIssueUpdate(BaseModel):
    status: str
```

在 `QaOpsService` 添加：

```python
    def list_issues(self, kb_id: int, status: str | None = None) -> list[KnowledgeIssue]:
        query = self.session.query(KnowledgeIssue).filter(KnowledgeIssue.kb_id == kb_id)
        if status:
            query = query.filter(KnowledgeIssue.status == status)
        return query.order_by(KnowledgeIssue.id.desc()).all()

    def get_issue(self, issue_id: int) -> KnowledgeIssue | None:
        return self.session.get(KnowledgeIssue, issue_id)

    def update_issue_status(self, issue_id: int, status: str) -> KnowledgeIssue | None:
        if status not in {"open", "resolved", "ignored"}:
            raise ValueError("Invalid issue status")
        issue = self.get_issue(issue_id)
        if issue is None:
            return None
        issue.status = status
        issue.updated_at = datetime.utcnow()
        self.session.commit()
        self.session.refresh(issue)
        return issue
```

替换 `GET /api/issues`：

```python
@app.get("/api/issues")
def list_knowledge_issues(
    kb_id: str | None = None,
    status: str | None = None,
    authorization: str | None = Header(default=None),
) -> dict[str, list[dict[str, Any]]]:
    if app.state.service_mode != "database":
        return {"items": app.state.knowledge_issues}
    if kb_id is None:
        raise HTTPException(status_code=422, detail="kb_id is required")
    session_data = require_session(app.state.session_store, authorization)
    user_id = resolve_session_user_id(app, session_data)
    require_kb_permission(app, int(kb_id), int(user_id), "can_grant")
    items = app.state.qa_ops_service.list_issues(kb_id=int(kb_id), status=status)
    return {
        "items": [
            {
                "id": item.id,
                "kb_id": item.kb_id,
                "message_id": item.message_id,
                "question": item.question,
                "reason": item.reason,
                "feedback_text": item.feedback_text,
                "status": item.status,
                "created_at": item.created_at.isoformat(),
            }
            for item in items
        ]
    }
```

新增 `PUT /api/issues/{issue_id}`：

```python
@app.put("/api/issues/{issue_id}")
def update_knowledge_issue(issue_id: str, request: KnowledgeIssueUpdate, authorization: str | None = Header(default=None)) -> dict[str, Any]:
    if app.state.service_mode != "database":
        raise HTTPException(status_code=404, detail="Knowledge issue not found")
    session_data = require_session(app.state.session_store, authorization)
    user_id = resolve_session_user_id(app, session_data)
    issue = app.state.qa_ops_service.get_issue(int(issue_id))
    if issue is None:
        raise HTTPException(status_code=404, detail="Knowledge issue not found")
    require_kb_permission(app, int(issue.kb_id), int(user_id), "can_grant")
    try:
        updated = app.state.qa_ops_service.update_issue_status(int(issue_id), request.status)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid issue status")
    return {"id": updated.id, "status": updated.status}
```

- [ ] **步骤 4：运行测试验证通过**

运行：

```bash
cd backend && pytest tests/test_qa_ops_api.py::test_admin_can_list_open_issues_and_resolve_one -v
```

预期：PASS。

- [ ] **步骤 5：Commit**

```bash
git add backend/app/services/qa_ops_service.py backend/app/main.py backend/tests/test_qa_ops_api.py
git commit -m "feat: manage knowledge issues from qa feedback"
```

---

### 任务 6：补齐权限边界测试

**文件：**
- 修改：`backend/tests/test_qa_ops_api.py`
- 必要时修改：`backend/app/main.py`、`backend/app/services/qa_ops_service.py`

- [ ] **步骤 1：编写失败的测试**

在 `backend/tests/test_qa_ops_api.py` 新增：

```python
def test_qa_ask_requires_can_view():
    client = TestClient(build_database_app())
    token = login_default_admin(client)
    kb = create_kb(client)
    client.put(
        f"/api/kb/{kb['id']}/permissions/1",
        headers={"Authorization": f"Bearer {token}"},
        json={"can_view": False, "can_upload": False, "can_delete": False, "can_grant": False},
    )

    response = client.post(
        "/api/qa/ask/sync",
        headers={"Authorization": f"Bearer {token}"},
        json={"kb_id": str(kb["id"]), "question": "SOS"},
    )

    assert response.status_code == 403


def test_user_without_can_grant_cannot_list_issues():
    client = TestClient(build_database_app())
    token = login_default_admin(client)
    kb = create_kb(client)
    create_negative_feedback_issue(client, token, kb)
    client.put(
        f"/api/kb/{kb['id']}/permissions/1",
        headers={"Authorization": f"Bearer {token}"},
        json={"can_view": True, "can_upload": False, "can_delete": False, "can_grant": False},
    )

    response = client.get(
        f"/api/issues?kb_id={kb['id']}&status=open",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 403
```

- [ ] **步骤 2：运行测试验证失败或确认已有保护**

运行：

```bash
cd backend && pytest tests/test_qa_ops_api.py::test_qa_ask_requires_can_view tests/test_qa_ops_api.py::test_user_without_can_grant_cannot_list_issues -v
```

预期：如果任务 2/5 已正确实现权限，此处可能直接 PASS；若 FAIL，则按失败信息修复权限检查。

- [ ] **步骤 3：编写最少实现代码**

若失败，确保：

- `/api/qa/ask/sync` 调用 `require_kb_permission(..., "can_view")`。
- `GET /api/issues` 调用 `require_kb_permission(..., "can_grant")`。
- `PUT /api/issues/{issue_id}` 调用 `require_kb_permission(..., "can_grant")`。

- [ ] **步骤 4：运行测试验证通过**

运行：

```bash
cd backend && pytest tests/test_qa_ops_api.py -v
```

预期：`test_qa_ops_api.py` 全部 PASS。

- [ ] **步骤 5：Commit**

```bash
git add backend/tests/test_qa_ops_api.py backend/app/main.py backend/app/services/qa_ops_service.py
git commit -m "test: cover qa operations permission boundaries"
```

---

### 任务 7：接入管理员工作台知识缺口区域

**文件：**
- 修改：`backend/app/static/admin.html`
- 修改：`backend/app/static/admin.js`
- 修改：`backend/tests/test_frontend_shell.py`

- [ ] **步骤 1：编写失败的测试**

在 `backend/tests/test_frontend_shell.py` 新增：

```python
def test_admin_shell_contains_issue_list_region_and_issue_hooks():
    client = TestClient(create_app())

    response = client.get("/admin")
    admin_js = ADMIN_JS_PATH.read_text(encoding="utf-8")

    assert response.status_code == 200
    assert 'id="issue-list"' in response.text
    assert "let issues = []" in admin_js
    assert "loadIssues" in admin_js
    assert "updateIssueStatus" in admin_js
```

- [ ] **步骤 2：运行测试验证失败**

运行：

```bash
cd backend && pytest tests/test_frontend_shell.py::test_admin_shell_contains_issue_list_region_and_issue_hooks -v
```

预期：FAIL，页面和 JS 还没有 issue 区域。

- [ ] **步骤 3：编写最少实现代码**

在 `admin.html` 中 `</main>` 前加入：

```html
<section id="issue-workspace">
  <h2>知识缺口</h2>
  <section id="issue-list"></section>
</section>
```

在 `admin.js` 顶部增加：

```javascript
let issues = []
```

新增函数：

```javascript
function renderIssues(items) {
  const container = document.getElementById('issue-list')
  if (!container) {
    return
  }
  container.innerHTML = ''
  for (const item of items) {
    const row = document.createElement('div')
    const title = document.createElement('p')
    title.textContent = item.question
    row.appendChild(title)

    const feedback = document.createElement('p')
    feedback.textContent = item.feedback_text || item.reason
    row.appendChild(feedback)

    const resolveButton = document.createElement('button')
    resolveButton.type = 'button'
    resolveButton.textContent = '标记已处理'
    resolveButton.addEventListener('click', () => {
      void updateIssueStatus(item.id, 'resolved')
    })
    row.appendChild(resolveButton)

    const ignoreButton = document.createElement('button')
    ignoreButton.type = 'button'
    ignoreButton.textContent = '忽略'
    ignoreButton.addEventListener('click', () => {
      void updateIssueStatus(item.id, 'ignored')
    })
    row.appendChild(ignoreButton)
    container.appendChild(row)
  }
}

async function loadIssues() {
  if (!activeKbId) {
    issues = []
    renderIssues(issues)
    return
  }
  const result = await authorizedJson(`/api/issues?kb_id=${activeKbId}&status=open`)
  issues = result?.items || []
  renderIssues(issues)
}

async function updateIssueStatus(issueId, status) {
  const result = await authorizedJson(`/api/issues/${issueId}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ status }),
  })
  if (!result?.id) {
    setAdminMessage('更新知识缺口失败。')
    return
  }
  setAdminMessage('知识缺口状态已更新。')
  await loadIssues()
}
```

在 `refreshKnowledgeBases()` 空状态中增加：

```javascript
issues = []
renderIssues(issues)
```

在 `refreshKnowledgeBaseView()` 中加载权限后增加：

```javascript
await loadIssues()
```

在无 `activeKbId` 分支中增加：

```javascript
issues = []
renderIssues(issues)
```

在 `loadAdminShell()` 初始化中增加：

```javascript
issues = []
renderIssues(issues)
```

- [ ] **步骤 4：运行测试验证通过**

运行：

```bash
cd backend && pytest tests/test_frontend_shell.py::test_admin_shell_contains_issue_list_region_and_issue_hooks -v
```

预期：PASS。

- [ ] **步骤 5：Commit**

```bash
git add backend/app/static/admin.html backend/app/static/admin.js backend/tests/test_frontend_shell.py
git commit -m "feat: show knowledge issues in admin workspace"
```

---

### 任务 8：同步 API 文档和技术映射

**文件：**
- 修改：`docs/api/api-reference.md`
- 修改：`docs/implementation/tech-code-mapping.md`

- [ ] **步骤 1：编写失败的测试**

如果项目没有文档同步测试，则本任务以人工检查为准；不新增测试文件。执行前先搜索文档是否已包含新接口：

```bash
grep -R "POST /api/qa/feedback" docs/api/api-reference.md || true
```

预期：没有输出。

- [ ] **步骤 2：更新 API 文档**

在 `docs/api/api-reference.md` 的问答 API 章节补充：

```markdown
### GET `/api/qa/conversations?kb_id={kb_id}`

返回当前用户在指定知识库下的会话列表。要求 `Authorization: Bearer <token>`，且用户拥有该知识库 `can_view`。

### GET `/api/qa/conversations/{conversation_id}/messages`

返回当前用户自己的会话消息。要求用户仍拥有该知识库 `can_view`。

### POST `/api/qa/feedback`

保存答案反馈。正反馈只保存反馈；负反馈会创建或更新 `knowledge_issues`，状态为 `open`。
```

在 issue API 章节补充：

```markdown
### GET `/api/issues?kb_id={kb_id}&status=open`

数据库模式下返回指定知识库的知识缺口列表，要求当前用户拥有 `can_grant`。

### PUT `/api/issues/{issue_id}`

更新知识缺口状态，允许 `open` / `resolved` / `ignored`，要求当前用户拥有 issue 所属知识库的 `can_grant`。
```

- [ ] **步骤 3：更新技术映射文档**

在 `docs/implementation/tech-code-mapping.md` 增加阶段 2 映射：

```markdown
| 问答运营闭环 | `backend/app/services/qa_ops_service.py`, `backend/app/models/kg_ops.py`, `backend/app/main.py` | ✅ 阶段 2 计划实现 | 后续接用户聊天页、运营看板、自动补知识入口 |
```

并在测试对应关系中加入：

```markdown
| `test_qa_ops_api.py` | 问答记录、会话消息、答案反馈、知识缺口和权限边界 |
```

- [ ] **步骤 4：文档检查**

运行：

```bash
grep -R "POST /api/qa/feedback" docs/api/api-reference.md && grep -R "qa_ops_service" docs/implementation/tech-code-mapping.md
```

预期：两条命令都有匹配输出。

- [ ] **步骤 5：Commit**

```bash
git add docs/api/api-reference.md docs/implementation/tech-code-mapping.md
git commit -m "docs: document qa operations loop APIs"
```

---

### 任务 9：运行阶段回归验证并修复发现的问题

**文件：**
- 测试：`backend/tests/test_qa_ops_api.py`
- 测试：`backend/tests/test_frontend_shell.py`
- 测试：全量 `backend/tests`

- [ ] **步骤 1：运行阶段定向测试**

运行：

```bash
cd backend && pytest tests/test_qa_ops_api.py tests/test_frontend_shell.py -v
```

预期：全部 PASS。

- [ ] **步骤 2：运行完整回归**

运行：

```bash
cd backend && pytest -q
```

预期：全部 PASS。

- [ ] **步骤 3：如果失败，按系统化调试修复**

若出现失败：

1. 读取完整错误。
2. 稳定复现单个失败测试。
3. 定位根因。
4. 先补或调整失败测试。
5. 最小修复。
6. 重跑定向测试和完整回归。

- [ ] **步骤 4：记录验证证据**

在最终回复中记录实际命令和输出摘要，例如：

```text
cd backend && pytest tests/test_qa_ops_api.py tests/test_frontend_shell.py -v → xx passed
cd backend && pytest -q → xx passed
```

- [ ] **步骤 5：Commit**

如有修复：

```bash
git add backend docs
git commit -m "fix: stabilize qa operations loop regression"
```

如没有修复，仅报告验证结果，不创建空提交。
