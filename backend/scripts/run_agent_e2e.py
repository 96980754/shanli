#!/usr/bin/env python3
"""真实 Agent 端到端测试 runner（内部工具）。

每个 worker 独占一条会话线程、串行跑分到的题目，采集系统答案与实际读取到的正文证据
（thread history tool_calls 中 query_kb/query_kbs/find_kb_document/open_kb_document 的结果），
落盘 JSONL 供后续评分与汇报报告使用。失败题记录 error 不中断。

为什么是「一题一条对话 + 沙箱按 worker 复用」：
- 线程历史是本轮 run 的上下文，同一条对话里跑多题时，后一题会读到前面所有题的问答——答案不再只由
  本题决定，评测测的就不是这一题；反问澄清中断的题还会把自己的待答问题留给下一题（实测出现过下一题
  的反问里列出上一题的现象）。所以每题新建一条对话，与 8/20 那批全量跑批（507 题 507 条对话）同条件。
- 但沙箱是按对话分配的（sandbox_id_for_thread，(uid, file_thread_id, skills_thread_id)，缺省回退
  thread_id），逐题新建对话若不管作用域就会逐题新建沙箱，每个约 500MB，几十题就把宿主机内存打满
  （实测跑到第 21 题时 milvus 被 OOM 重启）。所以 meta 里显式指定 file_thread_id/skills_thread_id
  为一个 worker 一条的固定作用域：对话逐题独立，沙箱 worker 内共用，沙箱数不超过并发数。
- 证据按 run_id 从对话历史切片，一题一条对话下本题证据天然不与他题混淆。

用法（容器内）：
    docker exec api-dev python /app/scripts/run_agent_e2e.py \
        --testset /app/scripts/eval_datasets/synthetic/poc.jsonl \
        --username <登录账号> --password <密码> --concurrency 3

账号密码也可通过环境变量 YUXI_TEST_USER / YUXI_TEST_PASSWORD 传入。
模型反问澄清（ask_user_question 中断）时有两种自动作答策略，二选一：
- `--clarify-reply <文本>`：每题都回同一段固定回复，让多模型比较拿到的是「澄清后的终答」
  而不是中断本身（对所有模型同条件，答案才可比）；
- `--clarify-pick-first`：每题取反问选项里的第一个作答，用于「拒答题在被追问时能不能答上来」
  这类补测；追问没给选项的题如实记未作答，不编造回答。
两者都可用 `--clarify-rounds N` 允许连续追问 N 轮（默认 1，模型再追问则按未完成记录）。

每题的 record 里带 steps（本次 run 的工具调用次数与递归步数近似值），用于扫参
「智能体最大执行步数」：传 --max-steps N 会在跑批前把 N 写进 Agent 的
config_json.context.max_execution_steps（跑完自动还原原配置），扫参脚本按 N 循环调用即可。
"""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import json
import sys
import time
import uuid
from pathlib import Path

import httpx

