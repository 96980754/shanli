"""系统首 token 验收脚本（一键可复现 + Markdown 展示报告）。

驱动生产 worker 路径 stream_agent_chat，从函数入口开始计时，测得系统侧真实处理延迟
（不含 HTTP、鉴权、队列等待与渲染开销）。自动发现默认 agent slug 与 superadmin/admin uid。
首轮标 cold，其余 warm；统计含冷启动 vs 暖态两行；暖态 p50 < 1000ms 判定达标
（对外口径「首 token ≈ 0.5s」）；first_tool / end 只展示不判定。
产物 Markdown 报告 + jsonl 原始数据到 reports/ 目录，末尾 os._exit(0) 跳过 async 生成器终结。

用法（api-dev 容器内）:
  docker exec -w /app api-dev uv run --no-sync python -u /app/scripts/bench/first_token_acceptance.py \
      --rounds 10
"""

from __future__ import annotations

import warnings

# 演示脚本只关心逐轮性能数据：静默 pydantic/langchain 等第三方库的 DeprecationWarning，
# 避免一屏 python warning 污染输出（`warnings.filterwarnings` 对运行时告警同样生效）。
warnings.filterwarnings("ignore")

import argparse
import asyncio
import json
import os
import shutil
import statistics
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

from sqlalchemy import select

import yuxi.agents.base as agents_base
from yuxi.repositories.agent_run_repository import AgentRunRepository
from yuxi.services.chat_service import stream_agent_chat
from yuxi.services.input_message_service import build_chat_input_message
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import User

# langgraph 会在 import 期间重新打开 beta 告警，import 后再断言一次忽略。
warnings.filterwarnings("ignore")

from loguru import logger as loguru_logger

# loguru 在 import yuxi 时已挂上 DEBUG 级控制台 handler，生产链路 INFO/DEBUG 日志
# 会混进输出污染逐轮性能数据。演示脚本只关心性能报告：收掉默认 handler，仅保留一条
# stderr 的 WARNING+ 输出，真实告警/错误仍可见。
loguru_logger.remove()
loguru_logger.add(
    sys.stderr,
    level="WARNING",
    format="<level>{level}</level> <cyan>{file}:{line}</cyan>: <level>{message}</level>",
    colorize=False,
    enqueue=False,
)

DEFAULT_QUERY = "POCSTARS MDM 平台有哪些核心功能？请结合知识库说明。"
ACCEPT_P50_MS = 1000.0  # 验收标准：暖态（去首轮冷启动）首 token p50 < 1000ms
REPORTS_DIR = Path(__file__).resolve().parent / "reports"
# 固定沙箱作用域：全部轮次共用一个沙箱容器（首轮冷建、后续热复用）。
# 每轮 thread_id 仍保持独立不串上下文，但缓存键 uid::file_thread_id::skills_thread_id 相同，
# 避免 10 个并发容器把内存撑爆（每个沙箱约 600MiB）。配合 chat_service 透传 meta.file_thread_id。
_BENCH_SANDBOX_SCOPE = "bench-sandbox"

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
            if isinstance(payload, dict) and payload.get("method") == "tools" and _STATE["first_tool"] is None:
                _STATE["first_tool"] = now
        yield event


agents_base.BaseAgent._stream_input_with_state = _timed_stream


def percentile(data, p):
    data = sorted(data)
    if not data:
        return 0.0
    k = (len(data) - 1) * (p / 100.0)
    f = int(k)
    c = f + 1 if f + 1 < len(data) else f
    return data[f] + (data[c] - data[f]) * (k - f)


def _stats(ms_list: list[float]) -> dict | None:
    """min/avg/max + 分位数，空列表返回 None。"""
    if not ms_list:
        return None
    return {
        "min": min(ms_list),
        "avg": statistics.mean(ms_list),
        "max": max(ms_list),
        "p50": percentile(ms_list, 50),
        "p90": percentile(ms_list, 90),
        "p95": percentile(ms_list, 95),
        "p99": percentile(ms_list, 99),
        "n": len(ms_list),
    }


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


# ---------------------------------------------------------------- 自动发现


async def _discover_agent_slug(db) -> str:
    from yuxi.repositories.agent_repository import AgentRepository, DEFAULT_AGENT_SLUG

    agent = await AgentRepository(db).get_default()
    return agent.slug if agent else DEFAULT_AGENT_SLUG


async def _discover_uid(db) -> str:
    result = await db.execute(
        select(User.uid).where(User.role.in_(["superadmin", "admin"]), User.is_deleted == 0).order_by(User.id).limit(1)
    )
    uid = result.scalar_one_or_none()
    if not uid:
        raise RuntimeError("未找到 superadmin/admin 用户")
    return uid


