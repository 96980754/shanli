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
        # 与真实 AgentContext dataclass 默认值对齐：预检会直接读取这两个字段。
        self.system_prompt: str | None = None
        self.knowledges: list[str] | None = None

    def update(self, data: dict):
        for key, value in data.items():
            setattr(self, key, value)


class _FakeSession:
    async def commit(self):
        pass


def _as_message_obj(message: dict) -> SimpleNamespace:
    obj = SimpleNamespace()
    for key, value in message.items():
        setattr(obj, key, value)
    return obj


class _FakeConvRepo:
    saved_messages: list[dict] = []

    def __init__(self, _db):
        self.conversations: dict[str, SimpleNamespace] = {}

    def _conversation(self, thread_id: str) -> SimpleNamespace:
        return self.conversations.setdefault(
            thread_id,
            SimpleNamespace(
                id=1, uid="user-1", agent_id="test-agent", thread_id=thread_id, status="active", extra_metadata={}
            ),
        )

    async def add_message_by_thread_id(self, **kwargs):
        self.saved_messages.append(kwargs)
        return SimpleNamespace(id=len(self.saved_messages))

    async def get_messages_by_thread_id(self, thread_id: str, limit: int | None = None, offset: int = 0):
        """镜像真实仓库：按会话返回消息（升序），供首答前文读取使用."""
        messages = self.saved_messages[offset:]
        if limit is not None:
            messages = messages[:limit]
        return [_as_message_obj(m) for m in messages]

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


def _install_harness(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, int]]:
    output_messages: list[tuple[str, int]] = []
    _FakeConvRepo.saved_messages = []

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

    async def fake_build_scope_corpus(**_kwargs):
        # 隔离范围门：不读真实业务线/知识库，仅作为可被 evaluate_scope 消费的空语料。
        return SimpleNamespace(terms=frozenset(), anchors=[], description="")

    async def fake_evaluate_scope(_question, _corpus, **_kwargs):
        # 预检分支单元测试只关心“判定为业务内”→ 继续走检索/零命中落库，范围门本身另有测试。
        return "in_scope"

    async def fake_interrupts(agent, langgraph_config, make_chunk, meta, thread_id, context):
        if False:
            yield None
        return

    async def fake_normalize_agent_context_config(context, **_kwargs):
        return dict(context or {})

    class FakeRunRepository:
        def __init__(self, _db):
            pass

        async def set_output_message(self, run_id, message_id):
            output_messages.append((run_id, message_id))

    monkeypatch.setattr(svc, "_resolve_agent_runtime", fake_resolve_agent_runtime)
    monkeypatch.setattr(svc, "normalize_agent_context_config", fake_normalize_agent_context_config)
    monkeypatch.setattr(svc, "build_scope_corpus", fake_build_scope_corpus)
    monkeypatch.setattr(svc, "evaluate_scope", fake_evaluate_scope)
    monkeypatch.setattr(svc, "ConversationRepository", _FakeConvRepo)
    monkeypatch.setattr(svc, "AgentRunRepository", FakeRunRepository)
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
    return output_messages


