from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

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
    response = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "admin"},
    )
    return response.json()["token"]


def test_document_detail_returns_parse_and_chunk_counts(tmp_path):
    app = build_database_app()
    app.state.upload_root = tmp_path
    client = TestClient(app)
    token = login_default_admin(client)
    kb = client.post("/api/kb", json={"name": "文档详情库", "visibility": "department"}).json()
    uploaded = client.post(
        f"/api/kb/{kb['id']}/documents/upload",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("manual.txt", b"SOS alarm", "text/plain")},
    ).json()

    response = client.get(
        f"/api/kb/{kb['id']}/documents/{uploaded['id']}",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == uploaded["id"]
    assert body["kb_id"] == kb["id"]
    assert body["title"] == "manual.txt"
    assert body["status"] == "pending"
    assert body["file_type"] == "txt"
    assert body["block_count"] == 1
    assert body["chunk_count"] == 1


def test_document_detail_returns_metadata_fields(tmp_path):
    app = build_database_app()
    app.state.upload_root = tmp_path
    client = TestClient(app)
    token = login_default_admin(client)
    kb = client.post("/api/kb", json={"name": "产品知识库", "visibility": "department"}).json()

    upload = client.post(
        f"/api/kb/{kb['id']}/documents/upload",
        headers={"Authorization": f"Bearer {token}"},
        data={
            "department": "售后",
            "product_line": "P368",
            "visibility": "internal",
            "security_level": "2",
            "tags": "FAQ,报警",
        },
        files={"file": ("manual.txt", b"SOS alarm", "text/plain")},
    ).json()

    response = client.get(
        f"/api/kb/{kb['id']}/documents/{upload['id']}",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    detail = response.json()
    assert detail["department"] == "售后"
    assert detail["product_line"] == "P368"
    assert detail["visibility"] == "internal"
    assert detail["security_level"] == 2
    assert detail["tags"] == "FAQ,报警"


def test_document_detail_returns_404_for_missing_document():
    client = TestClient(create_app())
    token = login_default_admin(client)
    kb = client.post("/api/kb", json={"name": "内存文档库"}).json()

    response = client.get(
        f"/api/kb/{kb['id']}/documents/missing-doc",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Document not found"


def test_document_detail_returns_404_when_document_belongs_to_other_kb():
    client = TestClient(create_app())
    token = login_default_admin(client)
    first_kb = client.post("/api/kb", json={"name": "第一个库"}).json()
    second_kb = client.post("/api/kb", json={"name": "第二个库"}).json()
    uploaded = client.post(
        f"/api/kb/{first_kb['id']}/documents/upload",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("manual.txt", b"content", "text/plain")},
    ).json()

    response = client.get(
        f"/api/kb/{second_kb['id']}/documents/{uploaded['id']}",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Document not found"


def test_user_without_can_view_gets_403_on_document_detail():
    admin_app = create_app()
    admin_client = TestClient(admin_app)
    admin_token = login_default_admin(admin_client)
    kb = admin_client.post("/api/kb", json={"name": "受控文档库"}).json()
    uploaded = admin_client.post(
        f"/api/kb/{kb['id']}/documents/upload",
        headers={"Authorization": f"Bearer {admin_token}"},
        files={"file": ("manual.txt", b"content", "text/plain")},
    ).json()
    admin_client.put(
        f"/api/kb/{kb['id']}/permissions/3001",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "can_view": False,
            "can_upload": False,
            "can_delete": False,
            "can_grant": False,
        },
    )
    viewer_token = admin_app.state.session_store.create(user_id="3001", username="viewer")

    response = admin_client.get(
        f"/api/kb/{kb['id']}/documents/{uploaded['id']}",
        headers={"Authorization": f"Bearer {viewer_token}"},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "Permission denied"