# ---------------------------------------------------------------- 核心


async def _run_once(agent_slug: str, query: str, uid: str, timeout: float) -> dict:
    thread_id = str(uuid.uuid4())
    run_id = str(uuid.uuid4())
    request_id = str(uuid.uuid4())

    timing = {"init": None, "first": None, "end": None, "first_kind": None, "timed_out": False}
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
            # 固定沙箱作用域：10 轮复用同一个沙箱，线程仍独立不串上下文
            "file_thread_id": _BENCH_SANDBOX_SCOPE,
            "skills_thread_id": _BENCH_SANDBOX_SCOPE,
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
        deadline = time.monotonic() + timeout if timeout > 0 else None
        async for chunk_bytes in stream:
            if deadline is not None and time.monotonic() > deadline:
                # 单轮超时：截取已采集的首 token 数据继续下一轮，保证最终报告总能输出。
                timing["timed_out"] = True
                break
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


def _series(records: list[dict], key: str) -> list[float]:
    return [r[key] * 1000 for r in records if r.get(key) is not None]


def _fmt_ms(ms: float | None) -> str:
    return f"{ms * 1000:.0f}" if ms is not None else "-"


def _verdict(warm_stats: dict | None) -> str:
    if warm_stats is None:
        return "—"
    return "✅" if warm_stats["p50"] < ACCEPT_P50_MS else "❌"


# ---------------------------------------------------------------- 报告生成


def build_report(args, agent_slug, uid, results, all_stats, warm_stats) -> dict:
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return {
        "generated_at": stamp,
        "command": (
            "docker exec -w /app api-dev uv run --no-sync python -u "
            f"/app/scripts/bench/first_token_acceptance.py --rounds {args.rounds}"
        ),
        "agent_slug": agent_slug,
        "uid": uid,
        "query": args.query,
        "rounds": args.rounds,
        "results": results,
        "stats": {"all": all_stats, "warm": warm_stats},
    }


def write_jsonl(path: Path, report: dict) -> None:
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _fmt_stat_cells(stats: dict) -> str:
    """返回 min/p50/p90/p95/p99/max/n 七个单元格（不含首尾分隔符），供外层拼完整行。"""
    return (
        f"{stats['min']:.0f} | {stats['p50']:.0f} | {stats['p90']:.0f} "
        f"| {stats['p95']:.0f} | {stats['p99']:.0f} | {stats['max']:.0f} | {stats['n']}"
    )


def write_markdown(report: dict, md_path: Path) -> None:
    L = [
        "# 智知库（YUXI）系统首 token 验收展示报告",
        "",
        f"> 被测配置：agent=`{report['agent_slug']}`  rounds={report['rounds']}  query=`{report['query'][:50]}…`",
        "> 验收口径：暖态（去首轮冷启动）首 token p50 **< 1000ms**；对外口径「首 token ≈ 0.5s」",
        "> 运行环境：Docker Compose 全栈（api-dev 容器），系统侧（不含 HTTP/排队/渲染）",
        "> 生成脚本：`/app/scripts/bench/first_token_acceptance.py`",
        "",
    ]

    L += [
        "## 一、逐轮明细（首轮冷启动，其余暖态）",
        "",
        "| round | 冷热 | init | first_model | first_tool | outer(kind) | end | tools |",
        "|:--:|:--:|--:|--:|--:|--:|--:|:--|",
    ]
    for r in report["results"]:
        if r.get("error"):
            L.append(f"| {r['round']} | {'冷' if r.get('cold') else '暖'} | ERROR {r['error'][:60]} |")
            continue
        kind = r["first_kind"] or "-"
        hot = ("冷" if r["cold"] else "暖") + ("·超时" if r.get("timed_out") else "")
        L.append(
            f"| {r['round']} | {hot} | {_fmt_ms(r['init'])} | {_fmt_ms(r['first_model'])} "
            f"| {_fmt_ms(r['first_tool'])} | {_fmt_ms(r['first'])}({kind}) | {_fmt_ms(r['end'])} "
            f"| {','.join(r['tool_calls']) or '-'} |"
        )

    all_stats = report["stats"]["all"]
    warm_stats = report["stats"]["warm"]
    L += [
        "",
        "## 二、统计（min/p50/p90/p95/p99/max，ms）",
        "",
        "| 指标 | 口径 | min | p50 | p90 | p95 | p99 | max | n |",
        "|:---|:---|--:|--:|--:|--:|--:|--:|--:|",
    ]
    metric_labels = (
        ("first_model", "系统首 token"),
        ("first_tool", "首个知识检索动作"),
        ("first", "外层首个可见输出"),
        ("end", "完整回答"),
    )
    for key, label in metric_labels:
        if all_stats.get(key):
            L.append(f"| {label} | 含冷启动 | {_fmt_stat_cells(all_stats[key])} |")
        if warm_stats.get(key):
            L.append(f"| {label} | 暖态（去首轮） | {_fmt_stat_cells(warm_stats[key])} |")

    L += ["", "## 三、验收结论", "", "| 指标 | 口径 | 实测(p50) | 达标 |", "|:---|:---|:--:|:---:|"]
    fm = warm_stats.get("first_model")
    if fm:
        L.append(f"| 系统首 token | 暖态 | {fm['p50']:.0f}ms | {_verdict(fm)} |")
    if warm_stats.get("first_tool"):
        L.append(f"| 首个知识检索动作 | 暖态 | {warm_stats['first_tool']['p50']:.0f}ms | —（只展示，不判定） |")
    if warm_stats.get("end"):
        L.append(f"| 完整回答 | 暖态 | {warm_stats['end']['p50']:.0f}ms | —（只展示，不判定） |")

    L += ["", "## 附：复现命令", "", "```bash", report["command"], "```", ""]
    md_path.write_text("\n".join(L), encoding="utf-8")


