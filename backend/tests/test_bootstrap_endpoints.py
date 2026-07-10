from fastapi.testclient import TestClient

from app.main import app


def test_review_endpoint_returns_empty_items_initially():
    client = TestClient(app)

    response = client.get("/api/review")

    assert response.status_code == 200
    assert response.json() == {"items": []}


def test_conflicts_endpoint_returns_empty_items_initially():
    client = TestClient(app)

    response = client.get("/api/conflicts")

    assert response.status_code == 200
    assert response.json() == {"items": []}


def test_dashboard_summary_returns_zero_counts_initially():
    client = TestClient(app)

    response = client.get("/api/dashboard/summary")

    assert response.status_code == 200
    assert response.json() == {
        "pending_review": 0,
        "pending_conflicts": 0,
        "pending_tasks": 0,
        "open_issues": 0,
    }


def test_issues_endpoint_returns_empty_items_initially():
    client = TestClient(app)

    response = client.get("/api/issues")

    assert response.status_code == 200
    assert response.json() == {"items": []}
