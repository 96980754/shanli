"""POST /api/agent/runs 的 version_ask 透传验证：结构化请求短路到 create_agent_run_view，
先于 curated 命中检测（防合成提问被问答对劫持）；不带 version_ask 时原路径不受影响。"""

from __future__ import annotations

import importlib
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.utils.auth_middleware import get_db, get_required_user

agent_router_module = importlib.import_module("server.routers.agent_router")

_VERSION_ASK = {
    "kb_id": "kb-1",
    "action": "read",
    "file_ids": ["f-1"],
    "title": "运营手册",
    "versions": [
        {"file_id": "f-1", "document_version": 1.1, "filename": "运营手册_V1.1.docx", "is_current": False}
    ],
}


def _build_app(monkeypatch: pytest.MonkeyPatch, *, captured: dict) -> TestClient:
    app = FastAPI()
    app.include_router(agent_router_module.agent_router, prefix="/api")

    async def fake_db():
        return object()

    app.dependency_overrides[get_db] = fake_db

    async def fake_user():
        return SimpleNamespace(uid="user-1", role="user", department_id=1)

    app.dependency_overrides[get_required_user] = fake_user

    async def fake_try_create_curated_qa_run(**kwargs):
        captured["curated_calls"] = captured.get("curated_calls", 0) + 1
        del kwargs
        return None

    async def fake_create_agent_run_view(**kwargs):
        captured["input_message"] = kwargs.get("input_message")
        captured["version_ask"] = kwargs.get("version_ask")
        return {"run_id": "run-1", "status": "created"}

    monkeypatch.setattr(agent_router_module, "try_create_curated_qa_run", fake_try_create_curated_qa_run)
    monkeypatch.setattr(agent_router_module, "create_agent_run_view", fake_create_agent_run_view)
    return TestClient(app)


@pytest.fixture
def client(monkeypatch):
    captured: dict = {}
    return _build_app(monkeypatch, captured=captured), captured


def _post_version_ask(test_client):
    return test_client.post(
        "/api/agent/runs",
        json={
            "query": "查看《运营手册》历史版本 V1.1 的内容",
            "agent_slug": "default-chatbot",
            "thread_id": "thread-1",
            "version_ask": _VERSION_ASK,
        },
    )


def test_version_ask_short_circuits_before_curated_and_round_trips(client):
    test_client, captured = client

    response = _post_version_ask(test_client)

    assert response.status_code == 200
    assert response.json() == {"run_id": "run-1", "status": "created"}
    assert captured.get("curated_calls", 0) == 0  # 短路到 create_agent_run_view，未探测 QA
    assert captured["version_ask"] == _VERSION_ASK
    assert captured["input_message"] is not None
    assert captured["input_message"].content == "查看《运营手册》历史版本 V1.1 的内容"


def test_plain_chat_without_version_ask_still_checks_curated(client):
    test_client, captured = client

    response = test_client.post(
        "/api/agent/runs",
        json={
            "query": "某产品有哪些认证？",
            "agent_slug": "default-chatbot",
            "thread_id": "thread-1",
        },
    )

    assert response.status_code == 200
    assert captured["curated_calls"] == 1
    assert captured["version_ask"] is None