# ---------------------------------------------------------------- 入口


async def main() -> None:
    parser = argparse.ArgumentParser(description="系统首 token 验收（生产 worker 路径，自动发现 + Markdown 报告）")
    parser.add_argument("--rounds", type=int, default=10, help="轮数（首轮标冷启动，其余暖态）")
    parser.add_argument("--query", default=DEFAULT_QUERY)
    parser.add_argument("--agent-slug", default=None, help="留空自动发现默认 agent")
    parser.add_argument("--uid", default=None, help="留空自动发现 superadmin/admin")
    parser.add_argument("--out-prefix", default="first_token_acceptance")
    parser.add_argument("--no-latest", action="store_true", help="不写 *_latest.md 稳定指针")
    parser.add_argument(
        "--round-timeout",
        type=float,
        default=90.0,
        help="单轮超时（秒）：超时后截取已采集的首 token 数据继续下一轮，保证最终报告总输出",
    )
    args = parser.parse_args()

    async with pg_manager.get_async_session_context() as db:
        agent_slug = args.agent_slug or await _discover_agent_slug(db)
        uid = args.uid or await _discover_uid(db)
    print(f"agent={agent_slug} uid={uid} query={args.query[:40]!r}", flush=True)

    results = []
    for i in range(1, args.rounds + 1):
        r = await _run_once(agent_slug, args.query, uid, timeout=args.round_timeout)
        r.update({"round": i, "cold": (i == 1)})
        if r.get("error"):
            print(f"  r{i}: ERROR {r['error']}", flush=True)
        else:
            kind = r["first_kind"] or "-"
            timeout_mark = " [超时]" if r.get("timed_out") else ""
            print(
                f"  r{i}{'(cold)' if r['cold'] else ''}{timeout_mark}: init={_fmt_ms(r['init'])} "
                f"model={_fmt_ms(r['first_model'])} tool={_fmt_ms(r['first_tool'])} "
                f"outer={_fmt_ms(r['first'])}({kind}) end={_fmt_ms(r['end'])} tools={r['tool_calls']}",
                flush=True,
            )
        results.append(r)

    ok = [r for r in results if not r.get("error")]
    warm = [r for r in ok if not r["cold"]]
    all_stats = {k: _stats(_series(ok, k)) for k in ("first_model", "first_tool", "first", "end")}
    warm_stats = {k: _stats(_series(warm, k)) for k in ("first_model", "first_tool", "first", "end")}

    report = build_report(args, agent_slug, uid, results, all_stats, warm_stats)
    reports_dir = REPORTS_DIR
    reports_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = f"{args.out_prefix}_{stamp}"
    jsonl_path = reports_dir / f"{base}.jsonl"
    md_path = reports_dir / f"{base}.md"
    write_jsonl(jsonl_path, report)
    write_markdown(report, md_path)
    if not args.no_latest:
        shutil.copy(md_path, reports_dir / f"{args.out_prefix}_latest.md")
    print(f"\n报告: {md_path}", flush=True)
    print(f"原始数据: {jsonl_path}", flush=True)
    os._exit(0)  # 跳过 async 生成器终结（astream_events 在 shutdown_asyncgens 会挂起）


if __name__ == "__main__":
    asyncio.run(main())
