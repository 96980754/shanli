"""通过生产问答链路（default-chatbot）批量端到端回答 100 道验收题并记录。

链路：stream_agent_chat = 前端问答同一条生产路径（ReAct + 知识库工具 + 深度查证）。
从 message_delta 累积完整回答，记录工具序列、耗时、状态。

特性：
- 串行执行（与用户确认的口径：后台串行）
- 断点续跑：已完成的 index 跳过
- 失败重试（--retries N）
- 输出：jsonl（逐条记录）+ Markdown 汇总

用法（api-dev 容器内，cwd=/app/scripts/eval_datasets）：
  python -u e2e_answer_100.py [--uid admin] [--out reports/e2e_answers.jsonl] [--retries 2]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
import uuid
from pathlib import Path

from sqlalchemy import select

from yuxi.repositories.agent_run_repository import AgentRunRepository
from yuxi.services.chat_service import stream_agent_chat, stream_agent_resume
from yuxi.services.input_message_service import build_chat_input_message
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import User

DATASETS = ["poc.jsonl", "mcx.jsonl", "loc.jsonl"]
AGENT_SLUG = "default-chatbot"

# 知识库对部分题目存在多义（如“关闭 SOS 报警”在多个产品里有不同含义），
# agent 会通过 ask_user_question 澄清。批量验收时无法真人作答，
# 用此 canned 回答引导其按最常见含义继续，对齐 gold_answer 的“按最常见含义”作答风格。
RESUME_HINT = "请按知识库中最常见的含义直接回答，并给出具体操作步骤。"


def load_questions() -> list[dict]:
    """读取全部数据集，返回按 index 升序、去重的题目列表。"""
    seen: dict[int, dict] = {}
    for name in DATASETS:
        path = Path(name)
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            item = json.loads(line)
            seen[item["index"]] = item
    return [seen[k] for k in sorted(seen)]


async def _load_user(db, uid: str) -> User | None:
    result = await db.execute(select(User).where(User.uid == uid, User.is_deleted == 0))
    return result.scalar_one_or_none()


def _consume_chunk(chunk: dict, tool_calls: list[str], answer_parts: list[str]) -> str | None:
    """消费单个 chunk，更新工具序列与回答累积，返回终态 status（无则 None）。"""
    status = chunk.get("status")
    se = chunk.get("stream_event")
    if isinstance(se, dict):
        if se.get("type") == "tool_call_delta":
            name = se.get("name")
            if name and name not in tool_calls:
                tool_calls.append(name)
        if se.get("type") == "message_delta":
            content = se.get("content")
            if content:
                answer_parts.append(content)
    event = chunk.get("event")
    if isinstance(event, dict) and event.get("method") == "tools":
        data = event.get("data") or {}
        name = data.get("tool_name") if isinstance(data, dict) else None
        if name and name not in tool_calls:
            tool_calls.append(name)
    if status in ("finished", "error", "interrupted", "ask_user_question_required"):
        return status
    return None


async def answer_one(db, user: User, query: str) -> dict:
    """驱动一次完整问答（含澄清 interrupt 的自动 resume），失败时抛异常，由上层重试。

    知识库对部分题目存在多义（如“关闭 SOS 报警”），agent 会通过 ask_user_question
    澄清；批量验收无法真人作答，用 canned 回复 resume 同一线程，按最常见含义继续。
    """
    thread_id = str(uuid.uuid4())
    run_id = str(uuid.uuid4())
    request_id = str(uuid.uuid4())
    t0 = time.perf_counter()

    await AgentRunRepository(db).create_run(
        run_id=run_id,
        conversation_thread_id=thread_id,
        agent_slug=AGENT_SLUG,
        uid=user.uid,
        request_id=request_id,
        input_payload={"query": query},
    )
    meta = {
        "run_id": run_id,
        "request_id": request_id,
        "agent_slug": AGENT_SLUG,
        "thread_id": thread_id,
        "uid": user.uid,
        "has_image": False,
        "run_type": "chat",
    }

    tool_calls: list[str] = []
    answer_parts: list[str] = []
    terminal_status = "unknown"
    error_message = None
    clarify_rounds = 0

    stream = stream_agent_chat(
        agent_slug=AGENT_SLUG,
        thread_id=thread_id,
        meta=meta,
        input_message=build_chat_input_message(query),
        current_user=user,
        db=db,
        save_user_message=False,
    )
    status = None
    async for chunk_bytes in stream:
        try:
            chunk = json.loads(chunk_bytes.decode("utf-8"))
        except json.JSONDecodeError:
            continue
        status = _consume_chunk(chunk, tool_calls, answer_parts) or status
        if status == "error":
            error_message = str(chunk.get("error_message") or chunk.get("error") or "")

    # 澄清 interrupt：自动 resume 同一线程（canned 回复），最多 2 轮，避免死循环
    while status == "ask_user_question_required" and clarify_rounds < 2:
        clarify_rounds += 1
        status = None
        resume_meta = {**meta, "run_type": "resume"}
        stream = stream_agent_resume(
            thread_id=thread_id,
            resume_input=RESUME_HINT,
            meta=resume_meta,
            current_user=user,
            db=db,
        )
        async for chunk_bytes in stream:
            try:
                chunk = json.loads(chunk_bytes.decode("utf-8"))
            except json.JSONDecodeError:
                continue
            status = _consume_chunk(chunk, tool_calls, answer_parts) or status
            if status == "error":
                error_message = str(chunk.get("error_message") or chunk.get("error") or "")

    elapsed_ms = int((time.perf_counter() - t0) * 1000)
    answer = "".join(answer_parts).strip()
    return {
        "thread_id": thread_id,
        "run_id": run_id,
        "tools": tool_calls,
        "answer": answer,
        "duration_ms": elapsed_ms,
        "status": status or terminal_status,
        "clarify_rounds": clarify_rounds,
        "error": error_message,
    }


def load_done(out_path: Path) -> dict[int, dict]:
    """读取已有输出，返回已完成 index -> 记录。"""
    done: dict[int, dict] = {}
    if not out_path.exists():
        return done
    for line in out_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        rec = json.loads(line)
        if rec.get("status") in ("finished", "interrupted"):
            done[rec["index"]] = rec
    return done


async def main() -> None:
    parser = argparse.ArgumentParser(description="100 题端到端回答（生产链路）")
    parser.add_argument("--uid", default="admin")
    parser.add_argument("--out", default="reports/e2e_answers.jsonl")
    parser.add_argument("--md", default="reports/e2e_answers.md")
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--only", type=int, nargs="*", help="只跑指定 index（调试用）")
    args = parser.parse_args()

    questions = load_questions()
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    md_path = Path(args.md)
    md_path.parent.mkdir(parents=True, exist_ok=True)

    if args.only:
        questions = [q for q in questions if q["index"] in set(args.only)]

    done = load_done(out_path)
    pending = [q for q in questions if q["index"] not in done]
    print(f"=== 100 题端到端回答 · {AGENT_SLUG} ===", flush=True)
    print(f"题目总数: {len(questions)}  已完成: {len(done)}  待跑: {len(pending)}", flush=True)

    async with pg_manager.get_async_session_context() as db:
        user = await _load_user(db, args.uid)
        if not user:
            print(f"user {args.uid} not found", flush=True)
            return
        for q in pending:
            index = q["index"]
            query = q["query"]
            section = q.get("section", "")
            print(f"[{index:>3}/{len(questions)}] {section} {query[:30]!r} ...", end="", flush=True)
            record = None
            for attempt in range(1, args.retries + 1):
                try:
                    record = await answer_one(db, user, query)
                    record["index"] = index
                    record["section"] = section
                    record["query"] = query
                    # 流内错误状态也算失败，重试；仅 finished 视为成功
                    if record["status"] == "finished" and record["answer"]:
                        break
                    if attempt < args.retries:
                        print(f" 状态={record['status']}，重试", end="", flush=True)
                except Exception as e:
                    print(f" 尝试{attempt}失败: {e}", end="", flush=True)
                    record = None
            if record is None:
                print(" 多次失败，跳过", flush=True)
                record = {
                    "index": index, "section": section, "query": query,
                    "answer": "", "tools": [], "duration_ms": -1,
                    "status": "error", "error": "max retries exceeded",
                }
            with out_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
            print(
                f" {record['status']} {record['duration_ms']}ms tools={record['tools']} "
                f"answer={len(record['answer'])}字",
                flush=True,
            )

    write_markdown(out_path, md_path)
    print(f"\n结果已写入: {out_path}", flush=True)
    print(f"汇总已写入: {md_path}", flush=True)
    os._exit(0)


def write_markdown(out_path: Path, md_path: Path) -> None:
    """从 jsonl 生成给甲方看的 Markdown 汇总（按题目 index 排序）。"""
    records: dict[int, dict] = {}
    for line in out_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        rec = json.loads(line)
        records[rec["index"]] = rec

    lines: list[str] = []
    lines.append("# 知识库问答端到端回答记录（100 题）\n")
    lines.append("> 链路：生产问答 Agent（default-chatbot）· ReAct 深度查证 + 知识库工具")
    lines.append(f"> 记录条数：{len(records)} 条\n")
    lines.append("| # | 分类 | 问题 | 状态 | 耗时 | 工具 | 回答摘要 |")
    lines.append("|:--:|:--|:--|:--:|--:|:--|:--|")
    for index in sorted(records):
        r = records[index]
        answer = (r.get("answer") or "").replace("\n", " ").strip()
        tools = ",".join(r.get("tools") or [])
        summary = answer[:40] + ("…" if len(answer) > 40 else "")
        lines.append(
            f"| {index} | {r.get('section','')} | {r.get('query','')} | {r.get('status','')} "
            f"| {r.get('duration_ms','-')} | {tools} | {summary} |"
        )
    md_path.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