BASE_URL_DEFAULT = "http://localhost:5050"
DEFAULT_OUTPUT = "/app/scripts/eval_datasets/synthetic"
EVALUATION_SOURCE = "agent_evaluation"
TERMINAL_STATUSES = {"completed", "failed", "cancelled", "interrupted"}
POLL_INTERVAL_SECONDS = 3.0
# 会返回正文证据的检索类工具：query_kb/query_kbs 返回命中片段（SearchOutputSchema.results），
# find_kb_document 返回命中上下文窗口（FindOutputSchema.windows），open_kb_document 返回整窗正文
# （OpenOutputSchema.content）。search_file 只返回文件元信息（无正文）、read_file 是沙箱通用
# 文件读取器（读线程工作区文件而非 KB 内容），二者不作为忠实度证据采集；search_file 的文件名
# 另经 _collect_file_names 用作窗口片段的来源名（见 extract_retrieved_chunks）。
CONTENT_TOOLS = {"query_kb", "query_kbs", "find_kb_document", "open_kb_document"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="真实 Agent 端到端测试 runner")
    parser.add_argument("--testset", required=True, help="测试集 JSONL（每行 {query, gold_answer?, gold_chunk_ids?}）")
    parser.add_argument("--agent-slug", default="default-chatbot", help="要运行的 Agent slug")
    parser.add_argument(
        "--model-spec",
        help="可选，对话级模型覆盖（如 alibaba:qwen3.7-flash）；不传则用智能体自身配置的模型",
    )
    clarify = parser.add_mutually_exclusive_group()
    clarify.add_argument(
        "--clarify-reply",
        help="可选，模型用 ask_user_question 反问时自动续跑的固定回复；不传则中断即计失败",
    )
    clarify.add_argument(
        "--clarify-pick-first",
        action="store_true",
        help="可选，反问时每题自动选第一个选项作答；追问没给选项的题不续跑，按未作答记录",
    )
    parser.add_argument(
        "--clarify-rounds",
        type=int,
        default=1,
        help="反问最多续跑几轮，每轮都用同一策略作答（默认 1；模型再次反问则按未完成记录）",
    )
    parser.add_argument("--base-url", default=BASE_URL_DEFAULT, help="API 基础地址")
    parser.add_argument("--username", help="登录账号（默认取环境变量 YUXI_TEST_USER）")
    parser.add_argument("--password", help="登录密码（默认取环境变量 YUXI_TEST_PASSWORD）")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="结果输出目录")
    parser.add_argument("--name", default="", help="结果文件名后缀（默认当日日期）")
    parser.add_argument(
        "--concurrency",
        type=int,
        default=2,
        help="worker 数：每题一条独立会话线程，每 worker 固定一个沙箱作用域（默认 2）",
    )
    parser.add_argument("--timeout", type=float, default=1200.0, help="单题最长等待秒数（默认 1200）")
    parser.add_argument(
        "--max-steps",
        type=int,
        help="可选，跑批前把 Agent 的最大执行步数改成该值（跑完还原原配置）；不传则不动 Agent 配置",
    )
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


async def create_thread(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    base_url: str,
    agent_slug: str,
    title: str,
) -> str:
    """建一条会话线程；用作沙箱作用域时，其线程 ID 即该 worker 共用的沙箱归属。"""
    resp = await client.post(
        f"{base_url}/api/chat/thread",
        json={"agent_id": agent_slug, "title": title},
        headers=headers,
        timeout=60.0,
    )
    resp.raise_for_status()
    thread_id = resp.json().get("id")
    if not thread_id:
        raise RuntimeError(f"创建会话失败: {resp.text[:200]}")
    return thread_id


def _collect_file_names(messages: list[dict]) -> dict[str, str]:
    """file_id → 文件名，取自同一轮的 search_file 与 query_kb/query_kbs 结果。

    与前端 messageProcessor.js 的 fileInfoMap 同做法：find_kb_document/open_kb_document
    只回 file_id 不回文件名，而模型往往是先用 search_file 按文件名找到文件、再打开它。
    不采集 search_file，这类「向量检索没召回、改走文件检索兜底」的题在来源面板里就没有
    可归因的文件名，引用证据会整批丢失。
    """
    names: dict[str, str] = {}
    for msg in messages:
        for tc in msg.get("tool_calls") or []:
            if tc.get("status") != "success":
                continue
            try:
                payload = json.loads((tc.get("tool_call_result") or {}).get("content") or "")
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict):
                continue
            if tc.get("name") == "search_file":
                entries = [
                    (f.get("file_id"), f.get("filename")) for f in payload.get("files") or [] if isinstance(f, dict)
                ]
            elif tc.get("name") in {"query_kb", "query_kbs"}:
                entries = [
                    (c.get("file_id"), (c.get("metadata") or {}).get("source"))
                    for c in payload.get("results") or []
                    if isinstance(c, dict)
                ]
            else:
                continue
            for file_id, filename in entries:
                if file_id and isinstance(filename, str) and filename:
                    names.setdefault(str(file_id), filename)
    return names


def _parse_tool_content(tool_name: str, content: str, names: dict[str, str] | None = None) -> list[dict]:
    """按工具类型解析 tool_call_result.content 为正文证据片段；无正文的工具返回空。

    names 是 file_id → 文件名的同轮映射，用于给只带 file_id 的窗口片段补出来源名。
    """
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
        metadata = {"file_id": file_id, "source": _file_name(payload, names)}
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
                    "metadata": metadata,
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
                "metadata": {"file_id": file_id, "source": _file_name(payload, names)},
            }
        ]
    return []


