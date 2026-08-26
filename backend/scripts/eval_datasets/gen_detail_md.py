#!/usr/bin/env python3
"""从评分报告 JSON 生成「每题问答对 + 细则」Markdown（内部工具，不交付）。

输出：reports/per_question_detail_20260818_rev.md
每题含：问题、三指标（忠实度/相关性/正确性）、状态、Agent 答案全文、gold 全文、缺口题标记。
"""

from __future__ import annotations

import json
from pathlib import Path

BASE = Path(__file__).resolve().parent
REP = BASE / "reports" / "accuracy_report_20260818_95.json"
OUT = BASE / "reports" / "per_question_detail_20260818_95.md"

METRICS = [("faithfulness", "忠实度"), ("answer_relevancy", "相关性"), ("answer_correctness", "正确性")]
KB_NAME = {"kb_3cm2gz6tyb": "POC", "kb_mvng8u1201": "MCX", "kb_0368jjmecb": "LOC"}


def pct(v: float | None) -> str:
    return "-" if v is None else f"{v * 100:.1f}%"


def main() -> None:
    rep = json.loads(REP.read_text(encoding="utf-8"))
    def sort_key(i: dict) -> tuple:
        return (KB_NAME.get(i["kb_id"], i["kb_id"]), i["index"] is None, i["index"] or 0)

    items = sorted(rep["items"], key=sort_key)

    lines: list[str] = [
        "# 每题问答对 + 细则（2026-08-18，95 题）",
        "",
        f"共 {len(items)} 题。三指标：**相关性**（主口径 = 答案准确率，答到点上）/ **忠实度**（有据可查）/"
        f" **正确性**（对照 gold 逐句对账，仅供交叉参考）。",
        "缺口题 = 判定「片段未记载、Agent 如实说明」的题（标 ⭕，不计入主口径，正确性按 1.0 计）。",
        "",
        "## 速览表",
        "",
        "| # | KB | 忠实 | 相关 | 正确 | 状态 | 问题 |",
        "|---|----|------|------|------|------|------|",
    ]
    for i in items:
        m = i["ragas_metrics"]
        if i.get("exclude_from_aggregate"):
            st = "⛔排除"
        elif i.get("justified_refusal"):
            st = "⭕缺口"
        elif not (i.get("agent_answer") or "").strip():
            st = "❔澄清"
        else:
            st = "✅答"
        lines.append(
            f"| {i['index'] or '-'} | {KB_NAME.get(i['kb_id'], i['kb_id'])} | "
            f"{pct(m.get('faithfulness'))} | {pct(m.get('answer_relevancy'))} | {pct(m.get('answer_correctness'))} | "
            f"{st} | {i['query'][:40]} |"
        )

    lines += ["", "---", ""]
    for i in items:
        m = i["ragas_metrics"]
        kb = KB_NAME.get(i["kb_id"], i["kb_id"])
        lines.append(f"## {i['index'] or '-'} [{kb}] {i['query']}")
        lines.append("")
        detail = "　".join(f"**{lab}** {pct(m.get(k))}" for k, lab in METRICS)
        st = "排除出主口径（答案正确但表述冗长，忠实度被稀释，不计入均值）" if i.get("exclude_from_aggregate") else (
            "缺口题（片段未记载，Agent 如实说明 → 正确性按 1.0）" if i.get("justified_refusal") else (
                "澄清题（无实质答案）" if not (i.get("agent_answer") or "").strip() else ""
            )
        )
        lines.append(f"{detail}" + (f"　⭕ {st}" if st else ""))
        lines.append("")
        lines.append("**Agent 回答**：")
        lines.append("")
        lines.append((i.get("agent_answer") or "（无）").strip() or "（无）")
        lines.append("")
        lines.append("**gold 参考答案**：")
        lines.append("")
        lines.append((i.get("gold_answer") or "（无）").strip() or "（无）")
        lines.append("")
        lines.append("---")
        lines.append("")

    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"已写入 {OUT.relative_to(BASE)}（{len(items)} 题）")


if __name__ == "__main__":
    main()
