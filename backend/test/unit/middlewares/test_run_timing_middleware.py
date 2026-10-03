"""Run 级耗时/轮数采集中间件测试：计数、真实 token 汇总与 state 写入时序。"""

import json
from types import SimpleNamespace

import pytest
from langchain.agents import create_agent
from langchain.agents.middleware.types import ModelResponse
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool
from pydantic import Field

from yuxi.agents.middlewares.run_timing import RunTimingMiddleware


class _ScriptedModel(BaseChatModel):
    """按脚本依次返回消息的桩模型。"""

    scripts: list[AIMessage] = Field(default_factory=list)
    calls: int = 0

    @property
    def _llm_type(self) -> str:
        return "stub"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        index = min(self.calls, len(self.scripts) - 1)
        self.calls += 1
        return ChatResult(generations=[ChatGeneration(message=self.scripts[index])])


def _model_request() -> SimpleNamespace:
    return SimpleNamespace(state={"messages": []}, messages=[], system_message=None, tools=[])


def _ai_message(content="", *, usage=None) -> AIMessage:
    return AIMessage(content=content, usage_metadata=usage)


@pytest.mark.asyncio
async def test_model_call_counters_and_real_token_usage_accumulate():
    """两次模型调用的轮数/耗时累计，token 取 usage_metadata 的真实值（含峰值）。"""
    middleware = RunTimingMiddleware()
    request = _model_request()

    async def first(_request):
        return ModelResponse(
            result=[_ai_message(usage={"input_tokens": 100, "output_tokens": 10, "total_tokens": 110})]
        )

    async def second(_request):
        return ModelResponse(
            result=[_ai_message(usage={"input_tokens": 250, "output_tokens": 20, "total_tokens": 270})]
        )

    await middleware.awrap_model_call(request, first)
    result = await middleware.awrap_model_call(request, second)

    metrics = result.command.update["run_metrics"]
    assert metrics["model_calls"] == 2
    assert metrics["model_time_ms"] >= 0
    assert metrics["input_tokens_total"] == 350
    assert metrics["input_tokens_peak"] == 250
    assert metrics["output_tokens_total"] == 30
    assert metrics["retrieval_calls"] == 0


@pytest.mark.asyncio
async def test_query_tools_counted_other_tools_not():
    """query_kb/query_kbs 计入检索轮数与耗时，其它工具不计。"""
    middleware = RunTimingMiddleware()

    async def run_tool(name: str):
        request = SimpleNamespace(tool_call={"name": name, "args": {}})

        async def handler(_request):
            return SimpleNamespace(content="{}")

        await middleware.awrap_tool_call(request, handler)

    await run_tool("query_kbs")
    await run_tool("query_kb")
    await run_tool("read_file")

    assert middleware.retrieval_calls == 2
    assert middleware.retrieval_time_ms >= 0


@pytest.mark.asyncio
async def test_budget_note_absorbed_from_tool_result_and_accumulated():
    """检索 ToolMessage JSON 里的 `_budget` 计数被摘走（不进消息流）并跨调用累积进快照。"""
    from langchain_core.messages import ToolMessage

    middleware = RunTimingMiddleware()

    async def call_with_budget(name: str, delta: dict):
        request = SimpleNamespace(tool_call={"name": name, "args": {}})

        # ToolNode 在中间件内层已把 dict 序列化成 ToolMessage.content 的 JSON 串
        payload = {"status": "ok", "results": [{"content": "x"}], "_budget": delta}

        async def handler(_request):
            return ToolMessage(content=json.dumps(payload, ensure_ascii=False), tool_call_id="c1")

        result = await middleware.awrap_tool_call(request, handler)
        assert isinstance(result, ToolMessage)
        assert "_budget" not in result.content
        assert json.loads(result.content) == {"status": "ok", "results": [{"content": "x"}]}

    await call_with_budget(
        "query_kb",
        {
            "injection_char_limit": 30000,
            "injection_truncated_chunks": 1,
            "injection_dropped_chunks": 2,
            "injection_dedup_placeholders": 3,
        },
    )
    await call_with_budget(
        "query_kbs",
        {
            "injection_char_limit": 30000,
            "injection_truncated_chunks": 0,
            "injection_dropped_chunks": 1,
            "injection_dedup_placeholders": 2,
        },
    )

    assert middleware.retrieval_calls == 2
    snapshot = middleware._snapshot()
    assert snapshot["injection_char_limit"] == 30000
    assert snapshot["injection_truncated_chunks"] == 1
    assert snapshot["injection_dropped_chunks"] == 3
    assert snapshot["injection_dedup_placeholders"] == 5


