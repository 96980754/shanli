#!/usr/bin/env python3
"""把「甲方评价为勉强可接受/不可接受」的 74 题，做成补测前后的回答对比文档。

每题给出三版系统答案，来源各不相同：
  - 甲方记录版：甲方《二期准确率测试.xlsx》「系统答案」列，即甲方给出该评价时看到的版本；
  - 8/20 批次：本次交付口径（引用准确率 98.1%）所依据的那一次跑批；
  - 9/28 复测：本轮补测。

甲方工作簿在 docs/ 下、容器未挂载该目录，故本脚本在宿主机运行；产物写入 reports/ 需要 root，
命令里先落到 /tmp 再 docker cp 进容器（见文末用法）。

用法：
    python3 backend/scripts/eval_datasets/export_retest_compare.py \
        --workbook docs/二期准确率测试.xlsx \
        --testset  backend/scripts/eval_datasets/retest_aip_20260928.jsonl \
        --ratings  backend/scripts/eval_datasets/aip_ratings.jsonl \
        --results  backend/scripts/eval_datasets/synthetic/agent_e2e_citation507_20260820.jsonl \
        --retest   backend/scripts/eval_datasets/synthetic/agent_e2e_retest_aip_20260928.jsonl \
        --retest-report backend/scripts/eval_datasets/reports/citation_accuracy_retest_aip_20260928.json \
        --output /tmp/补测前后回答对比.md
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from openpyxl import load_workbook

# 甲方工作簿里含「系统答案」列的分页：(分页名, 问题列, 系统答案列, 起始行)
WORKBOOK_SHEETS = [
    ("一期数据+复议", 2, 5, 2),
    ("MCX", 4, 6, 3),
    ("CAT1", 4, 6, 3),
    ("安卓", 4, 6, 3),
    ("MCX复议", 4, 6, 2),
    ("终端复议", 4, 6, 2),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="补测前后回答对比文档")
    parser.add_argument("--workbook", required=True, help="甲方《二期准确率测试.xlsx》")
    parser.add_argument("--testset", required=True, help="复测题集 JSONL（提供 gold 与题序）")
    parser.add_argument("--ratings", required=True, help="每行 {query, aip_rating} 的 JSONL")
    parser.add_argument("--results", required=True, help="8/20 交付批次产物 JSONL")
    parser.add_argument("--retest", required=True, help="9/28 复测产物 JSONL")
    parser.add_argument("--retest-report", required=True, help="9/28 复测评分报告 JSON")
    parser.add_argument("--output", required=True, help="输出 Markdown 路径")
    return parser.parse_args()


def load_jsonl(path: str) -> list[dict]:
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def load_workbook_answers(path: str) -> dict[str, str]:
    """甲方工作簿「系统答案」列 → {问题: 答案}，同名问题取首次出现。"""
    book = load_workbook(path, data_only=True)
    answers: dict[str, str] = {}
    for sheet_name, q_col, a_col, start_row in WORKBOOK_SHEETS:
        sheet = book[sheet_name]
        for row in range(start_row, sheet.max_row + 1):
            query = sheet.cell(row=row, column=q_col).value
            answer = sheet.cell(row=row, column=a_col).value
            if query:
                answers.setdefault(str(query).strip(), str(answer or "").strip())
    return answers


CLARIFY_ANSWER = "（本轮系统反问澄清，未直接作答；该线程残留的上一题答案已剔除，不计入引用判定）"


def retest_answer(record: dict) -> str:
    """复测答案的展示文本。

    run_status 非 completed 的题，记录里的正文可能是同一线程上一轮的残留
    （详见《引用准确率Hit5验收说明》5.6），一律不展示、不判定。
    """
    if record.get("run_status") != "completed":
        return CLARIFY_ANSWER
    return record.get("agent_answer") or ""


def verdict_text(item: dict | None, record: dict) -> str:
    """复测结论一行话：未作答 / 无引用标注 / 命中第 N 条 / 未命中。"""
    if record.get("run_status") != "completed":
        return "系统反问澄清，未直接作答（不计入分母）"
    if not item or not item.get("entries"):
        if item and item.get("unresolved_citation"):
            return "有引用标注，但对不上本轮检索片段（不计入分母）"
        return "答案没有引用标注（不计入分母）"
    if item.get("hit"):
        return f"引用正确（命中第 {item.get('hit_position')} 条）"
    reasons = "；".join((entry.get("reason") or "") for entry in item["entries"])
    return f"引用不正确 —— {reasons}"


def block(title: str, note: str, text: str) -> str:
    body = (text or "（无）").strip()
    return f"**{title}**{f'（{note}）' if note else ''}\n\n{body}\n"


def render_summary(rows: list[dict]) -> str:
    lines = [
        "## 一、汇总",
        "",
        "| # | 分区 | 问题 | 甲方评价 | 复测结论 |",
        "|---|---|---|---|---|",
    ]
    for index, row in enumerate(rows, 1):
        verdict = row["verdict"].split(" —— ")[0]
        lines.append(f"| {index} | {row['section']} | {row['query']} | {row['rating']} | {verdict} |")
    hit = sum(1 for row in rows if row["verdict"].startswith("引用正确"))
    judged = sum(
        1 for row in rows if not row["verdict"].startswith(("系统反问澄清", "答案没有引用标注", "有引用标注，但对不上"))
    )
    lines += [
        "",
        f"复测 74 题：可判（含可归因引用标注）{judged} 题，引用正确 {hit} 题，引用准确率 {100.0 * hit / judged:.1f}%。",
        "",
    ]
    return "\n".join(lines)


def render_body(rows: list[dict]) -> str:
    parts = ["## 二、逐题对比", ""]
    for index, row in enumerate(rows, 1):
        parts += [
            f"### {index}. [{row['section']}] {row['query']}",
            "",
            f"- **甲方评价**：{row['rating']}",
            f"- **复测结论**：{row['verdict']}",
            "",
            block("甲方标准答案", "", row["gold"]),
            block("补测前 ① 甲方记录版", "甲方《二期准确率测试》表中「系统答案」列，甲方据此给出上述评价", row["aip"]),
            block("补测前 ② 8/20 批次", "本次交付口径（引用准确率 98.1%）所依据的跑批", row["old"]),
            block("补测后 2026-09-28 复测", "", row["new"]),
            "---",
            "",
        ]
    return "\n".join(parts)


def render(rows: list[dict], counters: dict) -> str:
    head = [
        "# 补测前后回答对比（74 题）",
        "",
        "对比对象：甲方评价为「勉强可接受」（45 题）与「不可接受」（29 题）的题目。",
        "每题给出三版系统答案，各自的来源：",
        "",
        "| 版本 | 来源 | 说明 |",
        "|---|---|---|",
        "| 补测前 ① 甲方记录版 | 甲方《二期准确率测试.xlsx》「系统答案」列 | 甲方给出该评价时看到的版本 |",
        "| 补测前 ② 8/20 批次 | `synthetic/agent_e2e_citation507_20260820.jsonl` | 交付口径（98.1%）依据的跑批 |",
        "| 补测后 | `synthetic/agent_e2e_retest_aip_20260928.jsonl` | 2026-09-28 在当前系统上的复测 |",
        "",
        "**三版不是同一次跑批，读数时请注意：**",
        "",
        f"- 8/20 批次是唯一依据 `客服知识库`（`kb_2ak3wcz9wx`）作答的一次：这 74 题当时命中该库 "
        f"{counters['old_kb_hits']} 条片段（占该批片段总数 {counters['old_kb_ratio']:.0%}），"
        f"答案中有 {counters['old_mentions_kb']} 题在引用段点名该库。"
        "该库在当前平台的知识库列表中已不存在，复测时命中 0 条。",
        "- 甲方记录版与本次复测**同源**：都不引用 `客服知识库`，改从产品资料库（poc-资料、MDM 操作说明书等）"
        f"作答（提到该库的题数：甲方记录版 {counters['aip_mentions_kb']}、复测 {counters['new_mentions_kb']}）。",
        "- 三版两两之间答案文字完全一致的题数均为 0（甲方记录版 vs 8/20、甲方记录版 vs 复测、复测 vs 8/20），"
        "说明甲方记录版既不是 8/20 那批、也不是本次复测，而是另一次跑批的留档。",
        "",
        f"复测整体结果：系统反问澄清未作答 {counters['clarify']} 题、有作答 {counters['answered']} 题，"
        f"其中含可归因引用标注 {counters['judged']} 题、引用正确 {counters['hit']} 题，"
        f"**引用准确率 {100.0 * counters['hit'] / counters['judged']:.1f}%**。",
        "",
        "原因见《引用准确率Hit5验收说明》5.6：这 74 题的标准答案取自客服口径的知识库，该库现已不在系统中，"
        "复测时系统改从产品手册/白皮书作答，引用指向的正是它本轮实际读到并据以作答的那份文档，"
        "表述与标准答案不同，逐条判定因此判为「不支持」。",
        "",
        "",
    ]
    return "\n".join(head) + render_summary(rows) + render_body(rows)


def main() -> int:
    args = parse_args()
    testset = load_jsonl(args.testset)
    ratings = {record["query"]: record.get("aip_rating") or "" for record in load_jsonl(args.ratings)}
    old = {record["query"]: record for record in load_jsonl(args.results)}
    new = {record["query"]: record for record in load_jsonl(args.retest)}
    aip = load_workbook_answers(args.workbook)
    report = json.loads(Path(args.retest_report).read_text(encoding="utf-8"))
    items = {item["query"]: item for item in report["items"]}

    rows = []
    for entry in testset:
        query = entry["query"]
        record = new[query]
        rows.append(
            {
                "section": entry.get("section") or "",
                "query": query,
                "rating": ratings.get(query, ""),
                "gold": entry.get("gold_answer") or "",
                "aip": aip.get(query, ""),
                "old": (old.get(query) or {}).get("agent_answer") or "",
                "new": retest_answer(record),
                "new_raw": record.get("agent_answer") or "",
                "verdict": verdict_text(items.get(query), record),
            }
        )

    old_kb_hits = sum(
        1
        for row in rows
        for chunk in ((old.get(row["query"]) or {}).get("retrieved_chunks") or [])
        if chunk.get("kb_id") == "kb_2ak3wcz9wx"
    )
    old_total = sum(len((old.get(row["query"]) or {}).get("retrieved_chunks") or []) for row in rows)

    counters = {
        "aip_mentions_kb": sum(1 for row in rows if "客服知识库" in row["aip"]),
        "old_mentions_kb": sum(1 for row in rows if "客服知识库" in row["old"]),
        "new_mentions_kb": sum(1 for row in rows if "客服知识库" in row["new_raw"]),
        "old_kb_hits": old_kb_hits,
        "old_kb_ratio": old_kb_hits / old_total if old_total else 0.0,
        "clarify": sum(1 for row in rows if row["verdict"].startswith("系统反问澄清")),
        "judged": sum(
            1
            for row in rows
            if not row["verdict"].startswith(("系统反问澄清", "答案没有引用标注", "有引用标注，但对不上"))
        ),
        "hit": sum(1 for row in rows if row["verdict"].startswith("引用正确")),
    }
    counters["answered"] = len(rows) - counters["clarify"]

    output = Path(args.output)
    output.write_text(render(rows, counters), encoding="utf-8")
    print(f"74 题对比文档已写入 {output}（复测可判 {counters['judged']} 题，命中 {counters['hit']} 题）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