async def _run_stream(input_message) -> list[dict]:
    chunks = []
    async for chunk in svc.stream_agent_chat(
        agent_slug="test-agent",
        thread_id="thread-1",
        meta={"request_id": "req-1", "run_id": "run-1"},
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
async def test_zero_result_preflight_persists_structured_refusal_and_run_output(monkeypatch):
    async def fake_search_with_status(self, user, query):
        return [], False

    monkeypatch.setattr(GlobalKnowledgeSearchService, "search_with_status", fake_search_with_status)
    output_messages = _install_harness(monkeypatch)

    chunks = await _run_stream(build_chat_input_message("未知产品参数"))

    assistant = _FakeConvRepo.saved_messages[-1]
    assert assistant["extra_metadata"]["knowledge_disposition"] == {
        "schema_version": 2,
        "type": "knowledge_refusal",
        "reason": "no_results",
        "domain": "unknown",
    }
    assert assistant["extra_metadata"]["handoff_available"] is True
    assert output_messages == [("run-1", 2)]
    assert any(chunk["status"] == "knowledge_handoff_available" for chunk in chunks)


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


@pytest.mark.asyncio
async def test_zero_hit_preflight_runs_once_per_thread_then_reroutes_to_agent(monkeypatch):
    """回归：拒答一次后重问不再被零命中预检反复短路（每线程至多一次）。

    首问全局搜无结果 → 结构化拒答落库；同线程第二条首答轮（上一条 assistant 仍为拒答、
    线程未正常作答）应跳过零命中预检、放行主模型逐库检索，而不是再次被同一粗粒度搜索拒掉。
    """
    search_calls: list[str] = []

    async def fake_search_with_status(self, user, query):
        search_calls.append(query)
        return [], False  # 全局零命中 → 首问结构化拒答

    monkeypatch.setattr(GlobalKnowledgeSearchService, "search_with_status", fake_search_with_status)
    _install_harness(monkeypatch)

    # 第 1 轮（线程首条消息）：全局搜无结果 → 结构化拒答落库并回显。
    first_chunks = await _run_stream(build_chat_input_message("MNO是什么"))
    assert search_calls == ["MNO是什么"]
    assert any(chunk["status"] == "knowledge_handoff_available" for chunk in first_chunks)

    # 第 2 轮（同线程重问）：零命中预检不再重跑，直接放行主模型作答。
    second_chunks = await _run_stream(build_chat_input_message("MNO？？"))
    assert search_calls == ["MNO是什么"]  # 未被再次触发
    assert any(chunk.get("response") == "识别结果" for chunk in second_chunks)
    assert not any(chunk["status"] == "knowledge_handoff_available" for chunk in second_chunks)


@pytest.mark.asyncio
async def test_off_topic_chitchat_first_turn_refused_by_scope_no_handoff(monkeypatch):
    """类别2 闲聊·首答轮：业务外话题在进主模型前被入口 scope 门判 off_topic 拦截。

    落 scope_refusal/off_topic、不转人工；scope 拦截先于零命中预检，全局搜索不触发。
    """
    scope_calls: list[str] = []
    search_calls: list[str] = []

    async def fake_scope(question, _corpus):
        scope_calls.append(question)
        return "off_topic"

    async def fake_search_with_status(self, user, query):
        search_calls.append(query)
        return [], False

    monkeypatch.setattr(GlobalKnowledgeSearchService, "search_with_status", fake_search_with_status)
    _install_harness(monkeypatch)
    monkeypatch.setattr(svc, "evaluate_scope", fake_scope)  # 晚于 harness：仅本用例判业务外

    chunks = await _run_stream(build_chat_input_message("今天天气怎么样"))

    assistant = _FakeConvRepo.saved_messages[-1]
    assert assistant["role"] == "assistant"
    assert assistant["extra_metadata"]["knowledge_disposition"] == {
        "schema_version": 2,
        "type": "scope_refusal",
        "reason": "off_topic",
    }
    assert "handoff_available" not in assistant["extra_metadata"]  # 业务外闲聊不转人工
    assert scope_calls == ["今天天气怎么样"]
    assert search_calls == []  # scope 拦截在零命中预检之前，全局搜索不触发
    statuses = [chunk["status"] for chunk in chunks]
    assert "knowledge_handoff_available" not in statuses
    assert "finished" in statuses


@pytest.mark.asyncio
async def test_scope_refusal_then_business_reask_reroutes_to_agent(monkeypatch):
    """类别5 多轮·拒答不短路：scope 拒答后的业务重问放行主模型，不再重跑零命中预检。

    scope 拒答无 handoff、仍让线程保持“未正常作答”，但重问业务问题（scope 放行）时
    因已非线程首条消息，零命中预检不再触发，直接进主模型逐库检索。
    """
    scope_calls: list[str] = []
    search_calls: list[str] = []

    async def fake_scope(question, _corpus):
        scope_calls.append(question)
        return "off_topic" if "天气" in question else "in_scope"

    async def fake_search_with_status(self, user, query):
        search_calls.append(query)
        return [], False

    monkeypatch.setattr(GlobalKnowledgeSearchService, "search_with_status", fake_search_with_status)
    _install_harness(monkeypatch)
    monkeypatch.setattr(svc, "evaluate_scope", fake_scope)

    # 第 1 轮（业务外）：scope 拒答落库，无 handoff。
    await _run_stream(build_chat_input_message("今天天气怎么样"))
    refusal = _FakeConvRepo.saved_messages[-1]
    assert refusal["extra_metadata"]["knowledge_disposition"]["type"] == "scope_refusal"
    assert "handoff_available" not in refusal["extra_metadata"]

    # 第 2 轮（同线程重问业务）：scope 放行 → 零命中预检不再重跑，放行主模型作答。
    second_chunks = await _run_stream(build_chat_input_message("调度台怎么开通？"))
    assert scope_calls == ["今天天气怎么样", "调度台怎么开通？"]  # 未作答前 scope 门对每条新问题仍拦
    assert search_calls == []  # 全局零命中预检未在重问时再次触发
    assert any(chunk.get("response") == "识别结果" for chunk in second_chunks)
    assert not any(chunk["status"] == "knowledge_handoff_available" for chunk in second_chunks)


@pytest.mark.asyncio
async def test_greeting_first_turn_bypasses_preflight(monkeypatch):
    """类别1 问候·首答轮：问候由 _requires_knowledge_preflight 在入口短路，不进 scope 门/零命中预检。"""
    scope_calls: list[str] = []
    search_calls: list[str] = []

    async def fake_scope(question, _corpus):
        scope_calls.append(question)
        return "in_scope"

    async def fake_search_with_status(self, user, query):
        search_calls.append(query)
        return [], False

    monkeypatch.setattr(GlobalKnowledgeSearchService, "search_with_status", fake_search_with_status)
    _install_harness(monkeypatch)
    monkeypatch.setattr(svc, "evaluate_scope", fake_scope)

    chunks = await _run_stream(build_chat_input_message("你好"))

    assert scope_calls == []  # 问候豁免先于 scope 门短路
    assert search_calls == []
    assert any(chunk.get("response") == "识别结果" for chunk in chunks)


@pytest.mark.asyncio
async def test_business_after_answered_turn_skips_preflight(monkeypatch):
    """多轮·会话答后首条业务问题：线程已有正常作答后，后续业务问题不再被当首答轮预检。

    预置一条 disposition=answered 的上一条终答 → 本业务轮 first_substantive=False，
    直接放行主模型结合上下文作答（不重跑 scope 门与零命中预检）。
    """
    scope_calls: list[str] = []
    search_calls: list[str] = []

    async def fake_scope(question, _corpus):
        scope_calls.append(question)
        return "in_scope"

    async def fake_search_with_status(self, user, query):
        search_calls.append(query)
        return [], False

    monkeypatch.setattr(GlobalKnowledgeSearchService, "search_with_status", fake_search_with_status)
    _install_harness(monkeypatch)
    monkeypatch.setattr(svc, "evaluate_scope", fake_scope)
    # 线程历史：上一条 assistant 为正常作答（入口判定只看是否“正常终答”）。
    _FakeConvRepo.saved_messages.append(
        {
            "thread_id": "thread-1",
            "role": "assistant",
            "content": "上轮回答",
            "message_type": "text",
            "extra_metadata": {"knowledge_disposition": {"schema_version": 2, "type": "answered"}},
            "run_id": None,
            "request_id": None,
        }
    )

    chunks = await _run_stream(build_chat_input_message("调度台怎么开通？"))

    assert scope_calls == []  # 非首答轮不进入口门
    assert search_calls == []  # 零命中预检仅在每线程首条消息执行
    assert any(chunk.get("response") == "识别结果" for chunk in chunks)
