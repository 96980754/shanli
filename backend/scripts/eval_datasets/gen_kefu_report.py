#!/usr/bin/env python3
"""生成客服知识库抽样「对比测试报告」（内部工具，供业务方参考）。

主口径 = 答案正确性（Answer Correctness，对照甲方《客服知识库》的「解决方法」标准答案）：
  系统回答与甲方标准答案分别拆事实、做对账（共同/多说/漏掉）得事实F1，再与语义相似度各取
  0.5 加权。忠实度/贴合度按业务决策不再计算。

三类结果分开呈现，主口径只算「实质作答」题：
- 实质作答：系统给出实质回答，与甲方标准答案对账计分；
- 知识库缺口拒答：系统诚实拒答「未找到相关依据」——答案在甲方客服库但不在系统知识库，
  属缺口而非答错，单列展示，不计入主口径（同时给出「含缺口按 0 计」的全口径数字，不藏数）；
- 需澄清未答：系统反问澄清（生产 Agent 真实行为），无回答，指标缺失。

输入：
  --scored  score_agent_results.py 产出的 accuracy_report_*.json（含每题
            query/gold_answer/agent_answer/ragas_metrics）
  --testset kefu{N}.jsonl（query → section 域映射）
输出：Markdown 报告（每题系统回答 vs 甲方答案 + 答案正确性、总体、分域、低分清单）。

用法（宿主机，纯读文件）：
  python3 gen_kefu_report.py \
      --scored reports/accuracy_report_kefu20_20260819.json \
      --testset kefu20.jsonl \
      --out reports/kefu20_compare_20260819.md
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

HEADLINE = "answer_correctness"
_REFUSAL_MARKERS = ("未找到", "未检索", "无相关", "没有找到", "未查询", "抱歉")


def _pct(value) -> str:
    return "-" if value is None else f"{value * 100:.1f}%"


def _fmt(text: str | None, limit: int = 200) -> str:
    t = (text or "").strip()
    return t if len(t) <= limit else t[: limit - 1] + "…"


def classify(item: dict) -> str:
    """按 Agent 回答形态分类：answered / refusal_gap / clarify_missing。"""
    a = (item.get("agent_answer") or "").strip()
    if not a:
        return "clarify_missing"
    if len(a) < 60 and any(m in a for m in _REFUSAL_MARKERS):
        return "refusal_gap"
    return "answered"


def _mean(items: list[dict], metric: str) -> float | None:
    vals = [it["ragas_metrics"].get(metric) for it in items]
    vals = [v for v in vals if v is not None]
    return sum(vals) / len(vals) if vals else None


def main() -> int:
    parser = argparse.ArgumentParser(description="生成客服库对比报告（答案正确性主口径）")
    parser.add_argument("--scored", required=True, help="score_agent_results.py 产出的 JSON 报告")
    parser.add_argument("--testset", required=True, help="kefu{N}.jsonl（query→域映射）")
    parser.add_argument("--out", default="", help="输出 md 路径")
    args = parser.parse_args()

    scored = json.loads(Path(args.scored).read_text(encoding="utf-8"))
    section_of, index_of = {}, {}
    for line in Path(args.testset).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        d = json.loads(line)
        section_of[d["query"]] = d.get("section", "")
        index_of[d["query"]] = d.get("index", 0)

    items = scored["items"]
    for it in items:
        # E2E 输出顺序与测试集不完全一致：编号/排序按测试集编号回填，不依赖评分位置
        it["index"] = index_of.get(it["query"], it.get("index") or 0)
        it["section"] = section_of.get(it["query"], "未知")
        it["cls"] = classify(it)

    answered = [it for it in items if it["cls"] == "answered"]
    refusals = [it for it in items if it["cls"] == "refusal_gap"]
    missing = [it for it in items if it["cls"] == "clarify_missing"]

    total = {m: _mean(answered, m) for m in (HEADLINE,)}
    # 全口径（含缺口拒答按 0 计，澄清题不计）：不藏数的对照口径
    all_inclusive = {
        "correct": (sum((it["ragas_metrics"].get(HEADLINE) or 0.0) for it in items)) / len(items) if items else None
    }

    domain = {}
    for it in items:
        domain.setdefault(it["section"], []).append(it)
    domain_rows = []
    for name in sorted(domain):
        lst = domain[name]
        ans = [it for it in lst if it["cls"] == "answered"]
        domain_rows.append(
            (name, len(lst), len(ans), len([it for it in lst if it["cls"] == "refusal_gap"]),
             len([it for it in lst if it["cls"] == "clarify_missing"]), _mean(ans, HEADLINE))
        )

    low = sorted(
        [it for it in answered
         if it["ragas_metrics"].get(HEADLINE) is not None and it["ragas_metrics"][HEADLINE] < 0.60],
        key=lambda it: it["ragas_metrics"][HEADLINE],
    )

    lines = [
        "# 客服知识库 20 题对比测试报告（主口径：答案正确性）",
        "",
        "> 样本：甲方《【客服】POCSTARS知识库.xlsx》按域分层抽样 20 题，问题→query、解决"
        "方法→甲方标准答案。非问答表（型号清单/错误码表/severity矩阵/话术/附件索引）与线下操作、"
        "图片流程类已排除。",
        "> 链路：生产真实 Agent 端到端作答（采集实际检索上下文）→ 评测模型逐句判定。",
        "> **摸底口径**：未接入客服库，直接用系统现有知识库作答——低分/拒答反映知识库缺口，是补齐输入。",
        "",
        "## 口径定义",
        "",
        "**主口径 = 答案正确性**（对照甲方标准答案）：系统回答与甲方「解决方法」分别拆事实、三分类对账"
        "（共同命中 / 系统多说 / 甲方提到但漏掉），得事实F1；再与向量语义相似度各取 0.5 加权。",
        "",
        "> 忠实度（有据可查）、贴合度（不跑题）已按业务决策不再计算。",
        "",
        "**测试条件（业务方指令）**：只测 6 个业务 sheet（运营平台 / 调度台 / 终端-安卓 / "
        "终端-cat1 / MDM / miniserver，培训知识库不参与）；生成的问题统一加 sheet 前缀"
        "（如「调度台-什么是下发消息」），携带模块上下文去问，降低多义澄清率。",
        "",
        "**三类结果口径**：主口径只算**实质作答**题（系统给出实质回答、可与甲方答案对账）；"
        "「知识库缺口拒答」（答案在甲方客服库、不在系统库，Agent 诚实拒答）与「需澄清未答」"
        "单列不计入，另附「含缺口按 0 计」全口径数字。",
        "",
        "## 汇总",
        "",
        "| 指标 | 值 | 说明 |",
        "| --- | --- | --- |",
        f"| **答案正确性**（实质作答题 {len(answered)}/{len(items)}） | **{_pct(total[HEADLINE])}** | 主口径 |",
        f"| 全口径（含缺口拒答按 0 计） | {_pct(all_inclusive['correct'])} | 不藏数对照 |",
        "",
        f"**20 题分解**：实质作答 **{len(answered)}** 题 · 知识库缺口拒答 **{len(refusals)}** 题 · "
        f"需澄清未答 **{len(missing)}** 题。",
        "",
        "## 分域汇总",
        "",
        "| 域 | 题数 | 实质作答 | 缺口拒答 | 需澄清 | 作答题正确性 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for name, n, ans, ref, mis, acc in domain_rows:
        lines.append(f"| {name} | {n} | {ans} | {ref} | {mis} | {_pct(acc)} |")

    lines += [
        "",
        "## 实质作答明细（每题对比）",
        "",
        "> 正确性 < 60% 标 ⚠（低分，需逐题归因）。",
        "",
        "| # | 域 | 问题 | 系统回答 | 甲方标准答案 | 正确性 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for it in sorted(answered, key=lambda x: x["index"]):
        m = it["ragas_metrics"]
        flag = "⚠" if m.get(HEADLINE) is not None and m[HEADLINE] < 0.60 else ""
        lines.append(
            f"| {it['index']} | {it['section']} | {_fmt(it['query'], 40)} | "
            f"{_fmt(it.get('agent_answer'), 60)} | {_fmt(it.get('gold_answer'), 60)} | "
            f"**{_pct(m.get(HEADLINE))}**{flag} |"
        )

    lines += [
        "",
        "## 知识库缺口题（诚实拒答，不计入主口径）",
        "",
        "> 系统回答「抱歉，在现有知识库中未找到相关依据」，抽查确认具体答案不在系统现有知识库中。"
        "这些题的答案就在甲方客服库的「解决方法」列，接入客服库即可覆盖。",
        "",
        "| # | 域 | 问题 | 系统回答 | 甲方标准答案 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for it in sorted(refusals, key=lambda x: x["index"]):
        lines.append(
            f"| {it['index']} | {it['section']} | {_fmt(it['query'], 40)} | "
            f"（诚实拒答） | {_fmt(it.get('gold_answer'), 60)} |"
        )

    lines += [
        "",
        "## 需澄清未答题（生产 Agent 真实行为）",
        "",
        "> 系统反问澄清、未直接作答（批量场景无真人回答）。属问题多义，非系统错误：",
        "",
    ]
    for it in sorted(missing, key=lambda x: x["index"]):
        lines.append(f"- #{it['index']} [{it['section']}] {it['query']}")
    if not missing:
        lines.append("- 无")

    lines += [
        "",
        "## 低分清单（实质作答、答案正确性 < 60%）",
        "",
    ]
    if not low:
        lines.append("无。")
    else:
        lines.append("| # | 域 | 问题 | 答案正确性 |")
        lines.append("| --- | --- | --- | --- |")
        for it in low:
            m = it["ragas_metrics"]
            lines.append(
                f"| {it['index']} | {it['section']} | {_fmt(it['query'], 40)} | "
                f"{_pct(m.get(HEADLINE))} |"
            )

    lines += [
        "",
        "## 说明与边界",
        "",
        "- **答案正确性依赖「标准答案」质量**：甲方的「解决方法」是人工客服口径；同一问题系统给出等价"
        "另一种表述时，对账会扣「多说/漏掉」分，属严格口径。",
        "- 20 题为分层抽样，非全量；需澄清类（批量场景无真人澄清）属 Agent 生产行为，非评测故障。",
        "",
    ]
    scored_path = Path(args.scored)
    default_name = scored_path.stem.replace("accuracy_report_", "kefu_compare_") + ".md"
    out = Path(args.out) if args.out else scored_path.with_name(default_name)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"报告已写入: {out}")
    print(f"  实质作答 {len(answered)} · 缺口拒答 {len(refusals)} · 需澄清 {len(missing)}")
    print(f"  答案正确性（实质作答题）: {_pct(total[HEADLINE])} | 全口径: {_pct(all_inclusive['correct'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
