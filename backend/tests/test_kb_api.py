from fastapi.testclient import TestClient

from app.main import app


def login_default_admin(client: TestClient) -> str:
    response = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "admin"},
    )
    return response.json()["token"]


def test_create_knowledge_base_then_get_it_by_id():
    client = TestClient(app)
    token = login_default_admin(client)

    create_response = client.post(
        "/api/kb",
        json={"name": "产品线知识库", "description": "产品文档", "visibility": "department"},
    )

    assert create_response.status_code == 200
    created = create_response.json()
    assert created["name"] == "产品线知识库"
    assert created["doc_count"] == 0
    assert created["visibility"] == "department"

    get_response = client.get(
        f"/api/kb/{created['id']}",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert get_response.status_code == 200
    assert get_response.json()["id"] == created["id"]


def test_list_knowledge_bases_returns_created_items():
    client = TestClient(app)
    token = login_default_admin(client)
    client.post("/api/kb", json={"name": "FAQ知识库", "description": "FAQ", "visibility": "all"})

    response = client.get("/api/kb", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    names = [item["name"] for item in response.json()["items"]]
    assert "FAQ知识库" in names
