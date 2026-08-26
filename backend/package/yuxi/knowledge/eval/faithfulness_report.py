"""忠实度（答案正确率）汇报报告模块（内部，不交付甲方）。

复用 RAGAS 评估链路（run_ragas_evaluation）的多知识库结果，合并后生成管理向
「答案正确率（忠实度）」汇报报告（Markdown + JSON）。主指标 = RAGAS Faithfulness，
内部双指标 = Faithfulness + Answer Relevancy。

模块级不 import ragas，可在单元测试与客户端镜像中干净导入；所有输入均为普通 dict，
纯函数可单测。

口径说明：本报告「答案正确率」= RAGAS Faithfulness（忠实度）——逐条检查回答中的每个
陈述是否都能在本次检索到的知识库上下文中找到依据（有据可查）。它与合同验收指标
（引用正确率 ≥95%、答案可接受准确率 ≥80-90%）口径不同，不替代合同验收结论。
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

REPORT_DISCLAIMER = (
    "本报告为内部质量评估（不交付甲方），基于简化评估链路（复用 evaluate_question），"
    "不代表生产 Agent 最终表现。"
)

CONTRACT_DISCLAIMER = (
    "本报告「答案正确率」为内部质量口径（RAGAS Faithfulness / 忠实度）：逐条检查回答中的每个陈述"
    "是否都能在本次检索到的知识库上下文中找到依据（有据可查）。它**不是**《AI知识库软件开发合同终稿》"
    "中的验收指标——合同验收指标为：答案可接受准确率 ≥ 80%（一期 ≥90%，三期 ≥95%）、引用正确率 ≥ 95%。"
    "两者口径、判定方法与数据来源不同，本报告不得用于替代合同验收结论。"
)

HEADLINE_METRIC = "faithfulness"
DUAL_METRIC = "answer_relevancy"

METRIC_LABELS = {
    "faithfulness": "忠实度（答案正确率）",
    "answer_relevancy": "答案相关性（Answer Relevancy）",
    "context_precision": "上下文精确率",
    "context_recall": "上下文召回率",
    "answer_correctness": "答案正确性",
}


def _mean(items: list[dict[str, Any]], metric_name: str) -> float | None:
    """items 中某 metric 非 None 值的算术平均；无有效值时返回 None。

    标记 exclude_from_aggregate 的题（如表述冗长导致忠实度结构性偏低，经业务方确认）
    不计入主口径均值，但保留在明细中展示。
    """
    values = [
        item["ragas_metrics"][metric_name]
        for item in items
        if not item.get("exclude_from_aggregate") and item["ragas_metrics"].get(metric_name) is not None
    ]
    if not values:
        return None
    return sum(values) / len(values)


def combine_results(segments: list[dict[str, Any]]) -> dict[str, Any]:
    """合并多知识库 run_ragas_evaluation 结果。

    segments: [{"kb_id", "dataset", "results": {"metrics": {...}, "items": [...]}}, ...]

    每个 metric 的合并均值 = 该 metric 在所有题中非 None 值的算术平均；
    metric_counts[name] = 该 metric 非 None 的题目数，缺失数 = total_items - metric_counts[name]。
    分库 metrics 从该库 items 重算（不信任入参 results["metrics"]），合成 fixture 与真实结果一致。

    返回：
      {
        "metrics": {name: float|None, ...},
        "metric_counts": {name: int, ...},
        "total_items": int,
        "kbs": [{"kb_id", "dataset", "count", "metrics": {...}}, ...],
        "items": [{kb_id, dataset, index, query, ragas_metrics, answer_scores, retrieval_scores}, ...],
      }
    """
    metric_names: list[str] = []
    seen: set[str] = set()
    combined_items: list[dict[str, Any]] = []
    kbs: list[dict[str, Any]] = []

    for seg in segments:
        items = seg["results"].get("items", [])
        tagged: list[dict[str, Any]] = []
        for item in items:
            tagged_item = dict(item)
            tagged_item["kb_id"] = seg["kb_id"]
            tagged_item["dataset"] = seg["dataset"]
            tagged.append(tagged_item)
            for name in tagged_item.get("ragas_metrics", {}):
                if name not in seen:
                    seen.add(name)
                    metric_names.append(name)
        combined_items.extend(tagged)
        kb_metrics = {name: _mean(tagged, name) for name in metric_names}
        kbs.append({"kb_id": seg["kb_id"], "dataset": seg["dataset"], "count": len(tagged), "metrics": kb_metrics})

    metrics = {name: _mean(combined_items, name) for name in metric_names}
    # 排除出主口径的题不计入 metric_counts（"X/N 题有值"的 X 与主口径一致）
    metric_counts = {
        name: sum(
            1
            for item in combined_items
            if not item.get("exclude_from_aggregate") and item["ragas_metrics"].get(name) is not None
        )
        for name in metric_names
    }
    return {
        "metrics": metrics,
        "metric_counts": metric_counts,
        "total_items": len(combined_items),
        "kbs": kbs,
        "items": combined_items,
    }


def build_faithfulness_json_report(
    combined: dict[str, Any], *, run_name: str, threshold: float = 0.70
) -> dict[str, Any]:
    """组装忠实度汇报 JSON 报告。"""
    headline = combined["metrics"].get(HEADLINE_METRIC)
    low = sorted(
        (
            {
                "kb_id": item["kb_id"],
                "dataset": item["dataset"],
                "index": item["index"],
                "query": item["query"],
                "faithfulness": item["ragas_metrics"].get(HEADLINE_METRIC),
            }
            for item in combined["items"]
            if item["ragas_metrics"].get(HEADLINE_METRIC) is not None
            and item["ragas_metrics"][HEADLINE_METRIC] < threshold
        ),
        key=lambda row: row["faithfulness"],
    )
    return {
        "run_name": run_name,
        "disclaimer": REPORT_DISCLAIMER,
        "contract_disclaimer": CONTRACT_DISCLAIMER,
        "threshold": threshold,
        "headline_metric": HEADLINE_METRIC,
        "metrics": dict(combined["metrics"]),
        "metric_counts": dict(combined["metric_counts"]),
        "passed": headline is not None and headline >= threshold,
        "total_items": combined["total_items"],
        "kbs": combined["kbs"],
        "items": combined["items"],
        "low_faithfulness": low,
    }


def _pct(value: float | None) -> str:
    return "-" if value is None else f"{value * 100:.1f}%"


def _status(value: float | None, threshold: float) -> str:
    if value is None:
        return "数据缺失"
    return "达标" if value >= threshold else "未达标"


def _metric_label(name: str) -> str:
    return METRIC_LABELS.get(name, name)


def build_faithfulness_markdown_report(
    combined: dict[str, Any], *, run_name: str, threshold: float = 0.70
) -> str:
    """渲染忠实度汇报 Markdown 报告：口径定义 + 汇总 + 分库 + 明细 + 低分清单 + 结论。"""
    metrics = combined["metrics"]
    headline = metrics.get(HEADLINE_METRIC)
    relevancy = metrics.get(DUAL_METRIC)
    total = combined["total_items"]
    kb_summary = " / ".join(f"{kb['kb_id']} {kb['count']} 题" for kb in combined["kbs"])
    headline_count = combined["metric_counts"].get(HEADLINE_METRIC, 0)
    missing = total - headline_count

    low_rows = [
        (item["ragas_metrics"][HEADLINE_METRIC], item["kb_id"], item["query"])
        for item in combined["items"]
        if item["ragas_metrics"].get(HEADLINE_METRIC) is not None
        and item["ragas_metrics"][HEADLINE_METRIC] < threshold
    ]
    low_rows.sort(key=lambda row: row[0])

    kb_table_header = "| 知识库 | 数据集 | 题目数 |" + "".join(
        f" {_metric_label(name)} |" for name in metrics
    )
    kb_table_sep = "| --- | --- | --- |" + "".join(" --- |" for _ in metrics)

    lines = [
        f"# 忠实度汇报报告：{run_name}",
        "",
        f"> {REPORT_DISCLAIMER}",
        "",
        "## 口径定义（请先阅读）",
        "",
        "**本期「答案正确率」= RAGAS Faithfulness（忠实度）**",
        "公式：= 被本次检索上下文支持的陈述数 / 回答中总陈述数，逐条检查回答中的每个陈述是否都能"
        "在本次检索到的知识库上下文中找到依据（有据可查），回答中出现知识库未记载的内容（编造/臆测）"
        "时分数下降。取值范围 0~1，越高越好。",
        "其计算形式与业界精确率（Precision = TP/(TP+FP)）同构——都是「正确部分 / 全部输出部分」，"
        "但两者**对错标准不同**：忠实度以「检索到的上下文」为依据（回答对材料的事实一致性），"
        "精确率以「标准答案（Ground Truth）」为依据（预测准确度），严谨表述不作等同。",
        "",
        f"**内部双指标**：答案正确率（忠实度，目标 ≥ {threshold:.0%}）+ 答案相关性"
        "（Answer Relevancy，内部辅助参考）。",
        "",
        "**与合同验收指标的区别（重要）**",
        CONTRACT_DISCLAIMER,
        "",
        "## 本期汇总",
        "",
        "| 指标 | 本期值 | 目标 | 是否达标 |",
        "| --- | --- | --- | --- |",
        f"| {_metric_label(HEADLINE_METRIC)} | {_pct(headline)} | ≥ {threshold:.0%} | {_status(headline, threshold)} |",
        f"| {_metric_label(DUAL_METRIC)} | {_pct(relevancy)} | 内部参考 | - |",
        "",
        f"- 共评估 **{total}** 题（{kb_summary}）。",
        f"- 忠实度在 {headline_count}/{total} 题有值（{missing} 题缺失/为空）。",
        "",
        "## 分知识库汇总",
        "",
        kb_table_header,
        kb_table_sep,
    ]
    for kb in combined["kbs"]:
        cells = [kb["kb_id"], kb["dataset"], str(kb["count"])]
        cells.extend(_pct(kb["metrics"].get(name)) for name in metrics)
        lines.append("| " + " | ".join(cells) + " |")

    lines += [
        "",
        "## 每题明细",
        "",
        "| # | 知识库 | 问题 | 忠实度 | 答案相关性 | 是否低于阈值 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for item in combined["items"]:
        fh = item["ragas_metrics"].get(HEADLINE_METRIC)
        rel = item["ragas_metrics"].get(DUAL_METRIC)
        flag = "-" if (fh is None or fh >= threshold) else f"低于 {threshold:.0%}"
        cells = [str(item["index"]), item["kb_id"], item["query"], _pct(fh), _pct(rel), flag]
        lines.append("| " + " | ".join(cells) + " |")

    lines += [
        "",
        "## 低忠实度题目清单（优化输入）",
        "",
        f"以下 {len(low_rows)} 题忠实度低于阈值 {threshold:.0%}，是知识库补齐 / 检索优化 / "
        "答案链路优化的重点：",
        "",
        "| 忠实度 | 知识库 | 问题 |",
        "| --- | --- | --- |",
    ]
    for value, kb_id, query in low_rows:
        lines.append(f"| {_pct(value)} | {kb_id} | {query} |")

    kb_conclusion = " / ".join(f"{kb['kb_id']} {_pct(kb['metrics'].get(HEADLINE_METRIC))}" for kb in combined["kbs"])
    lines += [
        "",
        "## 结论",
        "",
        f"本期「答案正确率」（忠实度）均值为 **{_pct(headline)}**，**"
        f"{'未达' if headline is None or headline < threshold else '达到'}** 内部目标 **{threshold:.0%}**。",
        f"分知识库：{kb_conclusion}。",
        "本报告为内部质量基线；据此优先补齐低分题目对应知识库内容，再行复测。",
        "",
    ]
    return "\n".join(lines)


def write_faithfulness_reports(
    combined: dict[str, Any], *, run_name: str, output_dir: str = ".", threshold: float = 0.70
) -> tuple[str, str]:
    """写忠实度汇报 Markdown + JSON 报告到磁盘，返回 (json_path, md_path)。"""
    safe_name = re.sub(r"[^a-zA-Z0-9_-]+", "_", run_name)
    json_path = os.path.join(output_dir, f"faithfulness_report_{safe_name}.json")
    md_path = os.path.join(output_dir, f"faithfulness_report_{safe_name}.md")

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(
            build_faithfulness_json_report(combined, run_name=run_name, threshold=threshold),
            f,
            ensure_ascii=False,
            indent=2,
        )
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(build_faithfulness_markdown_report(combined, run_name=run_name, threshold=threshold))

    return json_path, md_path
