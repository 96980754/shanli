#!/usr/bin/env python3
"""客服知识库「单一口径」评分：答案正确性 = 硬门槛 × (0.8×关键事实命中率 + 0.2×补充事实命中率)。

背景：业务方定稿的单一口径，替代 RAGAS answer_correctness（0.5 事实F1 + 0.5 语义相似度），
也不再做机械打分 vs RAGAS 双口径对比。每题：

    答案正确性 = gate × (0.8 × 关键事实命中率 + 0.2 × 补充事实命中率)

- gate（硬门槛，0/1）：系统给出「实质作答且切题」才为 1；缺口拒答（诚实说"未找到相关依据"）、
  反问澄清、答非所问 → 0（整题 0 分）。
- 关键事实命中率 = 甲方标准答案的「关键事实」中被系统回答命中的比例（漏了答案不成立的
  核心结论/操作步骤/关键参数）。
- 补充事实命中率 = 甲方标准答案的「补充事实」中被系统回答命中的比例（次要细节/举例/原因）。
- 关键/补充事实由评测模型（judge LLM）对照甲方标准答案拆解；命中 = 语义等价，不要求逐字。

口径（实质作答口径）：拒答/澄清统一不计分、仅记录在缺口/澄清清单；
答案正确性 = 已实质作答题目的得分均值，缺口拒答反映知识库缺口而非答错。
报告另给 实质作答/缺口拒答/需澄清 三类分解便于归因。

输入：
  --e2e       run_agent_e2e.py 产出的端到端结果 JSONL（query/gold_answer/agent_answer/section）
  --testset   测试集 JSONL（query → index/section 映射；E2E 输出顺序与测试集不一致）

输出：JSON + Markdown 报告（含每题 gate/关键补充事实命中/未命中清单、分域汇总、低分清单）。

用法（容器内，复用 yuxi 模型接线）：
  docker exec api-dev python /app/scripts/eval_datasets/score_kefu_facts.py \
      --e2e /app/scripts/eval_datasets/synthetic/agent_e2e_kefu20_prefixed_20260819.jsonl \
      --testset /app/scripts/eval_datasets/kefu20.jsonl \
      --output /app/scripts/eval_datasets/reports \
      --name kefu20_prefixed_20260819
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
from pathlib import Path

DEFAULT_JUDGE = "deepseek:deepseek-v4-flash"
DEFAULT_OUTPUT = "/app/scripts/eval_datasets/reports"
_REFUSAL_MARKERS = ("未找到", "未检索", "无相关", "没有找到", "未查询", "抱歉")
_CLARIFY_WORDS = ("反问", "澄清", "补充", "请提供", "请补充")


def build_prompt(question: str, gold: str, answer: str) -> str:
    """构造 judge 单轮评分 prompt：gate + 关键/补充事实拆解 + 逐条命中判定，输出严格 JSON。"""
    return f"""你是客服知识库问答的客观评分员。给定问题、甲方标准答案和系统回答，评估「答案正确性」。
评估分三步，最后只输出一个 JSON 对象。

【问题】
{question}

【甲方标准答案】（评分基准，事实全部从此拆解）
{gold}

【系统回答】（被测）
{answer}

第一步 · 硬门槛判定 gate（只取 pass / fail，不要中间值）：
系统回答是否为「实质作答且切题」？
- 若系统回答为空、为拒答（如"抱歉，未找到相关依据"）、反问澄清、或明显答非所问
  （主题/入口与问题无关）→ gate 应为 "fail"；
- 否则 → "pass"。

第二步 · 从甲方标准答案拆解事实点：
把【甲方标准答案】拆成若干独立事实点，逐条标注：
- "关键事实"：必须命中的核心结论/操作步骤/关键参数，缺它答案不成立；
- "补充事实"：次要细节/举例/原因/额外说明。
每条事实一句话、尽量完整、不要拆分过细；只从甲方标准答案拆，不要引入标准答案之外的内容。

第三步 · 逐条判定命中：
对每条事实，判断它是否在【系统回答】中出现：语义等价即命中（不要求逐字一致）；
系统回答明确矛盾或缺失算未命中。

