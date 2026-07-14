from zipfile import ZipFile

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from app.core.db import Base
from app.main import create_app
from app.models import AuditLog, ContentBlock, Document, DocumentChunk, ParseTask, Role, User
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
    response = client.post("/api/kb", json={"name": "数据库知识库", "description": "desc", "visibility": "department"})
    assert response.status_code == 200
    assert response.json()["name"] == "数据库知识库"
    assert client.get("/api/kb", headers={"Authorization": f"Bearer {token}"}).json()["items"][0]["name"] == "数据库知识库"


def test_database_mode_upload_saves_original_file_metadata(tmp_path):
    app = build_database_app()
    app.state.file_storage_root = tmp_path
    client = TestClient(app)
    token = login_default_admin(client)
    kb = client.post("/api/kb", json={"name": "文件库"}).json()

    response = client.post(
        f"/api/kb/{kb['id']}/documents/upload",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("产品手册.txt", b"SOS alarm", "text/plain")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["original_filename"] == "产品手册.txt"
    assert body["content_type"] == "text/plain"
    assert body["file_size"] == 9
    assert body["storage_key"].startswith(f"knowledge-bases/{kb['id']}/documents/")
    assert body["download_available"] is True
    assert (tmp_path / body["storage_key"]).read_bytes() == b"SOS alarm"


def test_database_mode_upload_removes_saved_file_when_ingestion_fails(tmp_path, monkeypatch):
    app = build_database_app()
    app.state.file_storage_root = tmp_path
    client = TestClient(app, raise_server_exceptions=False)
    token = login_default_admin(client)
    kb = client.post("/api/kb", json={"name": "文件库"}).json()

    def fail_ingestion(self, document):
        raise RuntimeError("parse failure")

    monkeypatch.setattr("app.main.IngestionService.ingest_uploaded_document", fail_ingestion)

    response = client.post(
        f"/api/kb/{kb['id']}/documents/upload",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("manual.txt", b"SOS alarm", "text/plain")},
    )

    assert response.status_code == 500
    assert not any(path.is_file() for path in tmp_path.rglob("*"))
    assert app.state.db_session.query(Document).count() == 0
    assert app.state.db_session.query(ParseTask).count() == 0
    assert app.state.db_session.query(ContentBlock).count() == 0
    assert app.state.db_session.query(DocumentChunk).count() == 0
    assert app.state.kb_service.get(kb["id"]).doc_count == 0


def test_database_mode_document_upload_updates_persisted_doc_count():
    client = TestClient(build_database_app())
    token = login_default_admin(client)
    kb = client.post("/api/kb", json={"name": "数据库知识库", "visibility": "department"}).json()
    response = client.post(f"/api/kb/{kb['id']}/documents/upload", headers={"Authorization": f"Bearer {token}"}, files={"file": ("manual.pdf", b"PDF", "application/pdf")})
    assert response.status_code == 200
    assert response.json()["title"] == "manual.pdf"
    assert client.get(f"/api/kb/{kb['id']}", headers={"Authorization": f"Bearer {token}"}).json()["doc_count"] == 1


def test_database_mode_upload_stages_file_and_returns_parse_task(tmp_path):
    app = build_database_app()
    app.state.file_storage_root = tmp_path
    client = TestClient(app)
    token = login_default_admin(client)
    kb = client.post("/api/kb", json={"name": "入库知识库", "visibility": "department"}).json()
    response = client.post(f"/api/kb/{kb['id']}/documents/upload", headers={"Authorization": f"Bearer {token}"}, files={"file": ("manual.txt", b"SOS alarm", "text/plain")})
    assert response.status_code == 200
    body = response.json()
    assert body["parse_task_id"] is not None
    assert body["block_count"] == 1
    assert body["chunk_count"] == 1
    assert (tmp_path / body["storage_key"]).read_bytes() == b"SOS alarm"


