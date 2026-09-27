"""Tests for the repeated empty knowledge-search early stop middleware."""

from types import SimpleNamespace

import pytest
from langchain.agents import create_agent
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool

from yuxi.agents.middlewares.knowledge_refusal import KnowledgeRefusalMiddleware
from yuxi.services.knowledge_answer_disposition import classify_knowledge_disposition


class _AlwaysQueryKbsModel(BaseChatModel):
    """每轮都发起 query_kbs 的桩模型：不依赖真实模型即可复现「一直检索」的循环。"""

    calls: int = 0

    @property
    def _llm_type(self) -> str:
        return "stub"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        self.calls += 1
        message = AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "query_kbs",
                    "args": {"kb_ids": ["kb1"], "query_text": "电子围栏"},
                    "id": f"call-{self.calls}",
                }
            ],
        )
        return ChatResult(generations=[ChatGeneration(message=message)])


def _request(name: str, state: dict | None = None):
    return SimpleNamespace(
        tool_call={"name": name, "id": "call-1"},
        state=state or {},
    )


def _empty_result():
    return ToolMessage(content='{"status":"insufficient","reason":"no_results"}', tool_call_id="call-1")


@pytest.mark.asyncio
async def test_second_consecutive_empty_search_sets_refusal_state():
    middleware = KnowledgeRefusalMiddleware()
    state = {}

    async def handler(_request):
        return _empty_result()

    first = await middleware.awrap_tool_call(_request("query_kbs", state), handler)
    state.update(first.update)
    assert state["knowledge_empty_searches"] == 1
    assert "knowledge_refusal_pending" not in state

    second = await middleware.awrap_tool_call(_request("query_kb", state), handler)
    state.update(second.update)
    assert state["knowledge_empty_searches"] == 2
    assert state["knowledge_refusal_pending"] is True
    assert second.update["messages"] == [_empty_result()]


@pytest.mark.asyncio
async def test_success_resets_consecutive_empty_searches():
    middleware = KnowledgeRefusalMiddleware()
    state = {"knowledge_empty_searches": 1}

    async def handler(_request):
        return ToolMessage(content='{"status":"ok","results":[{"content":"依据"}]}', tool_call_id="call-1")

    result = await middleware.awrap_tool_call(_request("query_kbs", state), handler)
    assert result.update["knowledge_empty_searches"] == 0
    assert "knowledge_refusal_pending" not in result.update


@pytest.mark.asyncio
async def test_other_results_and_tools_do_not_trigger_refusal():
    middleware = KnowledgeRefusalMiddleware()
    state = {"knowledge_empty_searches": 1}

    async def error_handler(_request):
        return ToolMessage(content='{"status":"error","reason":"retrieval_error"}', tool_call_id="call-1")

    result = await middleware.awrap_tool_call(_request("query_kbs", state), error_handler)
    assert result.update["knowledge_empty_searches"] == 0

    async def empty_handler(_request):
        return _empty_result()

    result = await middleware.awrap_tool_call(_request("read_file", state), empty_handler)
    assert result == _empty_result()


@pytest.mark.asyncio
async def test_pending_state_short_circuits_model_with_refusal():
    middleware = KnowledgeRefusalMiddleware()
    request = SimpleNamespace(state={"knowledge_refusal_pending": True})
    called = False

    async def handler(_request):
        nonlocal called
        called = True
        return AIMessage(content="模型回答")

    result = await middleware.awrap_model_call(request, handler)
    assert called is False
    assert isinstance(result, AIMessage)
    # 终答正文必须能被落库侧的既有分类器判为知识库拒答（两模块的口径契约）
    assert classify_knowledge_disposition(str(result.content), None)["type"] == "knowledge_refusal"


@pytest.mark.asyncio
async def test_agent_loop_stops_with_refusal_after_two_empty_searches():
    """真实 create_agent 循环：第二次空检索后必须以拒答终答收尾，而不是继续检索或触顶。"""

    @tool
    async def query_kbs(kb_ids: list[str], query_text: str) -> dict:
        """检索知识库。"""
        return {"status": "insufficient", "reason": "no_results", "kb_id": ""}

    model = _AlwaysQueryKbsModel()
    graph = create_agent(
        model=model,
        tools=[query_kbs],
        system_prompt="test",
        middleware=[KnowledgeRefusalMiddleware()],
    )

    state = await graph.ainvoke(
        {"messages": [{"role": "user", "content": "电子围栏最多可以创建多少个？"}]},
        config={"recursion_limit": 50},
    )

    messages = state["messages"]
    final = messages[-1]
    assert isinstance(final, AIMessage)
    assert final.tool_calls == []  # 终答不再发起工具调用，循环正常结束
    assert final.content
    assert state["knowledge_empty_searches"] == 2
    assert state["knowledge_refusal_pending"] is True
    assert model.calls == 2  # 第 3 次模型调用被 middleware 短路，未真正打模型
    assert len([m for m in messages if isinstance(m, ToolMessage)]) == 2
