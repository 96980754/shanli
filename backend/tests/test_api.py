from fastapi.testclient import TestClient

from app.main import app


def test_health_endpoint_returns_ok():
    client = TestClient(app)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ask_sync_returns_answer_and_sources():
    client = TestClient(app)
    token = client.post("/api/auth/login", json={"username": "admin", "password": "admin"}).json()["token"]
    kb = client.post("/api/kb", json={"name": "问答知识库", "visibility": "department"}).json()
    app.state.rag_service.tools.vector_chunks_by_kb[kb["id"]] = [
        {
            "chunk_id": "c1",
            "content": "SOS 报警可以在设置中的报警设置里关闭。",
            "score": 0.9,
            "doc_title": "P368用户手册",
        }
    ]

    response = client.post(
        "/api/qa/ask/sync",
        headers={"Authorization": f"Bearer {token}"},
        json={"question": "SOS 报警怎么关闭", "kb_id": kb["id"]},
    )

    assert response.status_code == 200
    body = response.json()
    assert "SOS" in body["answer"]
    assert body["sources"][0]["chunk_id"] == "c1"
