"""端到端首 token 延迟实测：走真实产品链路（登录 → 建会话 → 发问 → SSE 流式），
量化用户从发送消息到看到首个回复字的时间，以及完整回答耗时与工具调用序列。

被测路径：
  1. POST /api/auth/token           登录
  2. POST /api/chat/thread          创建会话
  3. POST /api/agent/runs           创建运行（触发 ARQ worker 执行）
  4. GET  /api/agent/runs/{id}/events  SSE 流式消费事件

量化 default-chatbot 端到端首 token 延迟、完整回答耗时与工具调用序列。

用法（需可登录账号，密码通过参数传入，不回显）:
  python e2e_first_token.py --username <user> --password <pass> \
      --agent-slug default-chatbot \
      --query "POCSTARS MDM 平台有哪些核心功能？请结合知识库说明。"
"""
from __future__ import annotations

import argparse
import json
import sys
import time

import httpx

BASE = "http://localhost:5050"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--username", required=True)
    ap.add_argument("--password", required=True)
    ap.add_argument("--agent-slug", default="default-chatbot")
    ap.add_argument("--query", default="POCSTARS MDM 平台有哪些核心功能？请结合知识库说明。")
    ap.add_argument("--timeout", type=float, default=180.0)
    args = ap.parse_args()

    client = httpx.Client(base_url=BASE, timeout=httpx.Timeout(args.timeout, connect=10.0))

    # 1. login
    r = client.post("/api/auth/token", data={"username": args.username, "password": args.password})
    if r.status_code != 200:
        print(f"LOGIN_FAILED {r.status_code}: {r.text[:300]}", flush=True)
        return 1
    token = r.json().get("access_token")
    headers = {"Authorization": f"Bearer {token}"}
    me = client.get("/api/auth/me", headers=headers)
    uid = me.json().get("uid") if me.status_code == 200 else None
    print(f"LOGIN_OK uid={uid}", flush=True)

    # 2. create thread
    r = client.post("/api/chat/thread", json={"agent_id": args.agent_slug}, headers=headers)
    if r.status_code != 200:
        print(f"THREAD_FAILED {r.status_code}: {r.text[:300]}", flush=True)
        return 1
    thread_id = r.json().get("thread_id") or r.json().get("id")
    print(f"THREAD_OK thread_id={thread_id}", flush=True)

    # 3. start run
    t0 = time.monotonic()
    r = client.post(
        "/api/agent/runs",
        json={"query": args.query, "agent_slug": args.agent_slug, "thread_id": thread_id},
        headers=headers,
    )
    if r.status_code != 200:
        print(f"RUN_FAILED {r.status_code}: {r.text[:300]}", flush=True)
        return 1
    run_id = r.json().get("run_id")
    print(f"RUN_CREATED run_id={run_id}", flush=True)

    # 4. stream events
    t_first_token: float | None = None
    t_end: float | None = None
    tool_calls: list[str] = []
    seen_tool_ids = set()
    answer_parts: list[str] = []
    seen_custom = set()

    with client.stream("GET", f"/api/agent/runs/{run_id}/events?verbose=true", headers=headers) as resp:
        if resp.status_code != 200:
            print(f"STREAM_FAILED {resp.status_code}", flush=True)
            return 1
        sse_event = None
        data_lines: list[str] = []
        for raw in resp.iter_lines():
            if not raw:
                if data_lines:
                    try:
                        envelope = json.loads("\n".join(data_lines))
                    except json.JSONDecodeError:
                        envelope = {"_raw": "\n".join(data_lines)}
                    now = time.monotonic() - t0
                    et = envelope.get("event") if isinstance(envelope, dict) else sse_event
                    sse_event = None
                    data_lines = []
                    inner = envelope.get("payload") if isinstance(envelope, dict) and isinstance(envelope.get("payload"), dict) else {}

                    if et == "messages":
                        for chunk in inner.get("items") or []:
                            if not isinstance(chunk, dict):
                                continue
                            ste = chunk.get("stream_event") or {}
                            stype = ste.get("type")
                            if stype == "tool_call":
                                tid = ste.get("tool_call_id") or ""
                                name = ste.get("name") or "?"
                                if tid and tid not in seen_tool_ids:
                                    seen_tool_ids.add(tid)
                                    tool_calls.append(name)
                                    print(f"  TOOL_CALL {name} at {now:.2f}s", flush=True)
                            elif stype == "message_delta":
                                content = chunk.get("response") or ste.get("content") or ""
                                if content:
                                    if t_first_token is None:
                                        t_first_token = now
                                        print(f"FIRST_TOKEN at {now:.2f}s: {content[:80]!r}", flush=True)
                                    answer_parts.append(content)
                    elif et == "custom":
                        name = inner.get("name") or ""
                        if name and name not in seen_custom:
                            seen_custom.add(name)
                            print(f"EVENT custom {name} at {now:.2f}s", flush=True)
                    elif et == "end":
                        t_end = now
                        print(f"END at {now:.2f}s", flush=True)
                    elif et:
                        print(f"EVENT {et} at {now:.2f}s", flush=True)
                continue
            if raw.startswith("event:"):
                sse_event = raw[len("event:") :].strip()
            elif raw.startswith("data:"):
                data_lines.append(raw[len("data:") :].strip())

    print("\n=== 汇总 ===", flush=True)
    print(f"first_token_latency: {t_first_token:.2f}s" if t_first_token else "first_token_latency: N/A", flush=True)
    print(f"end_latency:         {t_end:.2f}s" if t_end else "end_latency: N/A", flush=True)
    print(f"tool_calls: {tool_calls}", flush=True)
    answer = "".join(answer_parts).strip()
    print(f"answer_len: {len(answer)}", flush=True)
    print(f"answer_head: {answer[:200]!r}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
