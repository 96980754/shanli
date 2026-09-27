"""Early-stop middleware for repeated empty knowledge-base searches."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, NotRequired

from langchain.agents.middleware.types import AgentMiddleware, AgentState, ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.types import Command

_QUERY_TOOLS = frozenset({"query_kb", "query_kbs"})
_EMPTY_RESULT_LIMIT = 2


class KnowledgeRefusalState(AgentState):
    """State fields owned by :class:`KnowledgeRefusalMiddleware`."""

    knowledge_empty_searches: NotRequired[int]
    knowledge_refusal_pending: NotRequired[bool]


def _tool_name(request: Any) -> str:
    return str((request.tool_call or {}).get("name") or "")


def _tool_payload(result: ToolMessage) -> Mapping[str, Any] | None:
    content = result.content
    if isinstance(content, Mapping):
        return content
    if not isinstance(content, str):
        return None
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, Mapping) else None


def _is_empty_search(result: ToolMessage) -> bool:
    payload = _tool_payload(result)
    return payload is not None and payload.get("status") == "insufficient" and payload.get("reason") == "no_results"


class KnowledgeRefusalMiddleware(AgentMiddleware[KnowledgeRefusalState]):
    """Stop after two consecutive query-tool no-result responses.

    The tool call still returns a normal ``ToolMessage``.  The following model
    call is short-circuited into a normal refusal, so the run completes instead
    of reaching the recursion limit or continuing into sandbox tools.
    """

    state_schema = KnowledgeRefusalState

    def wrap_tool_call(self, request, handler):
        result = handler(request)
        return self._record_search(request, result)

    async def awrap_tool_call(self, request, handler: Callable[..., Awaitable[ToolMessage]]):
        result = await handler(request)
        return self._record_search(request, result)

    def _record_search(self, request, result):
        if _tool_name(request) not in _QUERY_TOOLS or not isinstance(result, ToolMessage):
            return result

        searches = int(request.state.get("knowledge_empty_searches") or 0)
        if _is_empty_search(result):
            searches += 1
        else:
            searches = 0

        update: dict[str, Any] = {"knowledge_empty_searches": searches, "messages": [result]}
        if searches >= _EMPTY_RESULT_LIMIT:
            update["knowledge_refusal_pending"] = True

        return Command(update=update)

    def wrap_model_call(self, request: ModelRequest, handler) -> ModelResponse | AIMessage:
        if request.state.get("knowledge_refusal_pending"):
            return self._refusal_message()
        return handler(request)

    async def awrap_model_call(self, request: ModelRequest, handler) -> ModelResponse | AIMessage:
        if request.state.get("knowledge_refusal_pending"):
            return self._refusal_message()
        return await handler(request)

    @staticmethod
    def _refusal_message() -> AIMessage:
        """固定拒答终答：正文即 KNOWLEDGE_REFUSAL_REPLY，落库时由既有
        classify_knowledge_disposition 按同一文案前缀判为 knowledge_refusal。"""
        # 延后导入：chatbot 包的 __init__ 会反向 import 本包，模块级导入会成环。
        from yuxi.agents.buildin.chatbot.prompt import KNOWLEDGE_REFUSAL_REPLY

        return AIMessage(content=KNOWLEDGE_REFUSAL_REPLY)
