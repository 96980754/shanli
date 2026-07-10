from zipfile import ZipFile

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
    response = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "admin"},
    )
    return response.json()["token"]


def build_docx_bytes(*paragraphs: str) -> bytes:
    from io import BytesIO

    document_xml = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    {paragraphs}
  </w:body>
</w:document>
""".format(
        paragraphs="\n".join(f"<w:p><w:r><w:t>{paragraph}</w:t></w:r></w:p>" for paragraph in paragraphs)
    )
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", document_xml)
    return buffer.getvalue()


def test_database_mode_kb_api_persists_knowledge_base():
    client = TestClient(build_database_app())
    token = login_default_admin(client)

    response = client.post(
        "/api/kb",
        json={"name": "数据库知识库", "description": "desc", "visibility": "department"},
    )

    assert response.status_code == 200
    created = response.json()
    assert created["name"] == "数据库知识库"

    list_response = client.get("/api/kb", headers={"Authorization": f"Bearer {token}"})
    assert list_response.status_code == 200
    assert list_response.json()["items"][0]["name"] == "数据库知识库"


def test_database_mode_document_upload_updates_persisted_doc_count():
    client = TestClient(build_database_app())
    token = login_default_admin(client)
    kb = client.post("/api/kb", json={"name": "数据库知识库", "visibility": "department"}).json()

    upload_response = client.post(
        f"/api/kb/{kb['id']}/documents/upload",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("manual.pdf", b"PDF", "application/pdf")},
    )

    assert upload_response.status_code == 200
    body = upload_response.json()
    assert body["title"] == "manual.pdf"

    kb_after = client.get(f"/api/kb/{kb['id']}", headers={"Authorization": f"Bearer {token}"}).json()
    assert kb_after["doc_count"] == 1


def test_database_mode_upload_stages_file_and_returns_parse_task(tmp_path):
    app = build_database_app()
    app.state.upload_root = tmp_path
    client = TestClient(app)
    token = login_default_admin(client)
    kb = client.post("/api/kb", json={"name": "入库知识库", "visibility": "department"}).json()

    upload_response = client.post(
        f"/api/kb/{kb['id']}/documents/upload",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("manual.txt", b"SOS alarm", "text/plain")},
    )

    assert upload_response.status_code == 200
    body = upload_response.json()
    assert body["parse_task_id"] is not None
    assert body["block_count"] == 1
    assert body["chunk_count"] == 1
    staged_file = tmp_path / body["staged_filename"]
    assert staged_file.exists()
    assert staged_file.read_bytes() == b"SOS alarm"


def test_database_mode_upload_docx_creates_blocks_and_chunks(tmp_path):
    app = build_database_app()
    app.state.upload_root = tmp_path
    client = TestClient(app)
    token = login_default_admin(client)
    kb = client.post("/api/kb", json={"name": "Word知识库", "visibility": "department"}).json()

    upload_response = client.post(
        f"/api/kb/{kb['id']}/documents/upload",
        headers={"Authorization": f"Bearer {token}"},
        files={
            "file": (
                "mcstars.docx",
                build_docx_bytes("MCSTARS 产品白皮书", "MCSTARS 支持统一调度。"),
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            )
        },
    )

    assert upload_response.status_code == 200
    body = upload_response.json()
    assert body["block_count"] == 2
    assert body["chunk_count"] == 2

    response = client.post(
        "/api/qa/ask/sync",
        headers={"Authorization": f"Bearer {token}"},
        json={"kb_id": str(kb["id"]), "question": "MCSTARS 调度"},
    )
    assert response.json()["sources"]


def test_database_mode_upload_persists_metadata_fields(tmp_path):
    app = build_database_app()
    app.state.upload_root = tmp_path
    client = TestClient(app)
    token = login_default_admin(client)
    kb = client.post("/api/kb", json={"name": "元数据知识库", "visibility": "department"}).json()

    response = client.post(
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
    )

    assert response.status_code == 200
    body = response.json()
    assert body["department"] == "售后"
    assert body["product_line"] == "P368"
    assert body["visibility"] == "internal"
    assert body["security_level"] == 2
    assert body["tags"] == "FAQ,报警"


def test_database_mode_qa_uses_uploaded_document_chunks(tmp_path):
    app = build_database_app()
    app.state.upload_root = tmp_path
    client = TestClient(app)
    token = login_default_admin(client)
    kb = client.post("/api/kb", json={"name": "问答知识库", "visibility": "department"}).json()
    client.post(
        f"/api/kb/{kb['id']}/documents/upload",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("manual.txt", b"SOS alarm can be disabled in settings.", "text/plain")},
    )

    response = client.post(
        "/api/qa/ask/sync",
        headers={"Authorization": f"Bearer {token}"},
        json={"kb_id": str(kb["id"]), "question": "SOS alarm"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["sources"]
    assert body["sources"][0]["content"] == "SOS alarm can be disabled in settings."


def test_database_mode_qa_builds_bm25_index_from_uploaded_chunks(tmp_path):
    app = build_database_app()
    app.state.upload_root = tmp_path
    client = TestClient(app)
    token = login_default_admin(client)
    kb = client.post("/api/kb", json={"name": "BM25知识库", "visibility": "department"}).json()
    client.post(
        f"/api/kb/{kb['id']}/documents/upload",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("manual.txt", b"P368 unique model setup guide.", "text/plain")},
    )

    response = client.post(
        "/api/qa/ask/sync",
        headers={"Authorization": f"Bearer {token}"},
        json={"kb_id": str(kb["id"]), "question": "P368 model"},
    )

    assert response.status_code == 200
    bm25_results = app.state.rag_service.tools._bm25_chunks_by_kb[str(kb["id"])]
    assert bm25_results[0]["content"] == "P368 unique model setup guide."