def _file_name(payload: dict, names: dict[str, str] | None) -> str:
    """窗口片段的来源文件名：后端回填的 source 优先，缺失时用同轮 file_id 映射兜底。"""
    source = str(payload.get("source") or "").strip()
    if source or not names:
        return source
    return names.get(str(payload.get("file_id") or ""), "")


def extract_retrieved_chunks(history: dict, run_id: str) -> list[dict]:
    """提取本次 run 实际读取到的正文证据片段（去重）。

    只取 run_id 命中的消息：线程被多题复用时，按 run 切片才能保证证据属于本题。
    """
    messages = [msg for msg in history.get("history", []) if msg.get("run_id") == run_id]
    names = _collect_file_names(messages)
    seen: set[str] = set()
    chunks: list[dict] = []
    for msg in messages:
        for tc in msg.get("tool_calls") or []:
            if tc.get("name") not in CONTENT_TOOLS or tc.get("status") != "success":
                continue
            result = tc.get("tool_call_result") or {}
            for chunk in _parse_tool_content(tc["name"], result.get("content") or "", names):
                cid = str(chunk.get("id") or chunk.get("chunk_id") or "")
                if cid and cid not in seen:
                    seen.add(cid)
                    chunks.append(chunk)
    return chunks


def extract_run_steps(history: dict, run_id: str) -> dict:
    """统计本次 run 的执行规模（按 run_id 切片，与证据提取同口径）。

    LangGraph 的 recursion_limit 计的是 super-step：ReAct 循环里「模型节点 + 工具节点」
    一轮记 2 步，末尾不带工具调用的终答再记 1 步。历史里只有消息、没有真实步数，
    故用 est_steps = 2 × 带工具调用的 ai 消息数 + 1 近似；扫参时用「是否恰好触顶」反向校准。
    """
    tool_calls = 0
    by_tool: dict[str, int] = {}
    tool_turns = 0
    for msg in history.get("history", []):
        if msg.get("run_id") != run_id:
            continue
        calls = msg.get("tool_calls") or []
        if not calls:
            continue
        tool_turns += 1
        for tc in calls:
            name = str(tc.get("name") or "unknown")
            by_tool[name] = by_tool.get(name, 0) + 1
            tool_calls += 1
    return {
        "tool_calls": tool_calls,
        "by_tool": dict(sorted(by_tool.items(), key=lambda kv: (-kv[1], kv[0]))),
        "tool_turns": tool_turns,
        "est_steps": 2 * tool_turns + 1,
    }


def sum_run_steps(history: dict, run_ids: list[str]) -> dict:
    """合并一道题全部 run 的执行规模。

    反问澄清会拆成两轮独立 run，只统计末轮会漏掉首轮的检索成本；评价「这题花了几轮」
    必须合起来看。注意与 retrieved_chunks 口径不同——后者仍只取末轮 run（沿用既有报告口径）。
    """
    total: dict = {"tool_calls": 0, "by_tool": {}, "tool_turns": 0, "est_steps": 0, "runs": len(run_ids)}
    for run_id in run_ids:
        steps = extract_run_steps(history, run_id)
        total["tool_calls"] += steps["tool_calls"]
        total["tool_turns"] += steps["tool_turns"]
        total["est_steps"] += steps["est_steps"]
        for name, count in steps["by_tool"].items():
            total["by_tool"][name] = total["by_tool"].get(name, 0) + count
    total["by_tool"] = dict(sorted(total["by_tool"].items(), key=lambda kv: (-kv[1], kv[0])))
    return total


async def apply_max_steps(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    base_url: str,
    agent_slug: str,
    max_steps: int,
) -> dict:
    """把最大执行步数写进 Agent 配置，返回改动前的 config_json 供跑完还原。"""
    resp = await client.get(f"{base_url}/api/agent/{agent_slug}", headers=headers, timeout=60.0)
    resp.raise_for_status()
    agent = resp.json().get("agent") or {}
    original = agent.get("config_json") or {}
    config_json = json.loads(json.dumps(original))  # 深拷贝，避免与还原值共享嵌套
    config_json.setdefault("context", {})["max_execution_steps"] = max_steps
    put = await client.put(
        f"{base_url}/api/agent/{agent_slug}",
        json={"config_json": config_json},
        headers=headers,
        timeout=60.0,
    )
    put.raise_for_status()
    return original