def test_database_mode_upload_docx_creates_blocks_and_chunks(tmp_path):
    app = build_database_app()
    app.state.file_storage_root = tmp_path
    client = TestClient(app)
    token = login_default_admin(client)
    kb = client.post("/api/kb", json={"name": "Word知识库", "visibility": "department"}).json()
    response = client.post(f"/api/kb/{kb['id']}/documents/upload", headers={"Authorization": f"Bearer {token}"}, files={"file": ("mcstars.docx", build_docx_bytes("MCSTARS 产品白皮书", "MCSTARS 支持统一调度。"), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")})
    assert response.status_code == 200
    assert response.json()["block_count"] == 2
    assert response.json()["chunk_count"] == 2
    assert response.json()["status"] == "parsed"
    assert response.json()["parse_available"] is True
    assert response.json()["parse_status_label"] == "已解析，可用于问答"
    assert client.post("/api/qa/ask/sync", headers={"Authorization": f"Bearer {token}"}, json={"kb_id": str(kb["id"]), "question": "MCSTARS 调度"}).json()["sources"]


def test_database_mode_upload_persists_metadata_fields(tmp_path):
    app = build_database_app()
    app.state.file_storage_root = tmp_path
    client = TestClient(app)
    token = login_default_admin(client)
    kb = client.post("/api/kb", json={"name": "元数据知识库", "visibility": "department"}).json()
    response = client.post(f"/api/kb/{kb['id']}/documents/upload", headers={"Authorization": f"Bearer {token}"}, data={"department": "售后", "product_line": "P368", "visibility": "internal", "security_level": "2", "tags": "FAQ,报警"}, files={"file": ("manual.txt", b"SOS alarm", "text/plain")})
    assert response.status_code == 200
    body = response.json()
    assert body["department"] == "售后"
    assert body["product_line"] == "P368"
    assert body["visibility"] == "internal"
    assert body["security_level"] == 2
    assert body["tags"] == "FAQ,报警"


def test_database_mode_qa_uses_uploaded_document_chunks(tmp_path):
    app = build_database_app()
    app.state.file_storage_root = tmp_path
    client = TestClient(app)
    token = login_default_admin(client)
    kb = client.post("/api/kb", json={"name": "问答知识库", "visibility": "department"}).json()
    client.post(f"/api/kb/{kb['id']}/documents/upload", headers={"Authorization": f"Bearer {token}"}, files={"file": ("manual.txt", b"SOS alarm can be disabled in settings.", "text/plain")})
    response = client.post("/api/qa/ask/sync", headers={"Authorization": f"Bearer {token}"}, json={"kb_id": str(kb["id"]), "question": "SOS alarm"})
    assert response.status_code == 200
    assert response.json()["sources"][0]["content"] == "SOS alarm can be disabled in settings."


def test_database_mode_qa_builds_bm25_index_from_uploaded_chunks(tmp_path):
    app = build_database_app()
    app.state.file_storage_root = tmp_path
    client = TestClient(app)
    token = login_default_admin(client)
    kb = client.post("/api/kb", json={"name": "BM25知识库", "visibility": "department"}).json()
    client.post(f"/api/kb/{kb['id']}/documents/upload", headers={"Authorization": f"Bearer {token}"}, files={"file": ("manual.txt", b"P368 unique model setup guide.", "text/plain")})
    response = client.post("/api/qa/ask/sync", headers={"Authorization": f"Bearer {token}"}, json={"kb_id": str(kb["id"]), "question": "P368 model"})
    assert response.status_code == 200
    assert app.state.rag_service.tools._bm25_chunks_by_kb[f"{kb['id']}:user:1"][0]["content"] == "P368 unique model setup guide."


def test_database_mode_document_list_and_detail_hide_documents_outside_view_rule():
    app = build_database_app()
    client = TestClient(app)
    admin_token = login_default_admin(client)
    session = app.state.db_session
    owner = session.query(User).filter(User.username == "admin").one()
    viewer = User(username="viewer", password_hash="hash", role=owner.role)
    session.add(viewer)
    session.commit()
    viewer_token = app.state.session_store.create(user_id=str(viewer.id), username=viewer.username)
    kb = client.post("/api/kb", json={"name": "受控文档库", "visibility": "department"}).json()
    allowed = app.state.document_service.upload(kb["id"], "allowed.txt", b"")
    allowed.scope, allowed.department, allowed.product, allowed.security_level = "I", "售后", "MC", 1
    blocked = app.state.document_service.upload(kb["id"], "blocked.txt", b"")
    blocked.scope, blocked.department, blocked.product, blocked.security_level = "R", "研发", "MS", 3
    session.commit()
    client.put(f"/api/kb/{kb['id']}/permissions/{viewer.id}", headers={"Authorization": f"Bearer {admin_token}"}, json={"can_view": True, "can_upload": False, "can_delete": False, "can_grant": False})
    client.put(f"/api/kb/{kb['id']}/view-rules/{viewer.id}", headers={"Authorization": f"Bearer {admin_token}"}, json={"allowed_departments": ["售后"], "allowed_product_lines": ["MC"], "allowed_visibilities": ["internal"], "max_security_level": 1})
    listed = client.get(f"/api/kb/{kb['id']}/documents", headers={"Authorization": f"Bearer {viewer_token}"})
    detail = client.get(f"/api/kb/{kb['id']}/documents/{blocked.id}", headers={"Authorization": f"Bearer {viewer_token}"})
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()["items"]] == [allowed.id]
    assert detail.status_code == 403
    assert detail.json()["detail"] == "Permission denied"


def test_database_mode_upload_keeps_pptx_downloadable_without_chunks(tmp_path):
    app = build_database_app()
    app.state.file_storage_root = tmp_path
    client = TestClient(app)
    token = login_default_admin(client)
    kb = client.post("/api/kb", json={"name": "方案库"}).json()

    response = client.post(
        f"/api/kb/{kb['id']}/documents/upload",
        headers={"Authorization": f"Bearer {token}"},
        files={
            "file": (
                "方案.pptx",
                b"presentation",
                "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            )
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "stored_unsupported"
    assert body["parse_available"] is False
    assert body["parse_status_label"] == "仅可下载（暂不支持内容解析）"
    assert body["block_count"] == 0
    assert body["chunk_count"] == 0
    assert body["download_available"] is True
    assert app.state.db_session.query(ParseTask).count() == 0


def test_database_mode_upload_keeps_xlsx_downloadable_without_chunks(tmp_path):
    app = build_database_app()
    app.state.file_storage_root = tmp_path
    client = TestClient(app)
    token = login_default_admin(client)
    kb = client.post("/api/kb", json={"name": "清单库"}).json()

    response = client.post(
        f"/api/kb/{kb['id']}/documents/upload",
        headers={"Authorization": f"Bearer {token}"},
        files={
            "file": (
                "功能清单.xlsx",
                b"spreadsheet",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "stored_unsupported"
    assert body["parse_available"] is False
    assert body["download_available"] is True
    assert body["chunk_count"] == 0
    assert app.state.db_session.query(ParseTask).count() == 0
