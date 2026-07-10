from fastapi.testclient import TestClient

from app.main import create_app


def login_default_admin(client: TestClient) -> str:
    response = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "admin"},
    )
    return response.json()["token"]


def test_update_knowledge_base_changes_name_description_and_visibility():
    client = TestClient(create_app())
    created = client.post(
        "/api/kb",
        json={"name": "旧名称", "description": "旧描述", "visibility": "department"},
    ).json()

    response = client.put(
        f"/api/kb/{created['id']}",
        json={"name": "新名称", "description": "新的描述", "visibility": "private"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == created["id"]
    assert body["name"] == "新名称"
    assert body["description"] == "新的描述"
    assert body["visibility"] == "private"


def test_delete_knowledge_base_removes_it_from_list_and_detail():
    client = TestClient(create_app())
    token = login_default_admin(client)
    created = client.post("/api/kb", json={"name": "待删除库"}).json()

    response = client.delete(
        f"/api/kb/{created['id']}",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json() == {"deleted": True}
    ids = [item["id"] for item in client.get("/api/kb", headers={"Authorization": f"Bearer {token}"}).json()["items"]]
    assert created["id"] not in ids
    assert client.get(
        f"/api/kb/{created['id']}",
        headers={"Authorization": f"Bearer {token}"},
    ).status_code == 403


def test_update_missing_knowledge_base_returns_404():
    client = TestClient(create_app())

    response = client.put(
        "/api/kb/missing-kb",
        json={"name": "新名称", "description": "新的描述", "visibility": "department"},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Knowledge base not found"


def test_delete_missing_knowledge_base_returns_404():
    client = TestClient(create_app())
    token = login_default_admin(client)

    response = client.delete(
        "/api/kb/missing-kb",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "Permission denied"


def test_ungranted_user_cannot_see_knowledge_base_in_list():
    admin_app = create_app()
    admin_client = TestClient(admin_app)
    admin_token = login_default_admin(admin_client)
    created = admin_client.post("/api/kb", json={"name": "私有库"}).json()
    admin_client.put(
        f"/api/kb/{created['id']}/permissions/2001",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "can_view": False,
            "can_upload": False,
            "can_delete": False,
            "can_grant": False,
        },
    )

    viewer_token = admin_app.state.session_store.create(user_id="2001", username="viewer")

    response = admin_client.get(
        "/api/kb",
        headers={"Authorization": f"Bearer {viewer_token}"},
    )

    assert response.status_code == 200
    assert response.json()["items"] == []
