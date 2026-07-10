from fastapi.testclient import TestClient

from app.main import app


def login_default_admin(client: TestClient) -> str:
    response = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "admin"},
    )
    return response.json()["token"]


def test_upload_document_records_pending_status_and_increments_doc_count():
    client = TestClient(app)
    token = login_default_admin(client)
    kb = client.post("/api/kb", json={"name": "上传测试库"}).json()

    response = client.post(
        f"/api/kb/{kb['id']}/documents/upload",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("manual.txt", b"SOS alarm setup", "text/plain")},
    )

    assert response.status_code == 200
    doc = response.json()
    assert doc["title"] == "manual.txt"
    assert doc["status"] == "pending"
    assert doc["file_type"] == "txt"

    kb_after = client.get(
        f"/api/kb/{kb['id']}",
        headers={"Authorization": f"Bearer {token}"},
    ).json()
    assert kb_after["doc_count"] == 1


def test_list_documents_returns_uploaded_document():
    client = TestClient(app)
    token = login_default_admin(client)
    kb = client.post("/api/kb", json={"name": "文档列表库"}).json()
    uploaded = client.post(
        f"/api/kb/{kb['id']}/documents/upload",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("faq.pdf", b"PDF", "application/pdf")},
    ).json()

    response = client.get(
        f"/api/kb/{kb['id']}/documents",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json()["items"][0]["id"] == uploaded["id"]


def test_upload_document_records_metadata_fields():
    client = TestClient(app)
    token = login_default_admin(client)
    kb = client.post("/api/kb", json={"name": "产品知识库", "visibility": "department"}).json()

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
