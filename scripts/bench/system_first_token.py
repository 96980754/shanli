"""系统首 token 实测：驱动生产 worker 路径 stream_agent_chat，量化「请求进入系统 → AI 首次输出」。

与端到端首 token 的区别：
  端到端 = HTTP 登录/建会话/发问 + ARQ 排队 + worker 执行 + SSE 回传 + 浏览器渲染；
  本脚本 = 直接复刻 run_worker 调用 stream_agent_chat 的路径，从函数入口开始计时，
  不含 HTTP、鉴权、队列等待与渲染开销——测得的是系统侧真实处理延迟。

三个口径（同一次运行内同时采集）：
  first_model : 首条模型 token 事件（astream messages 首 chunk）=「系统 AI 开始输出/思考」的严格时刻
  first_tool  : 首个工具执行事件（stream_event method=tools / tool-started）=「AI 开始调用工具」
  first       : 外层 chunk 流中首个可见 AI 输出（message_delta 的 reasoning/content 或 tool_call_delta）
                兜底口径，与 first_model/first_tool 冗余对照

用法（api-dev 容器内）:
  docker exec -w /app api-dev uv run --no-sync python -u /tmp/system_first_token.py \
      --agent-slug default-chatbot --query "..." --rounds 5 [--uid admin]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import time
import uuid
from collections import Counter

from sqlalchemy import select

import yuxi.agents.base as agents_base
from yuxi.repositories.agent_run_repository import AgentRunRepository
from yuxi.services.chat_service import stream_agent_chat
from yuxi.services.input_message_service import build_chat_input_message
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import User

DEFAULT_QUERY = "POCSTARS MDM 平台有哪些核心功能？请结合知识库说明。"

# --- 全局捕获状态：在 _stream_input_with_state 层打点，测量严格意义的首 token ---
_STATE = {"t0": 0.0, "first_model": None, "first_tool": None}
_ORIG_STREAM = agents_base.BaseAgent._stream_input_with_state


async def _timed_stream(self, graph_input, input_context=None, **kwargs):
    async for event in _ORIG_STREAM(self, graph_input, input_context, **kwargs):
        now = time.perf_counter() - _STATE["t0"]
        if event[0] == "messages" and _STATE["first_model"] is None:
            _STATE["first_model"] = now
        elif event[0] == "stream_event":
            payload = event[1]
            if (
                isinstance(payload, dict)
                and payload.get("method") == "tools"
                and _STATE["first_tool"] is None
            ):
                _STATE["first_tool"] = now
        yield event


agents_base.BaseAgent._stream_input_with_state = _timed_stream


def _first_kind_of(chunk: dict) -> str | None:
    """外层 chunk 流中首个可见 AI 输出的类型（无则 None）。"""
    stream_event = chunk.get("stream_event")
    if isinstance(stream_event, dict):
        etype = stream_event.get("type")
        if etype == "message_delta":
            if stream_event.get("reasoning_content"):
                return "reasoning"
            if stream_event.get("additional_reasoning_content"):
                return "additional_reasoning"
            if stream_event.get("content"):
                return "answer"
        elif etype == "tool_call_delta":
            return "tool_call_delta"
    return None


async def _load_user(db, uid: str) -> User | None:
    result = await db.execute(select(User).where(User.uid == uid, User.is_deleted == 0))
    return result.scalar_one_or_none()


async def _run_once(agent_slug: str, query: str, uid: str) -> dict:
    thread_id = str(uuid.uuid4())
    run_id = str(uuid.uuid4())
    request_id = str(uuid.uuid4())

    timing = {"init": None, "first": None, "end": None, "first_kind": None}
    tool_calls: list[str] = []
    first_payload: str = ""

    async with pg_manager.get_async_session_context() as db:
        user = await _load_user(db, uid)
        if not user:
            return {"error": f"user {uid} not found"}

        await AgentRunRepository(db).create_run(
            run_id=run_id,
            conversation_thread_id=thread_id,
            agent_slug=agent_slug,
            uid=uid,
            request_id=request_id,
            input_payload={"query": query},
        )

        meta = {
            "run_id": run_id,
            "request_id": request_id,
            "agent_slug": agent_slug,
            "thread_id": thread_id,
            "uid": user.uid,
            "has_image": False,
            "run_type": "chat",
        }
        input_message = build_chat_input_message(query)

        _STATE["t0"] = time.perf_counter()
        _STATE["first_model"] = None
        _STATE["first_tool"] = None
        stream = stream_agent_chat(
            agent_slug=agent_slug,
            thread_id=thread_id,
            meta=meta,
            input_message=input_message,
            current_user=user,
            db=db,
            save_user_message=False,
        )
        async for chunk_bytes in stream:
            now = time.perf_counter() - _STATE["t0"]
            try:
                chunk = json.loads(chunk_bytes.decode("utf-8"))
            except json.JSONDecodeError:
                continue
            status = chunk.get("status")
            if status == "init" and timing["init"] is None:
                timing["init"] = now
                continue
            kind = _first_kind_of(chunk)
            if kind and timing["first"] is None:
                timing["first"] = now
                timing["first_kind"] = kind
                se = chunk.get("stream_event") or {}
                if kind in {"reasoning", "additional_reasoning"}:
                    first_payload = (se.get("reasoning_content") or se.get("additional_reasoning_content") or "")[:60]
                elif kind == "answer":
                    first_payload = (se.get("content") or "")[:60]
                else:
                    first_payload = (se.get("name") or "")[:60]
            se = chunk.get("stream_event")
            if isinstance(se, dict) and se.get("type") == "tool_call_delta":
                name = se.get("name")
                if name and name not in tool_calls:
                    tool_calls.append(name)
            event = chunk.get("event")
            if isinstance(event, dict) and event.get("method") == "tools":
                data = event.get("data") or {}
                name = data.get("tool_name") if isinstance(data, dict) else None
                if name and name not in tool_calls:
                    tool_calls.append(name)
            if status == "finished" and timing["end"] is None:
                timing["end"] = now

    return {
        **timing,
        "first_model": _STATE["first_model"],
        "first_tool": _STATE["first_tool"],
        "tool_calls": tool_calls,
        "first_payload": first_payload,
    }


async def main() -> None:
    parser = argparse.ArgumentParser(description="系统首 token 实测（生产 worker 路径）")
    parser.add_argument("--agent-slug", default="default-chatbot")
    parser.add_argument("--query", default=DEFAULT_QUERY)
    parser.add_argument("--rounds", type=int, default=5)
    parser.add_argument("--uid", default="admin", help="被测账号 uid")
    args = parser.parse_args()

    print(f"=== 系统首 token · {args.agent_slug} × {args.rounds} 轮 ===", flush=True)
    print(f"query: {args.query[:60]!r}", flush=True)
    results = []
    for i in range(1, args.rounds + 1):
        r = await _run_once(args.agent_slug, args.query, args.uid)
        if r.get("error"):
            print(f"  r{i}: ERROR {r['error']}", flush=True)
            continue
        fmt = lambda ms: f"{ms*1000:.0f}" if ms is not None else "-"
        kind = r["first_kind"] or "-"
        print(
            f"  r{i}: init={fmt(r['init'])}ms model={fmt(r['first_model'])}ms tool={fmt(r['first_tool'])}ms "
            f"outer={fmt(r['first'])}ms({kind}) end={fmt(r['end'])}ms tools={r['tool_calls']} "
            f"first_out={r['first_payload']!r}",
            flush=True,
        )
        results.append(r)

    if not results:
        return
    print("\n=== 汇总（系统侧，不含 HTTP/排队/渲染） ===", flush=True)

    def _stat(name, key, label):
        vals = [r[key] * 1000 for r in results if r[key] is not None]
        if not vals:
            print(f"  {name}: -", flush=True)
            return
        print(
            f"  {name}: min={min(vals):.0f} avg={statistics.mean(vals):.0f} max={max(vals):.0f} "
            f"p50={statistics.median(vals):.0f}ms ({len(vals)}轮)",
            flush=True,
        )

    _stat("init   ", "init", "到 init（agent 解析+上下文）")
    _stat("model  ", "first_model", "系统首 token（首模型 token=AI 开始输出/思考）")
    _stat("tool   ", "first_tool", "到首个工具执行")
    _stat("outer  ", "first", "外层首个可见 AI 输出")
    _stat("end    ", "end", "到 finished")
    print(f"  首token类型: {dict(Counter(r['first_kind'] for r in results))}", flush=True)
    with_tools = [r for r in results if r["tool_calls"]]
    if with_tools:
        print(f"  工具序列样例: {with_tools[0]['tool_calls']}", flush=True)
    # 摘要已打印，直接退出跳过 async 生成器终结（astream_events 在 shutdown_asyncgens 会挂起）
    os._exit(0)


if __name__ == "__main__":
    asyncio.run(main())
