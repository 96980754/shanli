"""POST /api/agent/runs 的 output_format 透传验证（mock 掉后端重依赖）。"""

from __future__ import annotations

import importlib
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from server.utils.auth_middleware import get_db, get_required_user

agent_router_module = importlib.import_module("server.routers.agent_router")


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
        return None

    async def fake_create_agent_run_view(**kwargs):
        captured["input_message"] = kwargs.get("input_message")
        return {"run_id": "run-1", "status": "created"}

    monkeypatch.setattr(agent_router_module, "try_create_curated_qa_run", fake_try_create_curated_qa_run)
    monkeypatch.setattr(agent_router_module, "create_agent_run_view", fake_create_agent_run_view)
    return TestClient(app)


@pytest.fixture
def client(monkeypatch):
    captured: dict = {}
    return _build_app(monkeypatch, captured=captured), captured


def _post(client, *, query: str = "某产品有哪些认证？", output_format: str = "table"):
    return client.post(
        "/api/agent/runs",
        json={
            "query": query,
            "agent_slug": "default-chatbot",
            "thread_id": "thread-1",
            "output_format": output_format,
        },
    )


def test_output_format_flows_into_model_input(client):
    test_client, captured = client

    response = _post(test_client, output_format="table")

    assert response.status_code == 200
    assert response.json() == {"run_id": "run-1", "status": "created"}
    input_message = captured["input_message"]
    assert input_message is not None
    assert input_message.content == "某产品有哪些认证？"  # 展示内容保持干净
    model_query = input_message.require_langchain_message().content
    assert "<output_format>" in model_query
    assert "Markdown 表格" in model_query


def test_output_format_default_does_not_append(client):
    test_client, captured = client

    response = _post(test_client, output_format="default")

    assert response.status_code == 200
    input_message = captured["input_message"]
    assert input_message.content == "某产品有哪些认证？"
    assert "<output_format>" not in input_message.require_langchain_message().content


def test_output_format_invalid_value_rejected(client):
    test_client, _ = client

    response = _post(test_client, output_format="card")

    assert response.status_code == 422


def _post_image(client, *, query: str = "", image_content: str = "iVBORw0KGgo="):
    return client.post(
        "/api/agent/runs",
        json={
            "query": query,
            "image_content": image_content,
            "agent_slug": "default-chatbot",
            "thread_id": "thread-1",
        },
    )


def test_image_only_run_builds_multimodal_input_message(client):
    """仅上传图片（无文字）不应 422，且应构造多模态输入消息。"""
    test_client, captured = client

    response = _post_image(test_client)

    assert response.status_code == 200
    input_message = captured["input_message"]
    assert input_message is not None
    assert input_message.message_type == "multimodal_image"
    assert input_message.image_content == "iVBORw0KGgo="
    content_parts = input_message.require_langchain_message().content
    assert isinstance(content_parts, list)
    assert content_parts[1]["type"] == "image_url"
    assert content_parts[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")


def test_text_and_image_run_builds_multimodal_input_message(client):
    """图片+文字同时上传时，文字保留在输入消息 content 中。"""
    test_client, captured = client

    response = _post_image(test_client, query="这是什么产品？")

    assert response.status_code == 200
    input_message = captured["input_message"]
    assert input_message.message_type == "multimodal_image"
    assert input_message.content == "这是什么产品？"
    content_parts = input_message.require_langchain_message().content
    assert content_parts[0] == {"type": "text", "text": "这是什么产品？"}
    assert content_parts[1]["type"] == "image_url"
