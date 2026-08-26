#!/usr/bin/env python3
"""重导客服知识库（一次性工具）。

流程：登录 admin → 创建 KB「客服知识库」→ 上传 md → 提交文档入库(auto_index) →
轮询任务状态直到完成 → 校验检索命中一条已知黄金答案。

用法（api-dev 容器内）：
    python /app/scripts/eval_datasets/import_kefu_kb.py
"""

from __future__ import annotations

import json
import time

import httpx

BASE_URL = "http://localhost:5050"
MD_PATH = "/app/scripts/eval_datasets/客服知识库.md"
KB_NAME = "客服知识库"
CATEGORY_ID = 1
EMBEDDING_MODEL = "siliconflow-cn:Pro/BAAI/bge-m3"


def login() -> str:
    resp = httpx.post(
        f"{BASE_URL}/api/auth/token",
        data={"username": "admin", "password": "admin"},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def list_kbs(headers: dict) -> list[dict]:
    resp = httpx.get(f"{BASE_URL}/api/knowledge/databases", headers=headers, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, list):
        return data
    for key in ("items", "data", "databases", "knowledge_bases"):
        if isinstance(data, dict) and isinstance(data.get(key), list):
            return data[key]
    return [data]
def create_kb(headers: dict) -> str:
    body = {
        "database_name": KB_NAME,
        "description": "客服知识库 Q&A（从甲方客服知识库导出重导）",
        "category_id": CATEGORY_ID,
        "embedding_model_spec": EMBEDDING_MODEL,
        "kb_type": "milvus",
    }
    resp = httpx.post(f"{BASE_URL}/api/knowledge/databases", headers=headers, json=body, timeout=60)
    resp.raise_for_status()
    data = resp.json()
    return data.get("kb_id") or data.get("database_id")


def upload_file(headers: dict, kb_id: str) -> str:
    with open(MD_PATH, "rb") as f:
        files = {"file": ("客服知识库.md", f, "text/markdown")}
        resp = httpx.post(
            f"{BASE_URL}/api/knowledge/files/upload",
            headers=headers,
            params={"kb_id": kb_id, "duplicate_strategy": "skip"},
            files=files,
            timeout=300,
        )
    resp.raise_for_status()
    return resp.json()["file_path"]


def add_documents(headers: dict, kb_id: str, file_path: str) -> str:
    resp = httpx.post(
        f"{BASE_URL}/api/knowledge/databases/{kb_id}/documents",
        headers=headers,
        json={"items": [file_path], "params": {"auto_index": True, "duplicate_strategy": "skip"}},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()["task_id"]


def wait_task(headers: dict, task_id: str, timeout_s: float = 1800.0) -> dict:
    deadline = time.time() + timeout_s
    last_status = ""
    while time.time() < deadline:
        resp = httpx.get(f"{BASE_URL}/api/tasks/{task_id}", headers=headers, timeout=30)
        if resp.status_code == 404:
            print(f"[{time.strftime('%H:%M:%S')}] task {task_id} 未找到，可能已随完成移除")
            return {"status": "not_found"}
        resp.raise_for_status()
        task = resp.json()["task"]
        status = task.get("status")
        if status != last_status:
            print(f"[{time.strftime('%H:%M:%S')}] task status = {status}")
            last_status = status
        if status in ("completed", "succeeded", "success"):
            return task
        if status in ("failed", "error", "cancelled", "canceled"):
            return task
        time.sleep(5)
    return {"status": "timeout"}


def verify_retrieval(headers: dict) -> None:
    probe = "调度台-如何关闭sos报警信息"
    gold = "点击调度台左边眼睛图标👁️后右上角会出现×，点击×即可关闭"
    resp = httpx.post(
        f"{BASE_URL}/api/knowledge/search",
        headers=headers,
        json={"query": probe, "limit": 5},
        timeout=120,
    )
    if resp.status_code != 200:
        print(f"校验检索接口异常: HTTP {resp.status_code} {resp.text[:200]}")
        return
    data = resp.json()
    hits = data if isinstance(data, list) else data.get("results") or data.get("items") or []
    if not hits:
        print("校验检索：0 命中")
        return
    combined = " ".join(str(h.get("content") or "") for h in hits)
    print(f"校验检索：命中 {len(hits)} 条；含黄金答案关键片段 = {gold[:20] in combined or '关闭sos' in combined}")
    for h in hits[:5]:
        print(f"  - {str(h.get('content') or '')[:60]!r}")


def main() -> int:
    token = login()
    headers = {"Authorization": f"Bearer {token}"}
    print("登录成功")

    # 幂等：若已存在同名 KB 则复用，不重复创建
    kb_id = None
    for kb in list_kbs(headers):
        if kb.get("name") == KB_NAME:
            kb_id = kb.get("kb_id") or kb.get("id") or kb.get("database_id")
            print(f"复用已存在 KB: {kb_id} (docs={kb.get('document_count')}, chunks={kb.get('chunk_count')})")
            break

    if not kb_id:
        kb_id = create_kb(headers)
        print(f"已创建 KB: {kb_id}")

    file_path = upload_file(headers, kb_id)
    print(f"上传成功: {file_path[:120]}")

    task_id = add_documents(headers, kb_id, file_path)
    print(f"已提交入库任务: {task_id}")

    task = wait_task(headers, task_id)
    print(f"任务最终状态: {task.get('status')}")
    if task.get("status") not in ("completed", "succeeded", "success"):
        print(json.dumps(task, ensure_ascii=False, default=str)[:800])
        return 1

    verify_retrieval(headers)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
