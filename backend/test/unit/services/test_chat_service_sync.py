from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from langchain.messages import AIMessage, HumanMessage

from yuxi.agents import context as agent_context
from yuxi.agents.backends.sandbox import paths as workspace_paths
from yuxi.services import chat_service as svc


def _empty_agent_context(_thread_id: str, _uid: str) -> str:
    return ""


async def _fake_normalize_agent_context_config(context, **_kwargs):
    return dict(context or {})


@pytest.mark.asyncio
async def test_resolve_agent_runtime_includes_subagents_only_when_requested(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    class FakeAgentRepository:
        def __init__(self, _db):
            pass

        async def get_visible_by_slug(self, *, slug: str, user, kind="main"):
            del user
            assert slug == "worker"
            calls.append(kind)
            if kind == "subagent":
                return SimpleNamespace(slug="worker", backend_id="SubAgentBackend", config_json={"context": {}})
            return None

    class FakeConversationRepository:
        def __init__(self, _db):
            pass

        async def get_conversation_by_thread_id(self, thread_id: str):
            return SimpleNamespace(uid="user-1", agent_id="worker", thread_id=thread_id, status="subagent")

    monkeypatch.setattr(svc, "AgentRepository", FakeAgentRepository)
    monkeypatch.setattr(svc, "ConversationRepository", FakeConversationRepository)
    monkeypatch.setattr(svc, "normalize_agent_context_config", _fake_normalize_agent_context_config)
    monkeypatch.setattr(
        svc.agent_manager,
        "get_agent",
        lambda backend_id: SimpleNamespace(context_schema=None) if backend_id == "SubAgentBackend" else None,
    )

    user = SimpleNamespace(uid="user-1")

    with pytest.raises(ValueError, match="智能体不存在或无权限访问"):
        await svc._resolve_agent_runtime(
            db=object(),
            user=user,
            requested_agent_slug="worker",
            thread_id="child-thread",
        )

    agent_item, backend, agent_config = await svc._resolve_agent_runtime(
        db=object(),
        user=user,
        requested_agent_slug="worker",
        thread_id="child-thread",
        agent_kind="subagent",
    )

    assert calls == ["main", "subagent"]
    assert agent_item.slug == "worker"
    assert backend.context_schema is None
    assert agent_config == {}


class _FakeConvRepo:
    def __init__(self, _db):
        self.db = _db
        self.saved_messages: list[dict] = []
        self.tool_calls: list[dict] = []
        self.conversations: dict[str, SimpleNamespace] = {}

    def _conversation(self, thread_id: str) -> SimpleNamespace:
        return self.conversations.setdefault(
            thread_id,
            SimpleNamespace(
                id=1,
                uid="user-1",
                agent_id="test-agent",
                thread_id=thread_id,
                status="active",
                extra_metadata={},
            ),
        )

    async def add_message_by_thread_id(
        self,
        *,
        thread_id: str,
        role: str,
        content: str,
        message_type: str = "text",
        extra_metadata: dict | None = None,
        image_content: str | None = None,
        run_id: str | None = None,
        request_id: str | None = None,
    ):
        self.saved_messages.append(
            {
                "thread_id": thread_id,
                "role": role,
                "content": content,
                "message_type": message_type,
                "extra_metadata": extra_metadata,
                "image_content": image_content,
                "run_id": run_id,
                "request_id": request_id,
            }
        )
        return SimpleNamespace(id=1)

    async def get_conversation_by_thread_id(self, thread_id: str):
        return self._conversation(thread_id)

    async def get_messages_by_thread_id(self, _thread_id: str):
        return []

    async def add_tool_call(
        self,
        *,
        message_id: int,
        tool_name: str,
        tool_input: dict | None = None,
        status: str = "pending",
        langgraph_tool_call_id: str | None = None,
    ):
        self.tool_calls.append(
            {
                "message_id": message_id,
                "tool_name": tool_name,
                "tool_input": tool_input,
                "status": status,
                "langgraph_tool_call_id": langgraph_tool_call_id,
            }
        )
        return SimpleNamespace(id=len(self.tool_calls))

    async def create_conversation(self, *, uid: str, agent_id: str, thread_id: str, metadata: dict | None = None):
        conversation = SimpleNamespace(
            id=1,
            uid=uid,
            agent_id=agent_id,
            thread_id=thread_id,
            status="active",
            extra_metadata=metadata or {},
        )
        self.conversations[thread_id] = conversation
        return conversation

    async def get_attachments_by_request_id(self, conversation_id: int, request_id: str):
        return []

    async def bind_attachments_to_request(self, conversation_id: int, request_id: str, file_ids: list[str]):
        return []


@pytest.mark.asyncio
async def test_save_messages_from_langgraph_state_handles_dict_tool_call_blocks() -> None:
    class FakeGraph:
        async def aget_state(self, _config):
            return SimpleNamespace(
                values={
                    "messages": [
                        {
                            "id": "ai-tool-call",
                            "role": "assistant",
                            "content": [
                                {
                                    "type": "tool_call",
                                    "id": "call-task-1",
                                    "name": "task",
                                    "args": {"description": "write file", "subagent_slug": "worker"},
                                }
                            ],
                        }
                    ]
                }
            )

    class FakeAgent:
        async def get_graph(self, *, context):
            assert context is fake_context
            return FakeGraph()

    conv_repo = _FakeConvRepo(None)
    fake_context = object()

    await svc.save_messages_from_langgraph_state(
        agent_instance=FakeAgent(),
        thread_id="thread-1",
        conv_repo=conv_repo,
        config_dict={"configurable": {"thread_id": "thread-1", "uid": "user-1"}},
        context=fake_context,
        trace_info=None,
    )

    assert conv_repo.saved_messages[0]["content"] == ""
    assert conv_repo.saved_messages[0]["extra_metadata"]["content"][0]["id"] == "call-task-1"
    assert conv_repo.tool_calls == [
        {
            "message_id": 1,
            "tool_name": "task",
            "tool_input": {"description": "write file", "subagent_slug": "worker"},
            "status": "pending",
            "langgraph_tool_call_id": "call-task-1",
        }
    ]


@pytest.mark.asyncio
async def test_save_messages_from_langgraph_state_backfills_run_output_message(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeDB:
        def __init__(self):
            self.commit_count = 0

        async def commit(self):
            self.commit_count += 1

    class FakeGraph:
        async def aget_state(self, _config):
            return SimpleNamespace(values={"messages": [HumanMessage(content="question"), AIMessage(content="answer")]})

    class FakeAgent:
        async def get_graph(self, *, context):
            assert context is fake_context
            return FakeGraph()

    fake_db = FakeDB()
    conv_repo = _FakeConvRepo(fake_db)
    fake_context = object()
    captured: dict[str, object] = {}

    class FakeRunRepo:
        def __init__(self, db):
            assert db is fake_db

        async def set_output_message(self, run_id: str, message_id: int):
            captured["run_id"] = run_id
            captured["message_id"] = message_id

    monkeypatch.setattr(svc, "AgentRunRepository", FakeRunRepo)
    # “answer”是零检索硬答，会被 ② 改写为拒答并走域/缺口落库；单测里隔离 DB 副作用。
    async def _no_gap(**kwargs):
        return None

    monkeypatch.setattr(svc, "record_knowledge_gap", _no_gap)

    await svc.save_messages_from_langgraph_state(
        agent_instance=FakeAgent(),
        thread_id="thread-1",
        conv_repo=conv_repo,
        config_dict={"configurable": {"thread_id": "thread-1", "uid": "user-1"}},
        context=fake_context,
        trace_info={"langfuse_trace_id": "trace-1"},
        run_id="run-1",
        request_id="req-1",
    )

    assert conv_repo.saved_messages[0]["content"] == "answer"
    assert conv_repo.saved_messages[0]["run_id"] == "run-1"
    assert conv_repo.saved_messages[0]["request_id"] == "req-1"
    assert conv_repo.saved_messages[0]["extra_metadata"]["langfuse_trace_id"] == "trace-1"
    assert captured == {"run_id": "run-1", "message_id": 1}
    assert fake_db.commit_count == 1


@pytest.mark.asyncio
async def test_build_agent_input_context_loads_all_workspace_agent_context_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(workspace_paths.conf, "save_dir", str(tmp_path))
    workspace_paths.ensure_thread_dirs("thread-1", "user-1")
    agents_dir = tmp_path / "threads" / "shared" / "user-1" / "workspace" / "agents"
    (agents_dir / "AGENTS.md").write_text("行为约束", encoding="utf-8")
    (agents_dir / "USER.md").write_text("用户信息", encoding="utf-8")
    (agents_dir / "MEMORY.md").write_text("长期记忆", encoding="utf-8")

    context = await agent_context.build_agent_input_context({}, thread_id="thread-1", uid="user-1")

    assert context["system_prompt"] == (
        "用户工作区 agents/AGENTS.md 内容：\n行为约束\n\n"
        "用户工作区 agents/USER.md 内容：\n用户信息\n\n"
        "用户工作区 agents/MEMORY.md 内容：\n长期记忆"
    )


@pytest.mark.asyncio
async def test_build_agent_input_context_merges_workspace_agent_context(monkeypatch: pytest.MonkeyPatch):
    def fake_agent_context(_thread_id: str, _uid: str) -> str:
        return (
            "用户工作区 agents/AGENTS.md 内容：\n回答前先读取 AGENTS.md\n\n"
            "用户工作区 agents/USER.md 内容：\n用户偏好中文"
        )

    monkeypatch.setattr(agent_context, "_load_workspace_agent_context", fake_agent_context)

    context = await agent_context.build_agent_input_context(
        {"system_prompt": "原始系统提示词", "temperature": 0.1},
        thread_id="thread-1",
        uid="user-1",
    )

    assert context["system_prompt"] == (
        "原始系统提示词\n\n"
        "用户工作区 agents/AGENTS.md 内容：\n回答前先读取 AGENTS.md\n\n"
        "用户工作区 agents/USER.md 内容：\n用户偏好中文"
    )
    assert context["temperature"] == 0.1
    assert context["thread_id"] == "thread-1"
    assert context["uid"] == "user-1"


@pytest.mark.asyncio
async def test_get_agent_state_view_rejects_async_subagent_without_child_conversation(
    monkeypatch: pytest.MonkeyPatch,
):
    child_thread_id = "missing-child-conversation"

    class ConvRepo:
        def __init__(self, _db):
            pass

        async def get_conversation_by_thread_id(self, thread_id: str):
            del thread_id
            return None

    class RunRepo:
        def __init__(self, _db):
            pass

        async def get_latest_subagent_run_by_thread_for_user(self, thread_id: str, uid: str):
            assert thread_id == child_thread_id
            assert uid == "user-1"
            return SimpleNamespace(
                id="child-run",
                conversation_thread_id=child_thread_id,
                agent_slug="worker",
                status="running",
                created_by_run_id="parent-run",
                subagent_thread_relation_id=77,
                input_payload={"runtime": {"tool_call_id": "tool-1"}},
            )

        async def get_run_for_user(self, run_id: str, uid: str):
            del run_id, uid
            raise AssertionError("async subagent state must be loaded through child conversation relation")

    monkeypatch.setattr(svc, "ConversationRepository", ConvRepo)
    monkeypatch.setattr(svc, "AgentRunRepository", RunRepo)

    with pytest.raises(HTTPException) as exc:
        await svc.get_agent_state_view(
            thread_id=child_thread_id,
            current_user=SimpleNamespace(uid="user-1"),
            db=object(),
            include_messages=True,
        )

    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_get_agent_state_view_includes_subagent_thread_relation(monkeypatch: pytest.MonkeyPatch):
    child_thread_id = "child-thread"

    class ConvRepo:
        def __init__(self, _db):
            pass

        async def get_conversation_by_thread_id(self, thread_id: str):
            if thread_id == child_thread_id:
                return SimpleNamespace(id=20, uid="user-1", agent_id="worker", status="subagent")
            return None

        async def get_conversation_by_id(self, conversation_id: int):
            assert conversation_id == 11
            return SimpleNamespace(id=11, thread_id="parent-thread", uid="user-1", status="active")

    class AgentRepo:
        def __init__(self, _db):
            pass

        async def get_by_slug(self, slug: str):
            assert slug == "worker"
            return SimpleNamespace(
                backend_id="SubAgentBackend",
                config_json={"context": {}},
            )

    class ThreadRepo:
        def __init__(self, _db):
            pass

        async def get_by_child_conversation_for_user(self, child_conversation_id: int, uid: str):
            assert child_conversation_id == 20
            assert uid == "user-1"
            return SimpleNamespace(
                id=77,
                parent_conversation_id=11,
                child_conversation_id=20,
                child_thread_id=child_thread_id,
                subagent_slug="worker",
                to_dict=lambda: {
                    "id": 77,
                    "parent_conversation_id": 11,
                    "child_conversation_id": 20,
                    "child_thread_id": child_thread_id,
                    "subagent_slug": "worker",
                },
            )

    class RunRepo:
        def __init__(self, _db):
            pass

        async def get_latest_run_by_thread_for_user(self, thread_id: str, uid: str):
            assert thread_id == child_thread_id
            assert uid == "user-1"
            return SimpleNamespace(input_payload={"model_spec": "provider:run-model"})

        async def get_latest_subagent_run_by_thread_for_user(self, thread_id: str, uid: str):
            assert thread_id == child_thread_id
            assert uid == "user-1"
            return SimpleNamespace(
                id="child-run",
                conversation_thread_id=child_thread_id,
                agent_slug="worker",
                uid="user-1",
                status="running",
                created_by_run_id="parent-run",
                subagent_thread_relation_id=77,
                input_payload={
                    "runtime": {
                        "tool_call_id": "tool-1",
                        "subagent_name": "Worker",
                        "description": "do work",
                    },
                },
                error_message=None,
                created_at=None,
                finished_at=None,
                to_dict=lambda: {"created_at": "2026-06-21T01:00:00Z", "finished_at": None},
            )

    class Graph:
        async def aget_state(self, config):
            assert config["configurable"]["thread_id"] == child_thread_id
            return SimpleNamespace(
                values={
                    "messages": [HumanMessage(content="do work"), AIMessage(content="working")],
                    "artifacts": ["out.txt"],
                }
            )

    class Context:
        def __init__(self, *, thread_id="", uid=""):
            self.thread_id = thread_id
            self.uid = uid
            self.model = ""

        def update(self, data: dict):
            for key, value in data.items():
                setattr(self, key, value)

    class Agent:
        context_schema = Context

        async def get_graph(self, *, context):
            assert context.thread_id == child_thread_id
            assert context.uid == "user-1"
            assert context.model == "provider:run-model"
            return Graph()

    monkeypatch.setattr(svc, "ConversationRepository", ConvRepo)
    monkeypatch.setattr(svc, "AgentRepository", AgentRepo)
    monkeypatch.setattr(svc, "SubagentThreadRepository", ThreadRepo)
    monkeypatch.setattr(svc, "AgentRunRepository", RunRepo)
    monkeypatch.setattr(svc, "normalize_agent_context_config", _fake_normalize_agent_context_config)
    monkeypatch.setattr(svc.agent_manager, "get_agent", lambda backend_id: Agent())

    result = await svc.get_agent_state_view(
        thread_id=child_thread_id,
        current_user=SimpleNamespace(uid="user-1"),
        db=object(),
        include_messages=True,
    )

    assert result["parent_thread_id"] == "parent-thread"
    assert result["subagent_thread"]["id"] == 77
    assert result["subagent_run"]["run_id"] == "child-run"
    assert result["agent_state"]["artifacts"] == ["out.txt"]
    assert [message["type"] for message in result["messages"]] == ["human", "ai"]


@pytest.mark.asyncio
async def test_get_agent_state_view_reports_malformed_subagent_run_as_server_error(
    monkeypatch: pytest.MonkeyPatch,
):
    child_thread_id = "child-thread"

    class ConvRepo:
        def __init__(self, _db):
            pass

        async def get_conversation_by_thread_id(self, thread_id: str):
            assert thread_id == child_thread_id
            return SimpleNamespace(id=20, uid="user-1", agent_id="worker", status="subagent")

        async def get_conversation_by_id(self, conversation_id: int):
            assert conversation_id == 11
            return SimpleNamespace(id=11, thread_id="parent-thread", uid="user-1", status="active")

    class AgentRepo:
        def __init__(self, _db):
            pass

        async def get_by_slug(self, slug: str):
            assert slug == "worker"
            return SimpleNamespace(backend_id="SubAgentBackend", config_json={"context": {}})

    class ThreadRepo:
        def __init__(self, _db):
            pass

        async def get_by_child_conversation_for_user(self, child_conversation_id: int, uid: str):
            assert child_conversation_id == 20
            assert uid == "user-1"
            return SimpleNamespace(
                id=77,
                parent_conversation_id=11,
                to_dict=lambda: {"id": 77},
            )

    class RunRepo:
        def __init__(self, _db):
            pass

        async def get_latest_run_by_thread_for_user(self, thread_id: str, uid: str):
            assert thread_id == child_thread_id
            assert uid == "user-1"
            return None

        async def get_latest_subagent_run_by_thread_for_user(self, thread_id: str, uid: str):
            assert thread_id == child_thread_id
            assert uid == "user-1"
            return SimpleNamespace(
                id="child-run",
                conversation_thread_id=child_thread_id,
                agent_slug="worker",
                status="running",
                input_payload={"runtime": {}},
            )

    class Graph:
        async def aget_state(self, _config):
            return SimpleNamespace(values={})

    class Context:
        def __init__(self, *, thread_id="", uid=""):
            self.thread_id = thread_id
            self.uid = uid

        def update(self, data: dict):
            for key, value in data.items():
                setattr(self, key, value)

    class Agent:
        context_schema = Context

        async def get_graph(self, *, context):
            assert context.thread_id == child_thread_id
            assert context.uid == "user-1"
            return Graph()

    monkeypatch.setattr(svc, "ConversationRepository", ConvRepo)
    monkeypatch.setattr(svc, "AgentRepository", AgentRepo)
    monkeypatch.setattr(svc, "SubagentThreadRepository", ThreadRepo)
    monkeypatch.setattr(svc, "AgentRunRepository", RunRepo)
    monkeypatch.setattr(svc, "normalize_agent_context_config", _fake_normalize_agent_context_config)
    monkeypatch.setattr(svc.agent_manager, "get_agent", lambda _backend_id: Agent())

    with pytest.raises(HTTPException) as exc:
        await svc.get_agent_state_view(
            thread_id=child_thread_id,
            current_user=SimpleNamespace(uid="user-1"),
            db=object(),
        )

    assert exc.value.status_code == 500
    assert exc.value.detail == "子智能体运行记录格式异常"


@pytest.mark.asyncio
async def test_build_agent_input_context_keeps_prompt_when_workspace_agent_context_empty(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(agent_context, "_load_workspace_agent_context", _empty_agent_context)

    context = await agent_context.build_agent_input_context(
        {"system_prompt": "原始系统提示词"},
        thread_id="thread-1",
        uid="user-1",
    )

    assert context["system_prompt"] == "原始系统提示词"


def _save_fake_agent(messages):
    class FakeGraph:
        async def aget_state(self, _config):
            return SimpleNamespace(values={"messages": messages})

    class FakeAgent:
        async def get_graph(self, *, context):
            return FakeGraph()

    return FakeAgent()


async def _no_gap(**kwargs):
    return None


@pytest.mark.asyncio
async def test_save_messages_revokes_zero_evidence_hard_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    """决策②：业务内问题零检索硬答（epoll 场景）在落库时改写为 knowledge_refusal 并转人工。"""
    monkeypatch.setattr(svc, "record_knowledge_gap", _no_gap)

    conv_repo = _FakeConvRepo(None)
    await svc.save_messages_from_langgraph_state(
        agent_instance=_save_fake_agent(
            [
                {"type": "human", "content": "介绍一下linux的epoll"},
                {"type": "ai", "content": "Linux 的 epoll 是一种 IO 事件通知机制……（通用知识）"},
            ]
        ),
        thread_id="thread-1",
        conv_repo=conv_repo,
        config_dict={"configurable": {"thread_id": "thread-1", "uid": "user-1"}},
        context=object(),
    )

    meta = conv_repo.saved_messages[0]["extra_metadata"]
    disposition = meta["knowledge_disposition"]
    assert disposition["type"] == "knowledge_refusal"
    assert disposition["reason"] == "no_evidence_output"
    assert disposition["domain"] == "unknown"
    assert meta["knowledge_no_evidence"] is True
    assert meta["handoff_available"] is True


@pytest.mark.asyncio
async def test_save_messages_keeps_answer_when_grounded_by_legit_tool() -> None:
    """文件等合法非 KB 来源的作答不被 ② 误伤。"""
    conv_repo = _FakeConvRepo(None)
    await svc.save_messages_from_langgraph_state(
        agent_instance=_save_fake_agent(
            [
                {"type": "human", "content": "根据上传的文档回答"},
                {"type": "tool", "name": "read_file", "content": "/attachments/a.md"},
                {"type": "ai", "content": "文档结论：该终端支持 CAT1。"},
            ]
        ),
        thread_id="thread-1",
        conv_repo=conv_repo,
        config_dict={"configurable": {"thread_id": "thread-1", "uid": "user-1"}},
        context=object(),
    )

    meta = conv_repo.saved_messages[0]["extra_metadata"]
    assert meta["knowledge_disposition"]["type"] == "answered"
    assert "knowledge_no_evidence" not in meta
    assert "handoff_available" not in meta


@pytest.mark.asyncio
async def test_save_messages_exempts_continuation_after_evidence_answer() -> None:
    """紧邻上一条带 ok 证据回答的续答轮（“那这个参数呢”）不被 ② 改写。"""
    conv_repo = _FakeConvRepo(None)

    async def _prev_evidence(_thread_id: str, limit: int | None = None):
        return [
            SimpleNamespace(
                role="assistant",
                extra_metadata={
                    "knowledge_evidence": {
                        "schema_version": 1,
                        "kb_scope": ["kb_a"],
                        "queries": [{"kb_id": "kb_a", "status": "ok", "reason": None, "result_count": 3}],
                    }
                },
            )
        ]

    conv_repo.get_messages_by_thread_id = _prev_evidence

    await svc.save_messages_from_langgraph_state(
        agent_instance=_save_fake_agent(
            [{"type": "human", "content": "那它的工作频率呢？"}, {"type": "ai", "content": "工作频率为 450MHz。"}]
        ),
        thread_id="thread-1",
        conv_repo=conv_repo,
        config_dict={"configurable": {"thread_id": "thread-1", "uid": "user-1"}},
        context=object(),
    )

    meta = conv_repo.saved_messages[0]["extra_metadata"]
    assert meta["knowledge_disposition"]["type"] == "answered"
    assert "knowledge_no_evidence" not in meta


def test_assistant_meta_is_answer_treats_refusals_as_unanswered() -> None:
    scope_refusal = {"knowledge_disposition": {"type": "scope_refusal", "reason": "off_topic"}}
    answered = {"knowledge_disposition": {"type": "answered"}}
    assert svc._assistant_meta_is_answer(scope_refusal) is False
    assert svc._assistant_meta_is_answer({"handoff_available": True}) is False
    assert svc._assistant_meta_is_answer({"is_error": True}) is False
    assert svc._assistant_meta_is_answer(answered) is True
    assert svc._assistant_meta_is_answer(None) is True


def test_requires_knowledge_preflight_skips_greeting_and_identity_but_keeps_business() -> None:
    # 问候 / 自我介绍类不走知识库预检与入口门，直接交主模型（身份由提示词回 IDENTITY_REPLY）
    for query in ["你是谁", "你能做什么", "介绍一下你自己", "who are you", "你好", "在吗"]:
        assert svc._requires_knowledge_preflight("ChatbotAgent", query) is False, query
    # 业务外/业务内实质问题仍走预检（由入口门或检索判定）
    for query in ["介绍一下linux的epoll", "MCX和PoC有什么区别？", "我们公司调度台怎么开通权限"]:
        assert svc._requires_knowledge_preflight("ChatbotAgent", query) is True, query
    # 非 ChatbotAgent 不触发
    assert svc._requires_knowledge_preflight("OtherAgent", "介绍一下linux的epoll") is False


def test_main_chat_read_scope_injected_for_main_and_resume_not_subagent() -> None:
    """bug3：主对话（chat/resume）注入受限读根；subagent 子流保持全量（不注入）。"""
    from yuxi.agents.buildin.chatbot.context import ChatBotContext

    class _StubAgent:
        context_schema = ChatBotContext

    main_context = svc._build_agent_context(_StubAgent(), {"thread_id": "t1", "uid": "u1"})
    svc._apply_main_chat_read_scope(main_context, run_type="chat")
    assert getattr(main_context, "fs_read_roots", None) == svc._MAIN_CHAT_READ_ROOTS

    resume_context = svc._build_agent_context(_StubAgent(), {"thread_id": "t1", "uid": "u1"})
    svc._apply_main_chat_read_scope(resume_context, run_type="resume")
    assert getattr(resume_context, "fs_read_roots", None) == svc._MAIN_CHAT_READ_ROOTS

    subagent_context = svc._build_agent_context(_StubAgent(), {"thread_id": "t1", "uid": "u1"})
    svc._apply_main_chat_read_scope(subagent_context, run_type="subagent")
    assert not hasattr(subagent_context, "fs_read_roots")


def test_main_chat_read_roots_exclude_shared_workspace() -> None:
    """bug3：受限读根只含本线程 uploads/outputs + skills，不含共享 workspace。"""
    from yuxi.utils.paths import VIRTUAL_PATH_OUTPUTS, VIRTUAL_PATH_UPLOADS, VIRTUAL_SKILLS_PATH

    roots = svc._MAIN_CHAT_READ_ROOTS
    assert roots == (VIRTUAL_PATH_UPLOADS, VIRTUAL_PATH_OUTPUTS, VIRTUAL_SKILLS_PATH)
    workspace_prefix = str(VIRTUAL_PATH_UPLOADS).removesuffix("uploads") + "workspace"
    assert workspace_prefix not in roots
    assert any(root != workspace_prefix for root in roots)


def test_industry_solution_skill_gate_strips_for_plain_main_chat_only() -> None:
    """bug1（验收③）：普通主对话（chat/resume，无行业结构化标记）摘除 industry-solution，
    防止模型自激活 Word 方案；显式行业请求与 subagent 子流保留。"""
    from yuxi.agents.buildin.chatbot.context import ChatBotContext

    class _StubAgent:
        context_schema = ChatBotContext

    def _context_with_skills():
        return svc._build_agent_context(
            _StubAgent(),
            {"thread_id": "t1", "uid": "u1", "skills": ["industry-solution", "knowledge-base"]},
        )

    plain_chat = _context_with_skills()
    svc._apply_industry_solution_skill_gate(plain_chat, run_type="chat", industry_enabled=False)
    assert plain_chat.skills == ["knowledge-base"]

    plain_resume = _context_with_skills()
    svc._apply_industry_solution_skill_gate(plain_resume, run_type="resume", industry_enabled=False)
    assert plain_resume.skills == ["knowledge-base"]

    industry_chat = _context_with_skills()
    svc._apply_industry_solution_skill_gate(industry_chat, run_type="chat", industry_enabled=True)
    assert industry_chat.skills == ["industry-solution", "knowledge-base"]

    subagent = _context_with_skills()
    svc._apply_industry_solution_skill_gate(subagent, run_type="subagent", industry_enabled=False)
    assert subagent.skills == ["industry-solution", "knowledge-base"]


def test_industry_solution_skill_gate_grants_on_structured_run_when_not_configured() -> None:
    """行业方案模式（结构化 industry_solution 请求）在 agent 未声明该技能时也应放行：
    门禁把 industry-solution 补进 context.skills，get_graph 的 prepare 重算后才会挂载；
    否则该技能依赖 agent 预配置，行业方案模式在未配置的 agent 上静默失效。"""
    from yuxi.agents.buildin.chatbot.context import ChatBotContext

    class _StubAgent:
        context_schema = ChatBotContext

    def _context_with_skills(skills):
        return svc._build_agent_context(_StubAgent(), {"thread_id": "t1", "uid": "u1", "skills": skills})

    unconfigured_chat = _context_with_skills(["knowledge-base"])
    svc._apply_industry_solution_skill_gate(unconfigured_chat, run_type="chat", industry_enabled=True)
    assert unconfigured_chat.skills == ["knowledge-base", "industry-solution"]

    unconfigured_resume = _context_with_skills(["knowledge-base"])
    svc._apply_industry_solution_skill_gate(unconfigured_resume, run_type="resume", industry_enabled=True)
    assert unconfigured_resume.skills == ["knowledge-base", "industry-solution"]

    empty_context = _context_with_skills([])
    svc._apply_industry_solution_skill_gate(empty_context, run_type="chat", industry_enabled=True)
    assert empty_context.skills == ["industry-solution"]

    already_present = _context_with_skills(["industry-solution", "knowledge-base"])
    svc._apply_industry_solution_skill_gate(already_present, run_type="chat", industry_enabled=True)
    assert already_present.skills == ["industry-solution", "knowledge-base"]

    no_grant_without_marker = _context_with_skills(["knowledge-base"])
    svc._apply_industry_solution_skill_gate(no_grant_without_marker, run_type="chat", industry_enabled=False)
    assert no_grant_without_marker.skills == ["knowledge-base"]


def test_industry_solution_skill_gate_must_target_input_context_dict() -> None:
    """回归（bug2）：门禁必须作用在传给流式执行的 input_context dict 上。

    运行期工具绑定由 input_context 重建 context（agents/base.py:_stream_input_with_state
    → get_graph → prepare_agent_runtime_context），只改 dataclass context 会在重建时丢失，
    导致 industry-solution 技能与其检索工具（research_industry_products）挂载不上——
    此前 default-chatbot（config 声明 skills=['knowledge-base']）行业方案 run 的沙箱
    context 实际只有 knowledge-base，模型只能降级手工拼引用、最终被正文校验拒绝。
    """
    from yuxi.agents.buildin.chatbot.context import ChatBotContext

    input_context = {"thread_id": "t1", "uid": "u1", "skills": ["knowledge-base"]}
    svc._apply_industry_solution_skill_gate(input_context, run_type="chat", industry_enabled=True)
    assert input_context["skills"] == ["knowledge-base", "industry-solution"]

    # base.py 重建逻辑：从 input_context 重造 context，门禁结果必须能透传进 prepare。
    rebuilt = ChatBotContext()
    rebuilt.update_from_dict(input_context)
    assert rebuilt.skills == ["knowledge-base", "industry-solution"]

    resume_context = {"thread_id": "t1", "uid": "u1", "skills": ["knowledge-base"]}
    svc._apply_industry_solution_skill_gate(resume_context, run_type="resume", industry_enabled=True)
    assert resume_context["skills"] == ["knowledge-base", "industry-solution"]

    # 普通主对话（无结构化标记）在 dict 上同样摘除，防止广告自激活。
    plain_dict = {"thread_id": "t1", "uid": "u1", "skills": ["industry-solution", "knowledge-base"]}
    svc._apply_industry_solution_skill_gate(plain_dict, run_type="chat", industry_enabled=False)
    assert plain_dict["skills"] == ["knowledge-base"]

    # subagent 子流不受上层门禁影响。
    subagent_dict = {"thread_id": "t1", "uid": "u1", "skills": ["industry-solution", "knowledge-base"]}
    svc._apply_industry_solution_skill_gate(subagent_dict, run_type="subagent", industry_enabled=False)
    assert subagent_dict["skills"] == ["industry-solution", "knowledge-base"]