async def restore_config_json(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    base_url: str,
    agent_slug: str,
    config_json: dict,
) -> None:
    resp = await client.put(
        f"{base_url}/api/agent/{agent_slug}",
        json={"config_json": config_json},
        headers=headers,
        timeout=60.0,
    )
    resp.raise_for_status()


async def create_run(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    base_url: str,
    payload: dict,
) -> str:
    resp = await client.post(
        f"{base_url}/api/agent/runs",
        json=payload,
        headers=headers,
        timeout=60.0,
    )
    resp.raise_for_status()
    run_id = resp.json().get("run_id")
    if not run_id:
        raise RuntimeError(f"创建 run 失败: {resp.text[:200]}")
    return run_id


async def start_run(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    base_url: str,
    agent_slug: str,
    thread_id: str,
    query: str,
    request_id: str,
    model_spec: str | None = None,
    sandbox_scope: str | None = None,
) -> str:
    payload = {
        "query": query,
        "agent_slug": agent_slug,
        "thread_id": thread_id,
        "meta": run_meta(request_id, sandbox_scope),
    }
    if model_spec:
        payload["model_spec"] = model_spec
    return await create_run(client, headers, base_url, payload)


def run_meta(request_id: str, sandbox_scope: str | None) -> dict:
    """run 的 meta：把沙箱作用域与对话线程分开——一题一条对话（上下文干净），沙箱按 worker 复用。

    沙箱按 (uid, file_thread_id, skills_thread_id) 分配，缺省回退 thread_id。若逐题新建对话又
    不指定作用域，就会逐题新建沙箱（每个约 500MB），几十题就把宿主机内存打满；固定作用域则
    worker 内所有题共用同一个沙箱。后端对这两个键是显式支持的（chat_service 的沙箱作用域解析）。
    """
    meta = {"source": EVALUATION_SOURCE, "request_id": request_id}
    if sandbox_scope:
        meta["file_thread_id"] = sandbox_scope
        meta["skills_thread_id"] = sandbox_scope
    return meta


async def wait_run(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    base_url: str,
    run_id: str,
    timeout: float,
) -> dict:
    """轮询 run 直到终态；超时先请求取消并等它落定，避免残留 run 占住线程。"""
    deadline = time.monotonic() + timeout
    timed_out = False
    while True:
        resp = await client.get(f"{base_url}/api/agent/runs/{run_id}/result", headers=headers, timeout=60.0)
        resp.raise_for_status()
        payload = resp.json()
        if payload.get("status") in TERMINAL_STATUSES:
            if timed_out:
                raise TimeoutError(f"run 超时（{timeout:.0f}s），已取消（终态 {payload.get('status')}）")
            return payload
        if not timed_out and time.monotonic() >= deadline:
            timed_out = True
            cancel_deadline = time.monotonic() + 180
            await client.post(f"{base_url}/api/agent/runs/{run_id}/cancel", headers=headers, timeout=60.0)
        elif timed_out and time.monotonic() >= cancel_deadline:
            raise TimeoutError(f"run 超时（{timeout:.0f}s），取消后仍未落定")
        await asyncio.sleep(POLL_INTERVAL_SECONDS)


def resolve_question_id(item: dict, index: int) -> str:
    """问题 ID；缺省规则与前端 normalizeQuestions、后端 normalize_questions 一致（q-1 起）。"""
    return str(item.get("question_id") or item.get("questionId") or f"q-{index + 1}").strip()


def build_clarification_answer(questions: list[dict], reply: str) -> dict[str, str]:
    """把固定回复套到本次中断的问题 ID 上。"""
    answer: dict[str, str] = {}
    for index, item in enumerate(questions):
        if not isinstance(item, dict):
            continue
        qid = resolve_question_id(item, index)
        if qid:
            answer[qid] = reply
    return answer


def build_first_option_answer(questions: list[dict]) -> tuple[dict, list[str]]:
    """每题取第一个选项作答：返回（答案、没有可选项因而未作答的问题 ID）。

    取值形状与前端 HumanApprovalModal 的 buildAnswer 一致：单选给字符串、多选给列表。
    追问没给选项的题如实留空，不编造回答（调用方据此放弃本轮续跑）。
    """
    answer: dict[str, object] = {}
    unanswered: list[str] = []
    for index, item in enumerate(questions):
        if not isinstance(item, dict):
            continue
        qid = resolve_question_id(item, index)
        if not qid:
            continue
        options = [opt for opt in (item.get("options") or []) if isinstance(opt, dict)]
        values = [str(opt.get("value") or opt.get("label") or "").strip() for opt in options]
        values = [value for value in values if value]
        if not values:
            unanswered.append(qid)
            continue
        answer[qid] = [values[0]] if item.get("multi_select") else values[0]
    return answer, unanswered


