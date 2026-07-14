import bcrypt

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.db import Base
from app.main import create_app
from app.models import KnowledgeBasePermission, Role, User


def build_database_client() -> tuple[TestClient, object]:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    admin_role = Role(name="管理员", level=3)
    admin = User(username="admin", password_hash=bcrypt.hashpw(b"adminpass1", bcrypt.gensalt()).decode(), role=admin_role)
    session.add_all([admin_role, admin])
    session.commit()
    app = create_app(mode="database", session=session)
    app.state.default_owner_id = admin.id
    return TestClient(app), session


def login_database_admin(client: TestClient) -> str:
    response = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "adminpass1"},
    )
    assert response.status_code == 200
    return response.json()["token"]


def test_register_creates_hashed_database_user_without_permissions():
    client, session = build_database_client()

    response = client.post(
        "/api/auth/register",
        json={
            "username": "alice",
            "password": "eightpass",
            "password_confirmation": "eightpass",
        },
    )

    assert response.status_code == 201
    assert response.json()["username"] == "alice"
    assert "password" not in response.json()
    user = session.query(User).filter_by(username="alice").one()
    assert user.password_hash != "eightpass"
    assert user.password_hash.startswith("$2b$")
    assert session.query(KnowledgeBasePermission).filter_by(user_id=user.id).count() == 0


def test_register_rejects_invalid_payload_and_duplicate_username():
    client, _ = build_database_client()

    too_short = client.post(
        "/api/auth/register",
        json={"username": "ab", "password": "eightpass", "password_confirmation": "eightpass"},
    )
    mismatch = client.post(
        "/api/auth/register",
        json={"username": "alice", "password": "eightpass", "password_confirmation": "different1"},
    )
    duplicate = client.post(
        "/api/auth/register",
        json={"username": "admin", "password": "eightpass", "password_confirmation": "eightpass"},
    )

    assert too_short.status_code == 422
    assert mismatch.status_code == 422
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"] == "Username already exists"


def test_registered_user_can_log_in_with_hashed_password_and_me_returns_numeric_id():
    client, _ = build_database_client()
    register = client.post(
        "/api/auth/register",
        json={"username": "alice", "password": "correctpass", "password_confirmation": "correctpass"},
    )

    login = client.post(
        "/api/auth/login",
        json={"username": "alice", "password": "correctpass"},
    )
    profile = client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {login.json()['token']}"},
    )

    assert register.status_code == 201
    assert login.status_code == 200
    assert profile.status_code == 200
    assert profile.json()["username"] == "alice"
    assert profile.json()["user_id"].isdigit()


def test_database_login_rejects_wrong_password_and_default_owner_id_does_not_change_identity():
    client, session = build_database_client()
    client.post(
        "/api/auth/register",
        json={"username": "public_viewer", "password": "correctpass", "password_confirmation": "correctpass"},
    )
    viewer = session.query(User).filter_by(username="public_viewer").one()
    client.app.state.default_owner_id = viewer.id

    admin_login = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "adminpass1"},
    )
    wrong_password = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "wrongpass1"},
    )
    unknown_user = client.post(
        "/api/auth/login",
        json={"username": "nobody", "password": "correctpass"},
    )
    profile = client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {admin_login.json()['token']}"},
    )

    assert admin_login.status_code == 200
    assert profile.json()["username"] == "admin"
    assert wrong_password.status_code == 401
    assert unknown_user.status_code == 401


def test_users_endpoint_requires_can_grant_and_omits_password_fields():
    client, session = build_database_client()
    client.post(
        "/api/auth/register",
        json={"username": "viewer", "password": "correctpass", "password_confirmation": "correctpass"},
    )
    viewer = session.query(User).filter_by(username="viewer").one()
    admin = session.query(User).filter_by(username="admin").one()
    permission = KnowledgeBasePermission(
        kb_id=1,
        user_id=admin.id,
        can_view=True,
        can_upload=True,
        can_delete=True,
        can_grant=True,
    )
    session.add(permission)
    session.commit()

    unauthorized = client.get("/api/users")
    viewer_login = client.post(
        "/api/auth/login",
        json={"username": "viewer", "password": "correctpass"},
    )
    forbidden = client.get(
        "/api/users",
        headers={"Authorization": f"Bearer {viewer_login.json()['token']}"},
    )
    admin_token = login_database_admin(client)
    allowed = client.get(
        "/api/users",
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    assert unauthorized.status_code == 401
    assert forbidden.status_code == 403
    assert allowed.status_code == 200
    assert [item["username"] for item in allowed.json()["items"]] == ["admin", "viewer"]
    assert all("password" not in item and "password_hash" not in item for item in allowed.json()["items"])


def test_registration_requires_database_mode():
    client = TestClient(create_app())

    response = client.post(
        "/api/auth/register",
        json={"username": "alice", "password": "correctpass", "password_confirmation": "correctpass"},
    )

    assert response.status_code == 501
    assert response.json()["detail"] == "Registration requires database mode"


def test_users_endpoint_requires_database_mode():
    client = TestClient(create_app())
    token = client.post("/api/auth/login", json={"username": "admin", "password": "admin"}).json()["token"]

    response = client.get("/api/users", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 501
    assert response.json()["detail"] == "User listing requires database mode"