只输出如下 JSON（不要任何其他文字、不要 Markdown 代码块）：
{{
  "gate": "pass"或"fail",
  "gate_reason": "一句话说明硬门槛判定依据",
  "key_facts": {{"total": 关键事实总数, "hit": 命中的关键事实数}},
  "supp_facts": {{"total": 补充事实总数, "hit": 命中的补充事实数}},
  "key_missed": ["未命中的关键事实（每条≤30字）"],
  "supp_missed": ["未命中的补充事实（每条≤30字）"]
}}"""


def extract_json(text: str) -> dict:
    """从 judge 输出中稳健抽取 JSON 对象（容忍 Markdown 代码块包裹/前后缀文字）。"""
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*", "", t)
        t = re.sub(r"\s*```$", "", t)
    start, end = t.find("{"), t.rfind("}")
    if start == -1 or end <= start:
        raise ValueError(f"输出中没有 JSON 对象: {t[:120]}")
    return json.loads(t[start : end + 1])


def parse_judge(text: str) -> dict:
    """校验 judge 返回，规整成 {gate, gate_reason, key_facts, supp_facts, key_missed, supp_missed}。"""
    raw = extract_json(text)
    gate = raw.get("gate")
    if gate not in ("pass", "fail"):
        raise ValueError(f"gate 非法: {gate!r}")
    kf = raw.get("key_facts") or {}
    sf = raw.get("supp_facts") or {}
    for name, facts in (("key_facts", kf), ("supp_facts", sf)):
        if not isinstance(facts, dict) or not isinstance(facts.get("total"), int) or not isinstance(
            facts.get("hit"), int
        ):
            raise ValueError(f"{name} 结构非法: {facts!r}")
    return {
        "gate": gate,
        "gate_reason": str(raw.get("gate_reason") or ""),
        "key_facts": {"total": kf["total"], "hit": kf["hit"]},
        "supp_facts": {"total": sf["total"], "hit": sf["hit"]},
        "key_missed": [str(x) for x in raw.get("key_missed") or []],
        "supp_missed": [str(x) for x in raw.get("supp_missed") or []],
    }


def classify(record: dict, judge: dict | None = None) -> str:
    """按 Agent 回答形态 + judge 硬门槛分类：answered / refusal_gap / clarify_missing / e2e_error。

    gate=fail 时以 judge 归因为准（拒答/未找到依据 → refusal_gap，反问澄清 → clarify_missing），
    不再受回答长度限制——修复长回答拒答（开头声明「知识库中未找到…」）被误判为实质作答的问题；
    短回答含拒答标记保留为无 judge 时的兜底。
    """
    err = record.get("error") or ""
    if "ask_user_question_required" in err:
        return "clarify_missing"
    if err:
        return "e2e_error"
    a = (record.get("agent_answer") or "").strip()
    if not a:
        return "clarify_missing"
    if judge and judge["gate"] == "fail":
        reason = judge.get("gate_reason") or ""
        if any(w in reason for w in _CLARIFY_WORDS):
            return "clarify_missing"
        return "refusal_gap"
    if len(a) < 60 and any(m in a for m in _REFUSAL_MARKERS):
        return "refusal_gap"
    return "answered"


def score_of(parsed: dict) -> float:
    """答案正确性 = gate × (0.8 × 关键事实命中率 + 0.2 × 补充事实命中率)。"""
    gate = 1.0 if parsed["gate"] == "pass" else 0.0
    key = parsed["key_facts"]
    supp = parsed["supp_facts"]
    key_rate = key["hit"] / key["total"] if key["total"] else 1.0
    supp_rate = supp["hit"] / supp["total"] if supp["total"] else 1.0
    return gate * (0.8 * key_rate + 0.2 * supp_rate)


async def score_one(llm, record: dict, semaphore: asyncio.Semaphore, max_tokens: int) -> dict:
    """对单题调 judge 打分；无实质回答（空答案）直接 gate=0，不浪费 judge 调用。"""
    answer = (record.get("agent_answer") or "").strip()
    if not answer:
        return {"score": 0.0, "gate": "fail", "gate_reason": "系统无实质回答（拒答/需澄清未答）",
                "key_facts": {"total": 0, "hit": 0}, "supp_facts": {"total": 0, "hit": 0},
                "key_missed": [], "supp_missed": [], "judge_error": None}
    async with semaphore:
        prompt = build_prompt(record["query"], record.get("gold_answer") or "", answer)
        last_err: Exception | None = None
        for attempt in range(2):
            try:
                # 显式超时：曾出现 deepseek 调用偶发挂起无响应，无超时会导致整轮评分卡死
                resp = await asyncio.wait_for(llm.ainvoke(prompt), timeout=120)
                parsed = parse_judge(str(resp.content))
                parsed = parse_judge(str(resp.content))
                parsed["score"] = score_of(parsed)
                parsed["judge_error"] = None
                return parsed
            except Exception as e:  # JSON 解析/结构非法等：补一句提示重试一次
                last_err = e
                if attempt == 0:
                    prompt = prompt + "\n\n（上一次输出无法解析为指定 JSON，请严格只输出指定 JSON 对象。）"
        return {"score": None, "gate": "fail", "gate_reason": "",
                "key_facts": {"total": 0, "hit": 0}, "supp_facts": {"total": 0, "hit": 0},
                "key_missed": [], "supp_missed": [], "judge_error": str(last_err)}


def _pct(value) -> str:
    return "-" if value is None else f"{value * 100:.1f}%"


def _fmt(text: str | None, limit: int = 40) -> str:
    t = (text or "").strip()
    return t if len(t) <= limit else t[: limit - 1] + "…"


def _mean(vals: list[float]) -> float | None:
    vals = [v for v in vals if v is not None]
    return sum(vals) / len(vals) if vals else None


def build_report(data: dict, md_path: Path, json_path: Path, domain: str = "") -> None:
    """domain 为空时用客服库专属文案；非空（如 MCX+定位产品）用通用文案避免误导。"""
    items = data["items"]
    overall = data["overall"]
    domains = {}
    for it in items:
        domains.setdefault(it["section"], []).append(it)

    if domain:
        sample = (
            f"> 样本：{domain} {len(items)} 题（来自甲方问答表，问题统一加分区前缀，携带模块上下文去问）；\n"
            "> 摸底口径：未导入新表，直接用系统现有知识库作答——拒答反映知识库缺口，是补齐输入。"
        )
        gap_note = "> 这些题的答案在甲方问答表中、但不在系统现有知识库——接入后即可覆盖。"
        answer_note = "甲方的问答表答案是人工客服口径"
        gap_cause = "摸底低分由「未导入新表」主导，接入后缺口题转为可作答"
        title = f"# {domain} {len(items)} 题「答案正确性」评分报告（实质作答口径）"
    else:
        sample = (
            "> 样本：6 个业务 sheet（运营平台/调度台/终端-安卓/终端-cat1/MDM/miniserver，"
            f"培训知识库不参与）分层抽样 {len(items)} 题；\n"
            "> 生成的问题统一加 sheet 前缀（如「调度台-什么是下发消息」），携带模块上下文去问。\n"
            "> 摸底口径：未接入客服库，直接用系统现有知识库作答——拒答反映知识库缺口，是补齐输入。"
        )
        gap_note = "> 这些题的答案就在甲方客服库的「解决方法」列、但不在系统现有知识库——接入客服库即可覆盖。"
        answer_note = "甲方的「解决方法」是人工客服口径"
        gap_cause = "摸底低分由「未接入客服库」主导，接入后缺口题转为可作答"
        title = f"# 客服知识库 {len(items)} 题「答案正确性」评分报告（实质作答口径）"

    lines = [
        title,
        "",
        sample,
        "",
        "## 口径定义（实质作答口径）",
        "",
        "**答案正确性 = 硬门槛 × (0.8 × 关键事实命中率 + 0.2 × 补充事实命中率)**",
        "",
        "- **硬门槛（0/1）**：系统给出实质作答且切题才为 1；缺口拒答（诚实说「未找到相关依据」）、"
        "反问澄清、答非所问 → 0（整题 0 分）。",
        "- **关键事实命中率** = 甲方标准答案的「关键事实」中被系统回答命中的比例"
        "（核心结论/操作步骤/关键参数，漏了答案不成立）。",
        "- **补充事实命中率** = 甲方标准答案的「补充事实」中被系统回答命中的比例（次要细节/举例/原因）。",
        "- 关键/补充事实由评测模型对照甲方标准答案拆解；命中 = 语义等价，不要求逐字。",
        "",
        "**实质作答口径**：答案正确性 = 已实质作答题目的得分均值；拒答/澄清统一不计分、"
        "仅记录在下方缺口/澄清清单，缺口拒答反映知识库缺口而非答错。",
        "",
        "## 汇总",
        "",
        "| 指标 | 值 |",
        "| --- | --- |",
        f"| **答案正确性**（{overall['answered']} 题实质作答口径，拒答/澄清不计分仅记录） | **{_pct(overall['mean'])}** |",
        f"| 实质作答 {overall['answered']} 题 · 缺口拒答 {overall['refusal_gap']} 题 · "
        f"需澄清 {overall['clarify_missing']} 题 · 评测失败 {overall['judge_error']} 题 | 分类归因 |",
        "",
        "## 分域汇总",
        "",
        "| 域 | 题数 | 答案正确性 | 实质作答 | 缺口拒答 | 需澄清 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for name in sorted(domains):
        lst = domains[name]
        rows = [it for it in lst if it["score"] is not None and it["cls"] == "answered"]
        lines.append(
            f"| {name} | {len(lst)} | {_pct(_mean([it['score'] for it in rows]))} | "
            f"{sum(1 for it in lst if it['cls'] == 'answered')} | "
            f"{sum(1 for it in lst if it['cls'] == 'refusal_gap')} | "
            f"{sum(1 for it in lst if it['cls'] == 'clarify_missing')} |"
        )

    lines += [
        "",
        "## 每题明细（gate + 事实命中）",
        "",
        "> 正确性 < 60% 标 ⚠（低分，需逐题归因）。",
        "",
        "| # | 域 | 问题 | 硬门槛 | 关键事实(命中/总) | 补充事实(命中/总) | 答案正确性 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for it in sorted(items, key=lambda x: x["index"]):
        key = it["key_facts"]
        supp = it["supp_facts"]
        flag = "⚠" if it["score"] is not None and it["score"] < 0.60 else ""
        lines.append(
            f"| {it['index']} | {it['section']} | {_fmt(it['query'], 36)} | "
            f"{'通过' if it['gate'] == 'pass' else '不通过'} | {key['hit']}/{key['total']} | "
            f"{supp['hit']}/{supp['total']} | **{_pct(it['score'])}**{flag} |"
        )

    lines += [
        "",
        "## 低分清单（答案正确性 < 60%，附未命中的关键事实）",
        "",
    ]
    low = sorted(
        [it for it in items if it["score"] is not None and it["score"] < 0.60 and it["cls"] == "answered"],
        key=lambda it: it["score"],
    )
    if not low:
        lines.append("无。")
    else:
        lines.append("| # | 域 | 问题 | 答案正确性 | 未命中的关键事实 |")
        lines.append("| --- | --- | --- | --- | --- |")
        for it in low:
            missed = "；".join(it["key_missed"][:4]) or "（gate 不通过）"
            lines.append(
                f"| {it['index']} | {it['section']} | {_fmt(it['query'], 32)} | "
                f"{_pct(it['score'])} | {_fmt(missed, 56)} |"
            )

    lines += [
        "",
        "## 缺口拒答题（诚实拒答，gate 不通过按 0 计）",
        "",
        gap_note,
        "",
        "| # | 域 | 问题 | 甲方标准答案 |",
        "| --- | --- | --- | --- |",
    ]
    for it in sorted([it for it in items if it["cls"] == "refusal_gap"], key=lambda x: x["index"]):
        lines.append(f"| {it['index']} | {it['section']} | {_fmt(it['query'], 36)} | {_fmt(it['gold_answer'], 48)} |")

    lines += [
        "",
        "## 说明与边界",
        "",
        f"- **答案正确性依赖「标准答案」质量**：{answer_note}；关键/补充事实由评测模型"
        "拆解，属自动化近似，非甲方标注。",
        "- 实质作答口径：主指标只统计已实质作答的题目，拒答/澄清不计分、仅记录在缺口/澄清清单；"
        f"{gap_cause}。",
        f"- judge 模型：{data['judge_llm']}。",
        "",
    ]
    json_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text("\n".join(lines), encoding="utf-8")
    json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="客服知识库单一口径评分（答案正确性 = 硬门槛×(0.8×关键+0.2×补充)）")
    parser.add_argument("--e2e", required=True, help="run_agent_e2e.py 输出的 JSONL")
    parser.add_argument("--testset", required=True, help="kefu{N}.jsonl（query→index/section 映射）")
    parser.add_argument("--judge-llm", default=DEFAULT_JUDGE, help="judge 模型 spec")
    parser.add_argument("--max-tokens", type=int, default=16384, help="judge LLM 输出上限")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="报告输出目录")
    parser.add_argument("--name", required=True, help="报告文件名/运行名，如 kefu20_prefixed_20260819")
    parser.add_argument("--domain", default="", help="报告域名称；非空（如 MCX+定位产品）时用通用文案替代客服库文案")
    args = parser.parse_args()

    index_of, section_of = {}, {}
    for line in Path(args.testset).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        d = json.loads(line)
        index_of[d["query"]] = d.get("index", 0)
        section_of[d["query"]] = d.get("section", "")

    records = []
    for line in Path(args.e2e).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            records.append(json.loads(line))

    from yuxi.agents.models import load_chat_model

    llm = load_chat_model(args.judge_llm, max_tokens=args.max_tokens)
    semaphore = asyncio.Semaphore(max(1, args.concurrency))

    async def run() -> int:
        items = []
        for i, rec in enumerate(records, 1):
            judge = await score_one(llm, rec, semaphore, args.max_tokens)
            items.append(
                {
                    "index": index_of.get(rec["query"], rec.get("index") or 0),
                    "section": section_of.get(rec["query"], rec.get("section") or "未知"),
                    "query": rec["query"],
                    "gold_answer": rec.get("gold_answer") or "",
                    "agent_answer": rec.get("agent_answer") or "",
                    "cls": classify(rec, judge),
                    **judge,
                }
            )
            # 逐题进度日志：实时观察推进速度，便于定位 judge 偶发挂起
            if i % 5 == 0 or i == len(records):
                print(f"[score] {i}/{len(records)} 题完成", flush=True)
        return items

    items = asyncio.run(run())

    scored = [it for it in items if it["score"] is not None]
    answered_items = [it for it in items if it["cls"] == "answered"]
    overall = {
        "mean": _mean([it["score"] for it in answered_items]),
        "scored": len(scored),
        "answered": sum(1 for it in items if it["cls"] == "answered"),
        "refusal_gap": sum(1 for it in items if it["cls"] == "refusal_gap"),
        "clarify_missing": sum(1 for it in items if it["cls"] == "clarify_missing"),
        "e2e_error": sum(1 for it in items if it["cls"] == "e2e_error"),
        "judge_error": sum(1 for it in items if it["judge_error"]),
    }

    name = args.name
    data = {
        "run_name": name,
        "formula": "答案正确性 = 硬门槛 × (0.8 × 关键事实命中率 + 0.2 × 补充事实命中率)",
        "judge_llm": args.judge_llm,
        "overall": overall,
        "items": items,
    }
    json_path = Path(args.output) / f"kefu_facts_{name}.json"
    md_path = Path(args.output) / f"kefu_facts_{name}.md"
    build_report(data, md_path, json_path, domain=args.domain)

    print(f"judge 模型: {args.judge_llm} | 指标: 实质作答口径（硬门槛×(0.8×关键+0.2×补充)）")
    print(f"{name}: 评分完成 {len(scored)}/{len(items)} 题（{overall['judge_error']} 题评测失败）")
    print(
        f"分类: 实质作答 {overall['answered']} · 缺口拒答 {overall['refusal_gap']} · "
        f"需澄清 {overall['clarify_missing']} · E2E 异常 {overall['e2e_error']}"
    )
    print(f"答案正确性（{overall['answered']} 题实质作答口径，拒答/澄清不计分仅记录）: {_pct(overall['mean'])}")
    print(f"报告已写入:\n  {json_path}\n  {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