async def fetch_interrupt_questions(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    base_url: str,
    run_id: str,
) -> list[dict]:
    """从 run 事件流取本次中断的提问列表：run result 接口不含该载荷，只在事件里。"""
    async with client.stream(
        "GET", f"{base_url}/api/agent/runs/{run_id}/events", headers=headers, params={"verbose": "true"}, timeout=180.0
    ) as resp:
        resp.raise_for_status()
        async for line in resp.aiter_lines():
            if not line.startswith("data: "):
                continue
            chunk = (json.loads(line[6:]).get("payload") or {}).get("chunk") or {}
            if chunk.get("status") in {"ask_user_question_required", "human_approval_required"}:
                questions = chunk.get("questions")
                return questions if isinstance(questions, list) else []
    return []


def is_clarification_interrupt(payload: dict) -> bool:
    """本次 run 是否停在「模型反问澄清」上（区别于失败/取消等其它中断）。"""
    return (
        payload.get("status") == "interrupted"
        and (payload.get("error") or {}).get("type") == "ask_user_question_required"
    )


async def resume_after_clarification(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    base_url: str,
    agent_slug: str,
    thread_id: str,
    run_id: str,
    answer: dict,
    timeout: float,
    model_spec: str | None = None,
    sandbox_scope: str | None = None,
) -> tuple[str, dict]:
    """用给定答案续跑一轮，返回 (新 run_id, 终态 payload)。"""
    resume_payload = {
        "agent_slug": agent_slug,
        "thread_id": thread_id,
        "resume": answer,
        "created_by_run_id": run_id,
        "meta": run_meta(f"agent-e2e-{uuid.uuid4().hex}", sandbox_scope),
    }
    if model_spec:
        resume_payload["model_spec"] = model_spec
    resumed_run_id = await create_run(client, headers, base_url, resume_payload)
    return resumed_run_id, await wait_run(client, headers, base_url, resumed_run_id, timeout)


async def answer_clarifications(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    base_url: str,
    agent_slug: str,
    thread_id: str,
    run_id: str,
    payload: dict,
    clarify_reply: str | None,
    timeout: float,
    model_spec: str | None,
    max_rounds: int,
    sandbox_scope: str | None = None,
) -> tuple[str, dict, list[dict]]:
    """反复「反问 → 作答 → 续跑」，直到跑完、答不出来或到轮次上限。

    返回 (最终 run_id, 最终 payload, 逐轮明细)。明细带每轮的提问、作答与
    「追问没给选项因而未作答」的问题 ID，供补测报告如实标注未作答的题；
    各轮续跑出的 run_id 已并入调用方的 run_ids 计步，不重复落进明细。
    """
    rounds: list[dict] = []
    while len(rounds) < max_rounds and is_clarification_interrupt(payload):
        questions = await fetch_interrupt_questions(client, headers, base_url, run_id)
        if clarify_reply is not None:
            answer, unanswered = build_clarification_answer(questions, clarify_reply), []
        else:
            answer, unanswered = build_first_option_answer(questions)
        rounds.append(
            {
                "questions": [str(item.get("question") or "") for item in questions if isinstance(item, dict)],
                "answer": answer,
                "unanswered": unanswered,
                "resumed_run_id": None,
            }
        )
        if not answer:
            break
        run_id, payload = await resume_after_clarification(
            client, headers, base_url, agent_slug, thread_id, run_id, answer, timeout, model_spec, sandbox_scope
        )
        rounds[-1]["resumed_run_id"] = run_id
    return run_id, payload, rounds


