from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.db import Base
from app.main import create_app
from app.models import Role, User
from app.services.password_service import hash_password


def build_database_context():
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
    viewer = User(username="viewer", password_hash=hash_password("viewerpass"), role=role)
    session.add_all([role, owner, viewer])
    session.commit()
    app = create_app(mode="database", session=session)
    app.state.default_owner_id = owner.id
    client = TestClient(app)
    admin_token = client.post("/api/auth/login", json={"username": "admin", "password": "admin"}).json()["token"]
    viewer_token = app.state.session_store.create(user_id=str(viewer.id), username=viewer.username)
    kb = client.post("/api/kb", json={"name": "视图规则知识库", "visibility": "department"}).json()
    return app, client, owner, viewer, kb, admin_token, viewer_token


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def grant_view(client: TestClient, kb_id: int, user_id: int, token: str):
    return client.put(
        f"/api/kb/{kb_id}/permissions/{user_id}",
        headers=auth(token),
        json={"can_view": True, "can_upload": False, "can_delete": False, "can_grant": False},
    )


def test_can_grant_user_can_set_get_and_delete_view_rule():
    _, client, _, viewer, kb, admin_token, _ = build_database_context()
    assert grant_view(client, kb["id"], viewer.id, admin_token).status_code == 200

    saved = client.put(
        f"/api/kb/{kb['id']}/view-rules/{viewer.id}",
        headers=auth(admin_token),
        json={
            "allowed_departments": ["售后"],
            "allowed_product_lines": ["P368"],
            "allowed_visibilities": ["public", "internal"],
            "max_security_level": 2,
        },
    )
    assert saved.status_code == 200
    assert saved.json()["effective_scope"] == "restricted"

    fetched = client.get(
        f"/api/kb/{kb['id']}/view-rules/{viewer.id}",
        headers=auth(admin_token),
    )
    assert fetched.status_code == 200
    assert fetched.json()["allowed_departments"] == ["售后"]

    deleted = client.delete(
        f"/api/kb/{kb['id']}/view-rules/{viewer.id}",
        headers=auth(admin_token),
    )
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True

    fetched_after = client.get(
        f"/api/kb/{kb['id']}/view-rules/{viewer.id}",
        headers=auth(admin_token),
    )
    assert fetched_after.json()["effective_scope"] == "all_documents"


def test_user_without_can_grant_cannot_manage_view_rules():
    _, client, _, viewer, kb, admin_token, viewer_token = build_database_context()
    grant_view(client, kb["id"], viewer.id, admin_token)

    response = client.get(
        f"/api/kb/{kb['id']}/view-rules/{viewer.id}",
        headers=auth(viewer_token),
    )

    assert response.status_code == 403


def test_target_user_requires_can_view_before_rule_can_be_set():
    _, client, _, viewer, kb, admin_token, _ = build_database_context()

    response = client.put(
        f"/api/kb/{kb['id']}/view-rules/{viewer.id}",
        headers=auth(admin_token),
        json={
            "allowed_departments": ["售后"],
            "allowed_product_lines": [],
            "allowed_visibilities": [],
            "max_security_level": None,
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "Target user requires can_view permission"


def test_get_missing_rule_returns_all_documents_scope():
    _, client, _, viewer, kb, admin_token, _ = build_database_context()
    grant_view(client, kb["id"], viewer.id, admin_token)

    response = client.get(
        f"/api/kb/{kb['id']}/view-rules/{viewer.id}",
        headers=auth(admin_token),
    )

    assert response.status_code == 200
    assert response.json() == {
        "kb_id": kb["id"],
        "user_id": viewer.id,
        "rule": None,
        "effective_scope": "all_documents",
    }
