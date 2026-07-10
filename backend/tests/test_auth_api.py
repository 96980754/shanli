from fastapi.testclient import TestClient

from app.main import create_app


def test_login_returns_session_token_for_default_admin():
    client = TestClient(create_app())

    response = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "admin"},
    )

    assert response.status_code == 200
    assert response.json()["token"]


def test_login_then_me_returns_default_admin_profile():
    client = TestClient(create_app())

    login_response = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "admin"},
    )

    token = login_response.json()["token"]
    me_response = client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert login_response.status_code == 200
    assert me_response.status_code == 200
    assert me_response.json()["user_id"] == "admin"
    assert me_response.json()["username"] == "admin"


def test_me_requires_valid_session_token():
    client = TestClient(create_app())

    response = client.get("/api/auth/me")

    assert response.status_code == 401


def test_me_rejects_invalid_session_token():
    client = TestClient(create_app())

    response = client.get(
        "/api/auth/me",
        headers={"Authorization": "Bearer invalid-token"},
    )

    assert response.status_code == 401


def test_me_rejects_invalid_authorization_header_format():
    client = TestClient(create_app())

    response = client.get(
        "/api/auth/me",
        headers={"Authorization": "Token abc"},
    )

    assert response.status_code == 401