async def run_one(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    base_url: str,
    agent_slug: str,
    thread_id: str,
    q: dict,
    timeout: float,
    model_spec: str | None = None,
    clarify_reply: str | None = None,
    clarify_pick_first: bool = False,
    clarify_rounds: int = 1,
    sandbox_scope: str | None = None,
) -> dict:
    request_id = f"agent-e2e-{uuid.uuid4().hex}"
    record: dict = {
        "query": q["query"],
        "gold_answer": q.get("gold_answer"),
        "gold_chunk_ids": q.get("gold_chunk_ids") or [],
        "section": q.get("section"),
        "kb_id": q.get("kb_id"),
        "thread_id": thread_id,
        "request_id": request_id,
        "model_spec": model_spec,
    }
    started = time.monotonic()
    try:
        run_id = await start_run(
            client, headers, base_url, agent_slug, thread_id, q["query"], request_id, model_spec, sandbox_scope
        )
        payload = await wait_run(client, headers, base_url, run_id, timeout)
        run_ids = [run_id]
        if clarify_reply or clarify_pick_first:
            run_id, payload, rounds = await answer_clarifications(
                client,
                headers,
                base_url,
                agent_slug,
                thread_id,
                run_id,
                payload,
                clarify_reply,
                timeout,
                model_spec,
                clarify_rounds,
                sandbox_scope,
            )
            run_ids.extend(item.pop("resumed_run_id") for item in rounds if item["resumed_run_id"])
            if rounds:
                record["clarification"] = {
                    "reply": clarify_reply,
                    "pick_first": clarify_pick_first,
                    "rounds": rounds,
                }
    except Exception as e:
        record["error"] = f"调用失败: {e}"
        record["elapsed_s"] = round(time.monotonic() - started, 1)
        return record

    record["elapsed_s"] = round(time.monotonic() - started, 1)
    record["run_id"] = run_id
    record["agent_answer"] = payload.get("output") or ""
    record["retrieved_chunks"] = []
    disposition = payload.get("knowledge_disposition")
    record["kb_scope"] = disposition.get("kb_scope") if isinstance(disposition, dict) else None
    # 拒答判定以服务端 disposition 为准（type/reason/domain），比按文案长度猜拒答可靠
    record["disposition"] = (
        {k: disposition.get(k) for k in ("type", "reason", "domain")} if isinstance(disposition, dict) else None
    )
    record["run_status"] = payload.get("status")
    if payload.get("status") != "completed":
        error = payload.get("error") or {}
        record["error"] = f"run 未完成: {payload.get('status')} {error.get('message') or error.get('type') or ''}"

    hist_resp = await client.get(f"{base_url}/api/chat/thread/{thread_id}/history", headers=headers, timeout=120.0)
    if hist_resp.status_code == 200:
        history = hist_resp.json()
        record["retrieved_chunks"] = extract_retrieved_chunks(history, run_id)
        record["steps"] = sum_run_steps(history, run_ids)
        # run 行本身不记错误原因；触顶/异常只体现在终答消息的 error_type 上（如 unexpected_error）
        record["message_error_type"] = next(
            (
                msg.get("error_type")
                for msg in history.get("history", [])
                if msg.get("run_id") == run_id and msg.get("error_type")
            ),
            None,
        )
    return record


