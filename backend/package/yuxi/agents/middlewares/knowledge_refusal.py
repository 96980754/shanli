"""Early-stop middleware for repeated empty knowledge-base searches."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage

# 知识检索工具集合：拒答早停与 run 级耗时统计（run_timing）共用的口径。
QUERY_TOOLS = frozenset({"query_kb", "query_kbs"})
_EMPTY_RESULT_LIMIT = 2


def _tool_payload(message: ToolMessage) -> Mapping[str, Any] | None:
    content = message.content
    if isinstance(content, Mapping):
        return content
    if not isinstance(content, str):
        return None
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, Mapping) else None


def _is_empty_search(message: ToolMessage) -> bool:
    payload = _tool_payload(message)
    return payload is not None and payload.get("status") == "insufficient" and payload.get("reason") == "no_results"


def _consecutive_empty_searches(messages: Sequence[BaseMessage]) -> int:
    """本轮提问内末尾连续的空检索次数。

    遇到非空检索结果（含检索错误）或新的用户提问即停止回溯；其它工具（上下文取证、
    沙箱等）不影响连续计数。
    """
    count = 0
    for message in reversed(messages):
        if isinstance(message, HumanMessage):
            break
        if isinstance(message, ToolMessage) and message.name in QUERY_TOOLS:
            if not _is_empty_search(message):
                break
            count += 1
    return count


def _refusal_message() -> AIMessage:
    """固定拒答终答：正文即 KNOWLEDGE_REFUSAL_REPLY，落库时由既有
    classify_knowledge_disposition 按同一文案前缀判为 knowledge_refusal。"""
    # 延后导入：chatbot 包的 __init__ 会反向 import 本包，模块级导入会成环。
    from yuxi.agents.buildin.chatbot.prompt import KNOWLEDGE_REFUSAL_REPLY

    return AIMessage(content=KNOWLEDGE_REFUSAL_REPLY)


class KnowledgeRefusalMiddleware(AgentMiddleware):
    """两次检索无结果后，用正常拒答终答结束本次 run。

    判断放在模型调用前，直接回溯消息历史，不在工具 hook 里写状态：模型可能在一个
    step 内并行发起多个 ``query_kb``/``query_kbs``，对同一 state key 的并发写入会被
    LangGraph 判为 InvalidUpdateError。回溯读取历史同样能覆盖并行调用。
    """

    def wrap_model_call(self, request: ModelRequest, handler) -> ModelResponse | AIMessage:
        if _consecutive_empty_searches(request.state.get("messages") or ()) >= _EMPTY_RESULT_LIMIT:
            return _refusal_message()
        return handler(request)

    async def awrap_model_call(self, request: ModelRequest, handler) -> ModelResponse | AIMessage:
        if _consecutive_empty_searches(request.state.get("messages") or ()) >= _EMPTY_RESULT_LIMIT:
            return _refusal_message()
        return await handler(request)
