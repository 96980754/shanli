"""图片消息跳过知识库预检、纯文本消息保留预检的单测。

回归保护：#68 修复——有图片时不能让空文字检索触发的预检把图给拒答掉。
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from langchain.messages import AIMessageChunk
from yuxi.services import chat_service as svc
from yuxi.services.global_knowledge_search_service import GlobalKnowledgeSearchService
from yuxi.services.input_message_service import build_chat_input_message


class _FakeContext:
    def __init__(self):
        self.thread_id = ""
        self.uid = ""
        self.temperature = None

    def update(self, data: dict):
        for key, value in data.items():
            setattr(self, key, value)


class _FakeSession:
    async def commit(self):
        pass


class _FakeConvRepo:
    def __init__(self, _db):
        self.conversations: dict[str, SimpleNamespace] = {}

    def _conversation(self, thread_id: str) -> SimpleNamespace:
        return self.conversations.setdefault(
            thread_id,
            SimpleNamespace(
                id=1, uid="user-1", agent_id="test-agent", thread_id=thread_id, status="active", extra_metadata={}
            ),
        )

    async def add_message_by_thread_id(self, **_kwargs):
        return SimpleNamespace(id=1)

    async def get_conversation_by_thread_id(self, thread_id: str):
        return self._conversation(thread_id)

    async def create_conversation(self, *, uid, agent_id, thread_id, metadata=None):
        conversation = SimpleNamespace(
            id=1, uid=uid, agent_id=agent_id, thread_id=thread_id, status="active", extra_metadata=metadata or {}
        )
        self.conversations[thread_id] = conversation
        return conversation

    async def get_attachments_by_request_id(self, conversation_id, request_id):
        return []

    async def bind_attachments_to_request(self, conversation_id, request_id, file_ids):
        return []


def _install_harness(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeAgent:
        context_schema = _FakeContext

        async def stream_messages_with_state(self, messages, input_context=None, **kwargs):
            yield "messages", (AIMessageChunk(content="识别结果"), {"node": "llm"})

        async def get_graph(self, *, context=None):
            class FakeGraph:
                async def aget_state(self, config):
                    return SimpleNamespace(values={"messages": [], "files": {}, "artifacts": []})

            return FakeGraph()

    async def fake_resolve_agent_runtime(**_kwargs):
        return SimpleNamespace(slug="test-agent", backend_id="ChatbotAgent"), FakeAgent(), {}

    async def fake_save_messages_from_langgraph_state(**kwargs):
        return None

    async def fake_guard_check(_content):
        return False

    async def fake_guard_check_with_keywords(_content):
        return False

    async def fake_interrupts(agent, langgraph_config, make_chunk, meta, thread_id, context):
        if False:
            yield None
        return

    async def fake_normalize_agent_context_config(context, **_kwargs):
        return dict(context or {})

    monkeypatch.setattr(svc, "_resolve_agent_runtime", fake_resolve_agent_runtime)
    monkeypatch.setattr(svc, "normalize_agent_context_config", fake_normalize_agent_context_config)
    monkeypatch.setattr(svc, "ConversationRepository", _FakeConvRepo)
    monkeypatch.setattr(svc, "save_messages_from_langgraph_state", fake_save_messages_from_langgraph_state)
    monkeypatch.setattr(svc.content_guard, "check", fake_guard_check)
    monkeypatch.setattr(svc.content_guard, "check_with_keywords", fake_guard_check_with_keywords)
    monkeypatch.setattr(svc, "check_and_handle_interrupts", fake_interrupts)
    monkeypatch.setattr(
        svc,
        "_build_langfuse_run_context",
        lambda **kwargs: SimpleNamespace(callbacks=[], metadata={}, tags=[], trace_id=None),
    )
    monkeypatch.setattr(svc, "get_trace_info", lambda _run_context: {})
    monkeypatch.setattr(svc, "flush_langfuse", lambda: None)


async def _run_stream(input_message) -> list[dict]:
    chunks = []
    async for chunk in svc.stream_agent_chat(
        agent_slug="test-agent",
        thread_id="thread-1",
        meta={"request_id": "req-1"},
        input_message=input_message,
        current_user=SimpleNamespace(id=1, uid="user-1", role="user", department_id="dept-1"),
        db=_FakeSession(),
    ):
        chunks.append(json.loads(chunk.decode("utf-8")))
    return chunks


@pytest.mark.asyncio
async def test_image_message_skips_knowledge_preflight(monkeypatch):
    search_calls: list[str] = []

    async def fake_search_with_status(self, user, query):
        search_calls.append(query)
        return [], False

    monkeypatch.setattr(GlobalKnowledgeSearchService, "search_with_status", fake_search_with_status)
    _install_harness(monkeypatch)

    chunks = await _run_stream(build_chat_input_message("", image_content="iVBORw0KGgo="))

    assert search_calls == []  # 有图时跳过预检，直接进模型
    statuses = [chunk["status"] for chunk in chunks]
    assert "knowledge_handoff_available" not in statuses  # 未被空检索误拒答
    assert "finished" in statuses
    assert any(chunk.get("response") == "识别结果" for chunk in chunks)


@pytest.mark.asyncio
async def test_text_message_still_runs_knowledge_preflight(monkeypatch):
    search_calls: list[str] = []

    async def fake_search_with_status(self, user, query):
        search_calls.append(query)
        return [{"id": 1}], False

    monkeypatch.setattr(GlobalKnowledgeSearchService, "search_with_status", fake_search_with_status)
    _install_harness(monkeypatch)

    chunks = await _run_stream(build_chat_input_message("某产品参数"))

    assert search_calls == ["某产品参数"]
    assert any(chunk["status"] == "finished" for chunk in chunks)
