#!/usr/bin/env python3
"""真实 Agent 端到端测试 runner（内部工具）。

逐题调用生产 Agent（POST /api/agent-invocation/eval/runs，阻塞式），采集系统答案与
实际读取到的正文证据（thread history tool_calls 中 query_kb/query_kbs/find_kb_document/
open_kb_document 的结果），落盘 JSONL 供后续评分与汇报报告使用。失败题记录 error 不中断。

用法（容器内）：
    docker exec api-dev python /app/scripts/run_agent_e2e.py \
        --testset /app/scripts/eval_datasets/synthetic/poc.jsonl \
        --username <登录账号> --password <密码>

账号密码也可通过环境变量 YUXI_TEST_USER / YUXI_TEST_PASSWORD 传入。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from pathlib import Path

import httpx

BASE_URL_DEFAULT = "http://localhost:5050"
DEFAULT_OUTPUT = "/app/scripts/eval_datasets/synthetic"
# 会返回正文证据的检索类工具：query_kb/query_kbs 返回命中片段（SearchOutputSchema.results），
# find_kb_document 返回命中上下文窗口（FindOutputSchema.windows），open_kb_document 返回整窗正文
# （OpenOutputSchema.content）。search_file 只返回文件元信息（无正文）、read_file 是沙箱通用
# 文件读取器（读线程工作区文件而非 KB 内容），二者不作为忠实度证据采集。
CONTENT_TOOLS = {"query_kb", "query_kbs", "find_kb_document", "open_kb_document"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="真实 Agent 端到端测试 runner")
    parser.add_argument("--testset", required=True, help="测试集 JSONL（每行 {query, gold_answer?, gold_chunk_ids?}）")
    parser.add_argument("--agent-slug", default="default-chatbot", help="要运行的 Agent slug")
    parser.add_argument("--base-url", default=BASE_URL_DEFAULT, help="API 基础地址")
    parser.add_argument("--username", help="登录账号（默认取环境变量 YUXI_TEST_USER）")
    parser.add_argument("--password", help="登录密码（默认取环境变量 YUXI_TEST_PASSWORD）")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="结果输出目录")
    parser.add_argument("--name", default="", help="结果文件名后缀（默认当日日期）")
    parser.add_argument("--concurrency", type=int, default=2, help="同时运行的 Agent 数（默认 2，避免超限）")
    parser.add_argument("--timeout", type=float, default=300.0, help="单题最长等待秒数（默认 300）")
    return parser.parse_args()


async def login(base_url: str, username: str, password: str, client: httpx.AsyncClient) -> str:
    resp = await client.post(
        f"{base_url}/api/auth/token",
        data={"username": username, "password": password},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    resp.raise_for_status()
    token = resp.json().get("access_token")
    if not token:
        raise RuntimeError(f"登录失败，响应中无 access_token: {resp.text[:200]}")
    return token


def _parse_tool_content(tool_name: str, content: str) -> list[dict]:
    """按工具类型解析 tool_call_result.content 为正文证据片段；无正文的工具返回空。"""
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, dict):
        return []
    if tool_name in {"query_kb", "query_kbs"}:
        results = payload.get("results")
        if not isinstance(results, list):
            return []
        return [c for c in results if isinstance(c, dict) and c.get("content")]
    if tool_name == "find_kb_document":
        # windows[].content 是带行号的命中窗口正文，是 Agent 实际看到的证据。
        file_id = payload.get("file_id") or ""
        kb_id = payload.get("kb_id") or ""
        chunks: list[dict] = []
        for w in payload.get("windows") or []:
            if not isinstance(w, dict):
                continue
            body = (w.get("content") or "").strip()
            if not body:
                continue
            chunks.append(
                {
                    "id": f"{file_id}:L{w.get('start_line', '?')}-{w.get('end_line', '?')}",
                    "content": body,
                    "kb_id": kb_id,
                    "file_id": file_id,
                    "tool": "find_kb_document",
                }
            )
        return chunks
    if tool_name == "open_kb_document":
        body = (payload.get("content") or "").strip()
        if not body:
            return []
        file_id = payload.get("file_id") or ""
        return [
            {
                "id": f"{file_id}:L{payload.get('start_line', '?')}-{payload.get('end_line', '?')}",
                "content": body,
                "kb_id": payload.get("kb_id") or "",
                "file_id": file_id,
                "tool": "open_kb_document",
            }
        ]
    return []


def extract_retrieved_chunks(history: dict) -> list[dict]:
    """从 thread history 的 tool_calls 提取 Agent 实际读取到的全部正文证据片段（去重）。"""
    seen: set[str] = set()
    chunks: list[dict] = []
    for msg in history.get("history", []):
        for tc in msg.get("tool_calls") or []:
            if tc.get("name") not in CONTENT_TOOLS or tc.get("status") != "success":
                continue
            result = tc.get("tool_call_result") or {}
            for chunk in _parse_tool_content(tc["name"], result.get("content") or ""):
                cid = str(chunk.get("id") or chunk.get("chunk_id") or "")
                if cid and cid not in seen:
                    seen.add(cid)
                    chunks.append(chunk)
    return chunks


async def run_one(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    base_url: str,
    agent_slug: str,
    q: dict,
    timeout: float,
) -> dict:
    request_id = f"agent-e2e-{uuid.uuid4().hex}"
    try:
        resp = await client.post(
            f"{base_url}/api/agent-invocation/eval/runs",
            json={
                "query": q["query"],
                "agent_slug": agent_slug,
                "meta": {"request_id": request_id},
                "include_trajectory_summary": False,
            },
            headers=headers,
            timeout=timeout + 15,
        )
        resp.raise_for_status()
        payload = resp.json()
        if payload.get("status") != "completed":
            return {
                "query": q["query"],
                "section": q.get("section"),
                "kb_id": q.get("kb_id"),
                "error": f"run 未完成: {payload.get('status')} {payload.get('error', '')}",
            }

        agent_answer = payload.get("output") or ""
        thread_id = payload.get("thread_id")
        retrieved_chunks: list[dict] = []
        if thread_id:
            hist_resp = await client.get(
                f"{base_url}/api/chat/thread/{thread_id}/history", headers=headers, timeout=timeout + 15
            )
            if hist_resp.status_code == 200:
                retrieved_chunks = extract_retrieved_chunks(hist_resp.json())

        record = {
            "query": q["query"],
            "gold_answer": q.get("gold_answer"),
            "gold_chunk_ids": q.get("gold_chunk_ids") or [],
            "section": q.get("section"),
            "kb_id": q.get("kb_id"),
            "agent_answer": agent_answer,
            "retrieved_chunks": retrieved_chunks,
            "thread_id": thread_id,
            "request_id": request_id,
            "kb_scope": payload.get("knowledge_disposition", {}).get("kb_scope")
            if isinstance(payload.get("knowledge_disposition"), dict)
            else None,
        }
        return record
    except Exception as e:
        return {
            "query": q["query"],
            "section": q.get("section"),
            "kb_id": q.get("kb_id"),
            "error": f"调用失败: {e}",
        }


async def run(args: argparse.Namespace) -> int:
    username = args.username or __import__("os").environ.get("YUXI_TEST_USER")
    password = args.password or __import__("os").environ.get("YUXI_TEST_PASSWORD")
    if not username or not password:
        print("需要登录账号，请用 --username/--password 或环境变量 YUXI_TEST_USER/YUXI_TEST_PASSWORD", file=sys.stderr)
        return 1

    questions = []
    for line_num, line in enumerate(Path(args.testset).read_text(encoding="utf-8").strip().split("\n"), 1):
        if not line.strip():
            continue
        item = json.loads(line)
        if "query" not in item:
            raise ValueError(f"第{line_num}行缺少 query")
        questions.append(item)
    if not questions:
        print("测试集为空", file=sys.stderr)
        return 1

    async with httpx.AsyncClient(timeout=60.0) as client:
        token = await login(args.base_url, username, password, client)
        headers = {"Authorization": f"Bearer {token}"}
        print(f"登录成功，开始运行 {len(questions)} 题（agent: {args.agent_slug}）")

        Path(args.output).mkdir(parents=True, exist_ok=True)
        safe_name = args.name or ""
        if not safe_name:
            from datetime import date

            safe_name = date.today().strftime("%Y%m%d")
        out = str(Path(args.output) / f"agent_e2e_{safe_name}.jsonl")

        semaphore = asyncio.Semaphore(max(1, args.concurrency))

        # 边跑边写：长跑中断/崩溃时保留已完成结果，并支持实时观察进度
        with open(out, "w", encoding="utf-8") as f:

            async def guarded(q: dict) -> dict:
                async with semaphore:
                    r = await run_one(client, headers, args.base_url, args.agent_slug, q, args.timeout)
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
                    f.flush()
                    return r

            results = list(await asyncio.gather(*[guarded(q) for q in questions]))

        ok = [r for r in results if "error" not in r]
        failed = [r for r in results if "error" in r]
        answered = [r for r in ok if (r.get("agent_answer") or "").strip()]
        print(
            f"完成：{len(ok)}/{len(results)} 成功，{len(failed)} 失败，其中 {len(answered)} 题有答案，"
            f"{sum(1 for r in ok if r['retrieved_chunks'])} 题检索到上下文"
        )
        for r in failed:
            print(f"  失败: {r['query'][:40]} → {r['error'][:120]}")

        print(f"结果已写入: {out}")
        return 0


def main() -> int:
    args = parse_args()
    try:
        return asyncio.run(run(args))
    except Exception as e:
        print(f"运行失败: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
