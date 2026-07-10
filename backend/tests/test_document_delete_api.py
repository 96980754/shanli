from fastapi.testclient import TestClient

from app.main import create_app


def login_default_admin(client: TestClient) -> str:
    response = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "admin"},
    )
    return response.json()["token"]


def test_delete_document_requires_login():
    client = TestClient(create_app())
    token = login_default_admin(client)
    kb = client.post("/api/kb", json={"name": "删除库"}).json()
    uploaded = client.post(
        f"/api/kb/{kb['id']}/documents/upload",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("manual.txt", b"content", "text/plain")},
    ).json()

    response = client.delete(f"/api/kb/{kb['id']}/documents/{uploaded['id']}")

    assert response.status_code == 401


def test_delete_document_removes_it_from_list_and_detail():
    client = TestClient(create_app())
    token = login_default_admin(client)
    kb = client.post("/api/kb", json={"name": "删除库"}).json()
    uploaded = client.post(
        f"/api/kb/{kb['id']}/documents/upload",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("manual.txt", b"content", "text/plain")},
    ).json()

    response = client.delete(
        f"/api/kb/{kb['id']}/documents/{uploaded['id']}",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json() == {"deleted": True}
    assert client.get(
        f"/api/kb/{kb['id']}/documents/{uploaded['id']}",
        headers={"Authorization": f"Bearer {token}"},
    ).status_code == 404
    assert client.get(
        f"/api/kb/{kb['id']}/documents",
        headers={"Authorization": f"Bearer {token}"},
    ).json()["items"] == []


def test_delete_document_without_can_delete_returns_403():
    app = create_app()
    client = TestClient(app)
    admin_token = login_default_admin(client)
    kb = client.post("/api/kb", json={"name": "删除权限库"}).json()
    client.put(
        f"/api/kb/{kb['id']}/permissions/4001",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "can_view": True,
            "can_upload": True,
            "can_delete": False,
            "can_grant": False,
        },
    )
    uploaded = client.post(
        f"/api/kb/{kb['id']}/documents/upload",
        headers={"Authorization": f"Bearer {admin_token}"},
        files={"file": ("manual.txt", b"content", "text/plain")},
    ).json()
    limited_token = app.state.session_store.create(user_id="4001", username="u4001")

    response = client.delete(
        f"/api/kb/{kb['id']}/documents/{uploaded['id']}",
        headers={"Authorization": f"Bearer {limited_token}"},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "Permission denied"