async def run_worker(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    base_url: str,
    agent_slug: str,
    worker_index: int,
    questions: list[dict],
    timeout: float,
    emit,
    model_spec: str | None = None,
    clarify_reply: str | None = None,
    clarify_pick_first: bool = False,
    clarify_rounds: int = 1,
) -> None:
    sandbox_scope = await create_thread(
        client, headers, base_url, agent_slug, f"Agent Evaluation Sandbox #{worker_index + 1}"
    )
    for index, q in enumerate(questions):
        thread_id = await create_thread(
            client, headers, base_url, agent_slug, f"Agent Evaluation Run #{worker_index + 1}-{index + 1}"
        )
        record = await run_one(
            client,
            headers,
            base_url,
            agent_slug,
            thread_id,
            q,
            timeout,
            model_spec,
            clarify_reply,
            clarify_pick_first,
            clarify_rounds,
            sandbox_scope,
        )
        await emit(record)


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
        worker_count = max(1, min(args.concurrency, len(questions)))
        if args.clarify_pick_first:
            clarify_mode = f"自动选第一个选项，最多 {args.clarify_rounds} 轮"
        elif args.clarify_reply:
            clarify_mode = f"固定回复「{args.clarify_reply}」，最多 {args.clarify_rounds} 轮"
        else:
            clarify_mode = "关闭（中断即计失败）"
        print(
            f"登录成功，开始运行 {len(questions)} 题（agent: {args.agent_slug}，"
            f"模型: {args.model_spec or '智能体默认配置'}，worker/沙箱: {worker_count}，"
            f"澄清续跑: {clarify_mode}，"
            f"最大执行步数: {args.max_steps if args.max_steps else '保持 Agent 现有配置'}）"
        )

        original_config: dict | None = None
        if args.max_steps:
            original_config = await apply_max_steps(client, headers, args.base_url, args.agent_slug, args.max_steps)

        Path(args.output).mkdir(parents=True, exist_ok=True)
        safe_name = args.name or ""
        if not safe_name:
            from datetime import date

            safe_name = date.today().strftime("%Y%m%d")
        out = str(Path(args.output) / f"agent_e2e_{safe_name}.jsonl")

        buckets: list[list[dict]] = [[] for _ in range(worker_count)]
        for i, q in enumerate(questions):
            buckets[i % worker_count].append(q)

        done = 0
        total = len(questions)
        lock = asyncio.Lock()

        # 独占输出文件：上一次被中断的 runner 可能仍在跑（kill 掉 docker exec 客户端不会带走
        # 容器内进程），两个进程写同一路径会按各自的偏移写，产出带空洞的 JSONL 静默毁数据。
        # 抢不到锁直接退出，且加锁成功前不截断既有文件。
        writer = open(out, "a", encoding="utf-8")
        try:
            fcntl.flock(writer.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            writer.close()
            print(f"输出文件已被另一个 runner 占用：{out}", file=sys.stderr)
            return 1
        writer.truncate(0)
        writer.seek(0)

        # 边跑边写：长跑中断/崩溃时保留已完成结果，并支持实时观察进度
        with writer as f:

            async def emit(record: dict) -> None:
                nonlocal done
                async with lock:
                    f.write(json.dumps(record, ensure_ascii=False) + "\n")
                    f.flush()
                    done += 1
                    mark = "✗" if record.get("error") else "✓"
                    steps = record.get("steps") or {}
                    print(
                        f"[{done}/{total}] {mark} {record['query'][:32]} "
                        f"({record.get('elapsed_s', 0):.0f}s, {steps.get('tool_calls', 0)} tools/"
                        f"{steps.get('est_steps', 0)} steps, {len(record.get('retrieved_chunks') or [])} chunks)"
                        f"{' → ' + record['error'][:80] if record.get('error') else ''}",
                        flush=True,
                    )

            try:
                await asyncio.gather(
                    *[
                        run_worker(
                            client,
                            headers,
                            args.base_url,
                            args.agent_slug,
                            i,
                            bucket,
                            args.timeout,
                            emit,
                            args.model_spec,
                            args.clarify_reply,
                            args.clarify_pick_first,
                            args.clarify_rounds,
                        )
                        for i, bucket in enumerate(buckets)
                        if bucket
                    ]
                )
            finally:
                # 扫参临时改过 Agent 配置，无论成败都要还原，避免把实验值留给线上
                if original_config is not None:
                    await restore_config_json(client, headers, args.base_url, args.agent_slug, original_config)
                    print("已还原 Agent 原有配置")

        records = [json.loads(line) for line in Path(out).read_text(encoding="utf-8").splitlines() if line.strip()]
        ok = [r for r in records if "error" not in r]
        failed = [r for r in records if "error" in r]
        answered = [r for r in ok if (r.get("agent_answer") or "").strip()]
        print(
            f"完成：{len(ok)}/{len(records)} 成功，{len(failed)} 失败，其中 {len(answered)} 题有答案，"
            f"{sum(1 for r in ok if r['retrieved_chunks'])} 题检索到上下文"
        )
        for r in failed:
            print(f"  失败: {r['query'][:40]} → {r['error'][:120]}")

        by_type: dict[str, list[int]] = {}
        for r in ok:
            if r.get("steps"):
                key = (r.get("disposition") or {}).get("type") or "未判定"
                by_type.setdefault(key, []).append(r["steps"]["est_steps"])
        for type_name, values in sorted(by_type.items()):
            values.sort()
            print(f"  步数 {type_name}: {len(values)} 题，中位 {values[len(values) // 2]}，最大 {values[-1]}")

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
