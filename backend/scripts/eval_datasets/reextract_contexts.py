#!/usr/bin/env python3
"""按修复后的解析逻辑重拉线程轨迹、重建 retrieved_chunks（内部工具）。

背景：run_agent_e2e 的记录器曾只采集 query_kb/query_kbs 的检索片段，遗漏
find_kb_document/open_kb_document 返回的正文证据，导致忠实度评分看不到 Agent 实际读到的
内容（向量检索失败、Agent 改用文件检索兜底作答时被误判为「无据可查」）。本脚本对既有
E2E 结果文件逐条重拉 thread history，用当前 extract_retrieved_chunks 逻辑重建
retrieved_chunks，原位更新（原件备份到 synthetic/ctx_backup_v1/），随后重跑 merge_final
与评分即可对比忠实度前后变化。agent_answer 不变，仅证据上下文变化。

用法（容器内）：
    docker exec api-dev python /app/scripts/eval_datasets/reextract_contexts.py \
        --file .../agent_e2e_rev25_0818.jsonl --file .../agent_e2e_retry6_4.jsonl ...
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import os
import shutil
import sys
from pathlib import Path

import httpx

BASE = Path(__file__).resolve().parent
SYN = BASE / "synthetic"
BACKUP = SYN / "ctx_backup_v1"
_SCRIPT = BASE.parent / "run_agent_e2e.py"


def load_extractor():
    spec = importlib.util.spec_from_file_location("run_agent_e2e", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.extract_retrieved_chunks


async def login(base_url: str, username: str, password: str, client: httpx.AsyncClient) -> str:
    resp = await client.post(
        f"{base_url}/api/auth/token",
        data={"username": username, "password": password},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    resp.raise_for_status()
    token = resp.json().get("access_token")
    if not token:
        raise RuntimeError(f"登录失败: {resp.text[:200]}")
    return token


async def reextract_one(
    client: httpx.AsyncClient,
    headers: dict,
    base_url: str,
    extract,
    record: dict,
    timeout: float,
) -> dict:
    thread_id = record.get("thread_id")
    if not thread_id or record.get("error"):
        return record
    try:
        hr = await client.get(f"{base_url}/api/chat/thread/{thread_id}/history", headers=headers, timeout=timeout)
        hr.raise_for_status()
        record["retrieved_chunks"] = extract(hr.json())
        record["_ctxfix"] = True
    except Exception as e:
        record["_ctxfix_error"] = str(e)[:200]
    return record


async def run(args: argparse.Namespace) -> int:
    username = args.username or os.environ.get("YUXI_TEST_USER") or os.environ.get("ADMIN_USER") or "admin"
    password = args.password or os.environ.get("YUXI_TEST_PASSWORD") or os.environ.get("ADMIN_PASS") or "admin"
    extract = load_extractor()
    BACKUP.mkdir(parents=True, exist_ok=True)

    async with httpx.AsyncClient(timeout=60.0) as client:
        token = await login(args.base_url, username, password, client)
        headers = {"Authorization": f"Bearer {token}"}
        print(f"已登录，重拉 {len(args.file)} 个结果文件")

        for path in args.file:
            src = Path(path)
            records = [json.loads(line) for line in src.read_text(encoding="utf-8").splitlines() if line.strip()]
            if not records:
                print(f"{src.name}: 空文件，跳过")
                continue
            shutil.copy2(src, BACKUP / src.name)
            semaphore = asyncio.Semaphore(args.concurrency)

            async def guarded(r: dict) -> dict:
                async with semaphore:
                    return await reextract_one(client, headers, args.base_url, extract, r, args.timeout)

            updated = await asyncio.gather(*[guarded(r) for r in records])
            fixed = sum(1 for r in updated if r.get("_ctxfix"))
            with_ctx = sum(1 for r in updated if r.get("retrieved_chunks"))
            failed = sum(1 for r in updated if r.get("_ctxfix_error"))
            src.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in updated), encoding="utf-8")
            print(f"{src.name}: {len(records)} 行 | 重拉成功 {fixed} | 有上下文 {with_ctx} | 重拉失败 {failed}")

    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="重拉线程轨迹重建 retrieved_chunks")
    parser.add_argument("--file", action="append", required=True, help="要重建的 E2E 结果文件（可重复）")
    parser.add_argument("--base-url", default="http://localhost:5050")
    parser.add_argument("--username", help="登录账号（默认 YUXI_TEST_USER/ADMIN_USER）")
    parser.add_argument("--password", help="登录密码（默认 YUXI_TEST_PASSWORD/ADMIN_PASS）")
    parser.add_argument("--concurrency", type=int, default=8, help="同时重拉的线程数")
    parser.add_argument("--timeout", type=float, default=120.0, help="单线程 history 拉取超时秒数")
    return parser.parse_args()


def main() -> int:
    try:
        return asyncio.run(run(parse_args()))
    except Exception as e:
        print(f"重拉失败: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
