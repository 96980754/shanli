from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.db import Base
from app.main import create_app
from app.models import AuditLog, Role, User


def build_database_app():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    role = Role(name="管理员", level=3)
    owner = User(username="admin", password_hash="hash", role=role)
    session.add_all([role, owner])
    session.commit()
    app = create_app(mode="database", session=session)
    app.state.default_owner_id = owner.id
    return app


def login_default_admin(client: TestClient) -> str:
    return client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "admin"},
    ).json()["token"]


def test_database_document_download_returns_attachment_and_audit_log(tmp_path: Path):
    app = build_database_app()
    app.state.file_storage_root = tmp_path
    client = TestClient(app)
    token = login_default_admin(client)
    kb = client.post("/api/kb", json={"name": "下载知识库"}).json()
    uploaded = client.post(
        f"/api/kb/{kb['id']}/documents/upload",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("manual.txt", b"SOS alarm", "text/plain")},
    ).json()

    response = client.get(
        f"/api/kb/{kb['id']}/documents/{uploaded['id']}/download",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.content == b"SOS alarm"
    assert response.headers["content-type"].startswith("text/plain")
    assert "attachment" in response.headers["content-disposition"]
    audit = app.state.db_session.query(AuditLog).one()
    assert audit.action == "download_document"
    assert audit.target_id == str(uploaded["id"])
    assert audit.kb_id == kb["id"]
