from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from app.core.db import Base
from app.main import create_app
from app.models import Role, User
from app.services.password_service import hash_password


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
    owner = User(username="admin", password_hash=hash_password("admin"), role=role)
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


def ask(client: TestClient, token: str, kb: dict, question: str, conversation_id: int | None = None) -> dict:
    payload = {"kb_id": str(kb["id"]), "question": question}
    if conversation_id is not None:
        payload["conversation_id"] = str(conversation_id)
    response = client.post(
        "/api/qa/ask/sync",
        headers={"Authorization": f"Bearer {token}"},
        json=payload,
    )
    assert response.status_code == 200
    return response.json()


def create_negative_feedback_issue(client: TestClient, token: str, kb: dict) -> int:
    answer = ask(client, token, kb, "SOS 报警")
    feedback = client.post(
        "/api/qa/feedback",
        headers={"Authorization": f"Bearer {token}"},
        json={"message_id": answer["message_id"], "is_helpful": False, "feedback_text": "没有菜单路径"},
    ).json()
    return feedback["issue_id"]


def test_ask_sync_persists_conversation_and_message_in_database_mode():
    client = TestClient(build_database_app())
    token = login_default_admin(client)
    kb = create_kb(client)

    body = ask(client, token, kb, "SOS 报警怎么关闭")

    assert body["conversation_id"] is not None
    assert body["message_id"] is not None

    conversations = client.get(
        f"/api/qa/conversations?kb_id={kb['id']}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert conversations.status_code == 200
    assert conversations.json()["items"][0]["title"] == "SOS 报警怎么关闭"


def test_ask_sync_appends_message_to_existing_conversation():
    client = TestClient(build_database_app())
    token = login_default_admin(client)
    kb = create_kb(client)

    first = ask(client, token, kb, "第一个问题")
    second = ask(client, token, kb, "第二个问题", conversation_id=first["conversation_id"])

    assert second["conversation_id"] == first["conversation_id"]

    messages = client.get(
        f"/api/qa/conversations/{first['conversation_id']}/messages",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert messages.status_code == 200
    assert [item["question"] for item in messages.json()["items"]] == ["第一个问题", "第二个问题"]


def test_positive_feedback_does_not_create_issue():
    client = TestClient(build_database_app())
    token = login_default_admin(client)
    kb = create_kb(client)
    answer = ask(client, token, kb, "SOS 报警")

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
    answer = ask(client, token, kb, "SOS 报警")

    feedback = client.post(
        "/api/qa/feedback",
        headers={"Authorization": f"Bearer {token}"},
        json={"message_id": answer["message_id"], "is_helpful": False, "feedback_text": "没有菜单路径"},
    )

    assert feedback.status_code == 200
    assert feedback.json()["saved"] is True
    assert feedback.json()["issue_id"] is not None


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


def test_admin_can_ignore_issue():
    client = TestClient(build_database_app())
    token = login_default_admin(client)
    kb = create_kb(client)
    issue_id = create_negative_feedback_issue(client, token, kb)

    updated = client.put(
        f"/api/issues/{issue_id}",
        headers={"Authorization": f"Bearer {token}"},
        json={"status": "ignored"},
    )

    assert updated.status_code == 200
    assert updated.json()["status"] == "ignored"


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


def test_feedback_requires_message_owner():
    app = build_database_app()
    client = TestClient(app)
    token = login_default_admin(client)
    kb = create_kb(client)
    answer = ask(client, token, kb, "SOS 报警")

    app.state.session_store._sessions["other-token"] = {"user_id": "2", "username": "other"}
    response = client.post(
        "/api/qa/feedback",
        headers={"Authorization": "Bearer other-token"},
        json={"message_id": answer["message_id"], "is_helpful": False, "feedback_text": "不是我的消息"},
    )

    assert response.status_code == 404