@pytest.mark.asyncio
async def test_budget_absorbed_through_full_graph_with_patch_middleware():
    """整图 + PatchToolCallsMiddleware 在场时，`_budget` 仍被摘走并进 state 的 run_metrics。

    回归：线上验收发现计数不落库，排查方向是中间件层序/结果被变换——本测试钉住
    「经过完整 create_agent 链路后 injection_* 仍在 run_metrics 里」这一端到端行为。
    """
    from deepagents.middleware.patch_tool_calls import PatchToolCallsMiddleware

    model = _ScriptedModel(
        scripts=[
            AIMessage(
                content="",
                tool_calls=[{"name": "query_kb", "args": {"kb_id": "x", "query_text": "q"}, "id": "c1"}],
            ),
            AIMessage(content="答案", usage_metadata={"input_tokens": 50, "output_tokens": 5, "total_tokens": 55}),
        ]
    )

    @tool
    async def query_kb(kb_id: str, query_text: str) -> dict:
        """检索知识库。"""
        return {
            "status": "ok",
            "results": [{"content": "依据"}],
            "_budget": {
                "injection_char_limit": 30000,
                "injection_truncated_chunks": 1,
                "injection_dropped_chunks": 2,
                "injection_dedup_placeholders": 3,
            },
        }

    graph = create_agent(
        model=model,
        tools=[query_kb],
        system_prompt="test",
        middleware=[PatchToolCallsMiddleware(), RunTimingMiddleware()],
    )
    state = await graph.ainvoke(
        {"messages": [{"role": "user", "content": "电子围栏上限"}]},
        config={"recursion_limit": 50},
    )

    metrics = state.get("run_metrics") or {}
    assert metrics["retrieval_calls"] == 1
    assert metrics["injection_char_limit"] == 30000
    assert metrics["injection_truncated_chunks"] == 1
    assert metrics["injection_dropped_chunks"] == 2
    assert metrics["injection_dedup_placeholders"] == 3
    # `_budget` 键不进模型上下文（工具消息 content 里不残留）
    tool_messages = [m for m in state["messages"] if m.type == "tool"]
    assert tool_messages and all("_budget" not in m.content for m in tool_messages)


@pytest.mark.asyncio
async def test_run_metrics_written_to_state_through_graph():
    """整图执行后 state 持有 run_metrics；并行检索不触发并发 state 写入异常。"""
    model = _ScriptedModel(
        scripts=[
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "query_kbs", "args": {"kb_ids": ["kb1"], "query_text": "q"}, "id": "c1"},
                    {"name": "query_kbs", "args": {"kb_ids": ["kb2"], "query_text": "q"}, "id": "c2"},
                ],
            ),
            AIMessage(content="答案", usage_metadata={"input_tokens": 80, "output_tokens": 6, "total_tokens": 86}),
        ]
    )

    @tool
    async def query_kbs(kb_ids: list[str], query_text: str) -> dict:
        """检索知识库。"""
        return {"status": "ok", "results": [{"content": "依据"}]}

    graph = create_agent(model=model, tools=[query_kbs], system_prompt="test", middleware=[RunTimingMiddleware()])
    state = await graph.ainvoke(
        {"messages": [{"role": "user", "content": "电子围栏上限"}]},
        config={"recursion_limit": 50},
    )

    metrics = state["run_metrics"]
    assert model.calls == 2
    assert metrics["model_calls"] == 2
    assert metrics["retrieval_calls"] == 2  # 同一 step 内两次并行检索都计数
    assert metrics["input_tokens_peak"] == 80
