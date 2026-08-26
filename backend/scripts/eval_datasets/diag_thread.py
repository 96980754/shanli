#!/usr/bin/env python3
"""诊断：拉取单个 thread 的完整 history，打印每次 query_kb 检索的工具调用与返回片段摘要。"""

import json
import os
import sys

import httpx

BASE_URL = os.environ.get("BASE_URL", "http://localhost:5050")
THREAD_ID = os.environ["THREAD_ID"]
USER = os.environ.get("ADMIN_USER", "admin")
PASS = os.environ.get("ADMIN_PASS", "admin")
TOOL_NAMES = {"query_kb", "query_kbs"}


async def main() -> None:
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(
            f"{BASE_URL}/api/auth/token",
            data={"username": USER, "password": PASS},
        )
        r.raise_for_status()
        token = r.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        hr = await client.get(f"{BASE_URL}/api/chat/thread/{THREAD_ID}/history", headers=headers)
        hr.raise_for_status()
        hist = hr.json().get("history", [])
        print(f"history messages: {len(hist)}")

        for i, msg in enumerate(hist):
            role = msg.get("role")
            for tc in msg.get("tool_calls") or []:
                name = tc.get("name")
                status = tc.get("status")
                result = tc.get("tool_call_result") or {}
                content = result.get("content") or ""
                chunks = None
                if name in TOOL_NAMES and status == "success":
                    try:
                        payload = json.loads(content)
                        chunks = payload.get("results") if isinstance(payload, dict) else None
                    except Exception:
                        chunks = None
                n = len(chunks) if isinstance(chunks, list) else "?"
                print(f"[msg {i} role={role}] tool={name} status={status} chunks={n}")
                if isinstance(chunks, list):
                    for j, c in enumerate(chunks[:6]):
                        body = (c.get("content") or "")[:90].replace("\n", " ")
                        print(f"    [{j}] id={c.get('id') or c.get('chunk_id')} | {body}")
                else:
                    body = content[:300].replace(chr(10), " ")
                    print(f"    content: {body}")
            if msg.get("role") == "assistant" and not (msg.get("tool_calls") or []):
                txt = msg.get("content") or ""
                if isinstance(txt, list):
                    txt = "".join(b.get("text", "") for b in txt if isinstance(b, dict))
                if txt.strip():
                    print(f"[msg {i} role=assistant] final? {txt[:120].replace(chr(10), ' ')}...")


if __name__ == "__main__":
    import asyncio

    if "THREAD_ID" not in os.environ:
        print("need THREAD_ID env")
        sys.exit(2)
    asyncio.run(main())
