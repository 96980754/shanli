"""Run 级耗时/轮数采集中间件：模型轮数、检索轮数、各自累计耗时与真实 token 汇总。

计数累积在中间件实例上（`_build_middlewares` 每次 run 重建实例），state 只在模型调用
包装器里整体覆盖写入 `run_metrics`——检索工具可能在一个 step 内并行执行，若在
`wrap_tool_call` 里并发写同一 state key 会被 LangGraph 判为 InvalidUpdateError，
因此工具侧只累加实例计数，不动 state（与 KnowledgeRefusalMiddleware 规避同一坑）。

检索注入预算的截断/丢弃/占位计数同理由本中间件累积：query_kb/query_kbs 把每次的
计数挂在工具结果 `_budget` 键上带出（chat 主路径会重建 context，工具与落库拿到的
不是同一实例，context 直读不通）。注意中间件在 `wrap_tool_call` 里拿到的不是工具
返回的 dict——ToolNode 最内层已把 dict 序列化成 `ToolMessage.content` 的 JSON 字符串，
`_budget` 在这段 JSON 里，因此从这里解析摘除并以同参数重新序列化（与 ToolNode 的
`json.dumps(..., ensure_ascii=False)` 一致），该键不进模型上下文也不落 tool 消息，
随 `_snapshot` 一起写进 run_metrics 落库。
"""

from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable
from typing import Any, NotRequired

from langchain.agents.middleware.types import (
    AgentMiddleware,
    AgentState,
    ExtendedModelResponse,
    ModelRequest,
    ModelResponse,
    ToolCallRequest,
)
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.types import Command

from yuxi.agents.middlewares.knowledge_refusal import QUERY_TOOLS


class RunMetricsState(AgentState):
    """Agent state extension with the cumulative run timing/metrics snapshot."""

    run_metrics: NotRequired[dict]


class RunTimingMiddleware(AgentMiddleware[RunMetricsState]):
    """为每次 run 采集耗时/轮数指标，供 chat_service 落库到 agent_runs.metrics。"""

    state_schema = RunMetricsState

    def __init__(self) -> None:
        super().__init__()
        self.model_calls = 0
        self.model_time_ms = 0.0
        self.retrieval_calls = 0
        self.retrieval_time_ms = 0.0
        self.input_tokens_total = 0
        self.input_tokens_peak = 0
        self.output_tokens_total = 0
        self.injection_char_limit: int | None = None
        self.injection_truncated_chunks = 0
        self.injection_dropped_chunks = 0
        self.injection_dedup_placeholders = 0

    def _snapshot(self) -> dict:
        snapshot = {
            "model_calls": self.model_calls,
            "model_time_ms": round(self.model_time_ms),
            "retrieval_calls": self.retrieval_calls,
            "retrieval_time_ms": round(self.retrieval_time_ms),
            "input_tokens_total": self.input_tokens_total,
            "input_tokens_peak": self.input_tokens_peak,
            "output_tokens_total": self.output_tokens_total,
        }
        if self.injection_char_limit is not None:
            snapshot["injection_char_limit"] = self.injection_char_limit
            snapshot["injection_truncated_chunks"] = self.injection_truncated_chunks
            snapshot["injection_dropped_chunks"] = self.injection_dropped_chunks
            snapshot["injection_dedup_placeholders"] = self.injection_dedup_placeholders
        return snapshot

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ExtendedModelResponse:
        start = time.perf_counter()
        response = handler(request)
        self._after_model_call(response, time.perf_counter() - start)
        return self._with_metrics_update(response)

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ExtendedModelResponse:
        start = time.perf_counter()
        response = await handler(request)
        self._after_model_call(response, time.perf_counter() - start)
        return self._with_metrics_update(response)

    def _with_metrics_update(self, response: ModelResponse) -> ExtendedModelResponse:
        return ExtendedModelResponse(
            model_response=response,
            command=Command(update={"run_metrics": self._snapshot()}),
        )

    def _after_model_call(self, response: ModelResponse, elapsed_seconds: float) -> None:
        self.model_calls += 1
        self.model_time_ms += elapsed_seconds * 1000
        for message in reversed(list(response.result or [])):
            if not isinstance(message, AIMessage):
                continue
            usage = message.usage_metadata or {}
            input_tokens = usage.get("input_tokens")
            output_tokens = usage.get("output_tokens")
            if isinstance(input_tokens, int):
                self.input_tokens_total += input_tokens
                self.input_tokens_peak = max(self.input_tokens_peak, input_tokens)
            if isinstance(output_tokens, int):
                self.output_tokens_total += output_tokens
            break

    def wrap_tool_call(self, request: ToolCallRequest, handler: Callable[[ToolCallRequest], Any]) -> Any:
        if request.tool_call["name"] not in QUERY_TOOLS:
            return handler(request)
        start = time.perf_counter()
        result = self._absorb_budget_note(handler(request))
        self._after_retrieval(time.perf_counter() - start)
        return result

    async def awrap_tool_call(
        self, request: ToolCallRequest, handler: Callable[[ToolCallRequest], Awaitable[Any]]
    ) -> Any:
        if request.tool_call["name"] not in QUERY_TOOLS:
            return await handler(request)
        start = time.perf_counter()
        result = self._absorb_budget_note(await handler(request))
        self._after_retrieval(time.perf_counter() - start)
        return result

    def _absorb_budget_note(self, result: Any) -> Any:
        """摘走 ToolMessage.content JSON 里的 `_budget` 计数并累积；该键不进模型上下文。"""
        if not isinstance(result, ToolMessage) or not isinstance(result.content, str):
            return result
        try:
            payload = json.loads(result.content)
        except json.JSONDecodeError:
            # 工具报错路径的 content 可能是纯文本错误信息，没有可摘的计数
            return result
        if not isinstance(payload, dict) or "_budget" not in payload:
            return result
        delta = payload.pop("_budget")
        if isinstance(delta, dict):
            limit = delta.get("injection_char_limit")
            if isinstance(limit, int):
                self.injection_char_limit = limit
            self.injection_truncated_chunks += int(delta.get("injection_truncated_chunks") or 0)
            self.injection_dropped_chunks += int(delta.get("injection_dropped_chunks") or 0)
            self.injection_dedup_placeholders += int(delta.get("injection_dedup_placeholders") or 0)
        return result.model_copy(update={"content": json.dumps(payload, ensure_ascii=False)})

    def _after_retrieval(self, elapsed_seconds: float) -> None:
        self.retrieval_calls += 1
        self.retrieval_time_ms += elapsed_seconds * 1000
