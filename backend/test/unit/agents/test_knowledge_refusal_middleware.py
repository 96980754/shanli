"""Tests for the repeated empty knowledge-search early stop middleware."""

import json

import pytest
from langchain.agents import create_agent
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool
from pydantic import Field

from yuxi.agents.buildin.chatbot.prompt import KNOWLEDGE_REFUSAL_REPLY
from yuxi.agents.middlewares.knowledge_refusal import KnowledgeRefusalMiddleware, _consecutive_empty_searches
from yuxi.services.knowledge_answer_disposition import classify_knowledge_disposition

EMPTY = {"status": "insufficient", "reason": "no_results", "kb_id": ""}
OK = {"status": "ok", "results": [{"content": "依据"}]}
ERROR = {"status": "error", "reason": "retrieval_error"}

QUESTION = "电子围栏最多可以创建多少个？"


class _ScriptedModel(BaseChatModel):
    """按脚本依次返回消息的桩模型，用于构造「一直检索」或「检索后作答」的轨迹。"""

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


def _call(*call_ids: str) -> AIMessage:
    """一条发起 query_kbs 的助手消息；多个 call_id 表示同一 step 内的并行调用。"""
    return AIMessage(
        content="",
        tool_calls=[
            {"name": "query_kbs", "args": {"kb_ids": ["kb1"], "query_text": "电子围栏"}, "id": call_id}
            for call_id in call_ids
        ],
    )


def _kb_tool(payloads: list[dict]):
    """按顺序返回预设检索结果的 query_kbs 桩工具，最后一次结果会重复使用。"""
    served = {"count": 0}

    @tool
    async def query_kbs(kb_ids: list[str], query_text: str) -> dict:
        """检索知识库。"""
        payload = payloads[min(served["count"], len(payloads) - 1)]
        served["count"] += 1
        return payload

    return query_kbs


async def _run(scripts: list[AIMessage], payloads: list[dict]):
    model = _ScriptedModel(scripts=scripts)
    graph = create_agent(
        model=model,
        tools=[_kb_tool(payloads)],
        system_prompt="test",
        middleware=[KnowledgeRefusalMiddleware()],
    )
    state = await graph.ainvoke(
        {"messages": [{"role": "user", "content": QUESTION}]},
        config={"recursion_limit": 50},
    )
    return model, state["messages"]


@pytest.mark.asyncio
async def test_two_consecutive_empty_searches_end_with_refusal():
    """两轮空检索后以拒答收尾，且不再真正打模型。"""
    model, messages = await _run([_call("c1"), _call("c2"), _call("c3")], [EMPTY])

    final = messages[-1]
    assert isinstance(final, AIMessage)
    assert final.tool_calls == []  # 终答不再发起工具调用，循环正常结束
    assert final.content == KNOWLEDGE_REFUSAL_REPLY
    # 终答正文必须能被落库侧的既有分类器判为知识库拒答（两模块的口径契约）
    assert classify_knowledge_disposition(str(final.content), None)["type"] == "knowledge_refusal"
    assert model.calls == 2  # 第 3 次模型调用被 middleware 短路，未真正打模型
    assert len([m for m in messages if isinstance(m, ToolMessage)]) == 2


@pytest.mark.asyncio
async def test_parallel_empty_searches_in_one_step_end_with_refusal():
    """同一 step 内并行空检索：既不能触发并发写入异常，也要计入两次检索预算。"""
    model, messages = await _run([_call("c1", "c2"), _call("c3")], [EMPTY])

    tool_messages = [m for m in messages if isinstance(m, ToolMessage)]
    assert len(tool_messages) == 2  # 两次并行检索都真实执行了
    assert messages[-1].content == KNOWLEDGE_REFUSAL_REPLY
    assert model.calls == 1


@pytest.mark.asyncio
async def test_successful_search_resets_the_budget():
    """中间出现成功检索后重新计数：第 4 次空检索才收尾。"""
    model, messages = await _run([_call("c1"), _call("c2"), _call("c3"), _call("c4"), _call("c5")], [EMPTY, OK, EMPTY])

    assert messages[-1].content == KNOWLEDGE_REFUSAL_REPLY
    assert model.calls == 4
    assert len([m for m in messages if isinstance(m, ToolMessage)]) == 4


@pytest.mark.asyncio
async def test_retrieval_error_is_not_disguised_as_refusal():
    """检索服务错误不计数、也不被伪装成拒答，模型仍能自行作答。"""
    model, messages = await _run([_call("c1"), _call("c2"), AIMessage(content="模型自己的回答")], [ERROR])

    assert messages[-1].content == "模型自己的回答"
    assert model.calls == 3


def test_new_question_and_other_tools_do_not_leak_the_count():
    """跨轮次的连续计数以用户提问为界，其它工具不打断连续检索。"""
    empty = ToolMessage(content=json.dumps(EMPTY), name="query_kbs", tool_call_id="c1")
    ok = ToolMessage(content=json.dumps(OK), name="query_kbs", tool_call_id="c2")
    other = ToolMessage(content="文件内容", name="read_file", tool_call_id="c3")

    assert _consecutive_empty_searches([HumanMessage(content=QUESTION), empty, other]) == 1
    assert _consecutive_empty_searches([HumanMessage(content=QUESTION), empty, ok, empty]) == 1
    # 上一轮的连续空检索不跨过新的用户提问
    assert _consecutive_empty_searches([HumanMessage(content=QUESTION), empty, HumanMessage(content="另一个问题")]) == 0
