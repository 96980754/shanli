from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.db import Base
from app.main import create_app
from app.models import KnowledgeBase, KnowledgeBasePermission, Role, User
from app.services.db_kb_service import DbKnowledgeBaseService


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


def test_create_knowledge_base_grants_creator_all_permissions_in_database_mode():
    app = build_database_app()
    client = TestClient(app)

    created = client.post(
        "/api/kb",
        json={"name": "管理员知识库", "description": "desc", "visibility": "department"},
    ).json()

    permissions = (
        app.state.db_session.query(KnowledgeBasePermission)
        .filter(KnowledgeBasePermission.kb_id == created["id"])
        .all()
    )

    assert len(permissions) == 1
    permission = permissions[0]
    assert permission.can_view is True
    assert permission.can_upload is True
    assert permission.can_delete is True
    assert permission.can_grant is True


def test_create_knowledge_base_rolls_back_when_owner_permission_write_fails():
    app = build_database_app()
    session = app.state.db_session
    service = DbKnowledgeBaseService(session)
    original_add = session.add

    def failing_add(instance, _warn=True):
        if isinstance(instance, KnowledgeBasePermission):
            raise SQLAlchemyError("permission insert failed")
        return original_add(instance, _warn=_warn)

    session.add = failing_add

    try:
        service.create(name="会回滚的知识库", owner_id=app.state.default_owner_id)
    except SQLAlchemyError:
        pass
    else:
        raise AssertionError("expected permission insert to fail")
    finally:
        session.add = original_add

    assert session.query(KnowledgeBase).count() == 0
    assert session.query(KnowledgeBasePermission).count() == 0


def login_default_admin(client: TestClient) -> str:
    response = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "admin"},
    )
    return response.json()["token"]


def test_grant_user_permissions_then_list_them():
    client = TestClient(create_app())
    token = login_default_admin(client)
    kb = client.post("/api/kb", json={"name": "授权库"}).json()

    grant_response = client.put(
        f"/api/kb/{kb['id']}/permissions/1001",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "can_view": True,
            "can_upload": True,
            "can_delete": False,
            "can_grant": False,
        },
    )

    list_response = client.get(
        f"/api/kb/{kb['id']}/permissions",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert grant_response.status_code == 200
    assert grant_response.json()["user_id"] == "1001"
    assert list_response.status_code == 200
    granted = [item for item in list_response.json()["items"] if item["user_id"] == "1001"]
    assert granted[0]["can_view"] is True
    assert granted[0]["can_upload"] is True
    assert granted[0]["can_delete"] is False
    assert granted[0]["can_grant"] is False


def test_remove_user_permissions_deletes_permission_record():
    client = TestClient(create_app())
    token = login_default_admin(client)
    kb = client.post("/api/kb", json={"name": "移除授权库"}).json()
    client.put(
        f"/api/kb/{kb['id']}/permissions/1002",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "can_view": True,
            "can_upload": False,
            "can_delete": False,
            "can_grant": False,
        },
    )

    delete_response = client.delete(
        f"/api/kb/{kb['id']}/permissions/1002",
        headers={"Authorization": f"Bearer {token}"},
    )

    list_response = client.get(
        f"/api/kb/{kb['id']}/permissions",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert delete_response.status_code == 200
    assert delete_response.json() == {"deleted": True}
    assert [item for item in list_response.json()["items"] if item["user_id"] == "1002"] == []


def test_permission_interface_requires_can_grant():
    app = create_app()
    client = TestClient(app)
    admin_token = login_default_admin(client)
    kb = client.post("/api/kb", json={"name": "授权受控库"}).json()
    client.put(
        f"/api/kb/{kb['id']}/permissions/5001",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "can_view": True,
            "can_upload": False,
            "can_delete": False,
            "can_grant": False,
        },
    )
    limited_token = app.state.session_store.create(user_id="5001", username="viewer")

    response = client.get(
        f"/api/kb/{kb['id']}/permissions",
        headers={"Authorization": f"Bearer {limited_token}"},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "Permission denied"
