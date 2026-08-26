"""真实 Agent 端到端「答案准确率」汇报报告模块（内部，不交付甲方）。

对合成测试集 + 真实 Agent 回答的评分结果，合并多知识库后生成管理向「答案准确率」
汇报报告（Markdown + JSON）。主口径 = RAGAS Answer Relevancy（系统答案对用户问题的
语义贴合度，不依赖参考答案质量），内部辅助 = 忠实度 / 答案正确性。

模块级不 import ragas，可在单元测试与客户端镜像中干净导入；所有输入均为普通 dict，
纯函数可单测。复用 faithfulness_report.combine_results 做多库合并（指标无关）。
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

from yuxi.knowledge.eval.faithfulness_report import (
    METRIC_LABELS,
    combine_results,  # noqa: F401  # 复用 faithfulness_report 的多库合并实现（指标无关），并作为本模块公共接口再导出
)

REPORT_DISCLAIMER = (
    "本报告为内部整体质量评估（不交付甲方）：用业务方人工整理的业务验证集驱动生产真实 Agent "
    "作答，由评测模型评估系统答案对用户问题的贴合度。"
)

CONTRACT_DISCLAIMER = (
    "本报告「答案准确率」为内部质量口径（RAGAS Answer Relevancy：系统答案对用户问题的语义贴合度），"
    "测试集为业务方人工整理的业务验证集。它**不是**《AI知识库软件开发"
    "合同终稿》中的验收指标——合同验收指标为：答案可接受准确率 ≥ 80%（一期 ≥90%，三期 ≥95%）、"
    "引用正确率 ≥ 95%。两者口径、判定方法与数据来源不同，本报告不得用于替代合同验收结论。"
)


def _dataset_desc(combined: dict[str, Any]) -> str:
    """按实际合并结果生成测试集描述（如「95 题（kb_a 63 题 + kb_b 13 题）」）。"""
    desc = f"{combined['total_items']} 题"
    if combined["kbs"]:
        desc += "（" + " + ".join(f"{kb['kb_id']} {kb['count']} 题" for kb in combined["kbs"]) + "）"
    return desc

# 主口径 = Answer Relevancy：衡量「答案贴不贴合问题」。相比 Answer Correctness（F1 逐句对账 + 语义），
# 它不依赖参考答案（gold）质量，不受 gold 检索错漏、详略颗粒度干扰（gold 检索不可靠，见 generate_gold_answers）。
HEADLINE_METRIC = "answer_relevancy"
DUAL_METRIC = "faithfulness"

# 知识库缺口题：参考答案本身即「知识库未记载」且 Agent 给出简短诚实拒答。
# RAGAS 三项指标对此类答案结构性失真（相关性按回避性回答乘 0、忠实度无法验证
# 「信息不存在」、正确性因参考答案主体是泛泛概括而偏低），评分端将答案正确性
# 按正确（1.0）计入，忠实度/相关性置 N/A，并在报告中单列说明。
_GOLD_GAP_MARKERS = ("未记载", "未查询到", "无相关", "未提供", "未找到", "没有记载", "尚无", "无明确")
_REFUSAL_MARKERS = ("未找到", "抱歉", "未检索", "请问您指", "无相关")


def is_justified_refusal(record: dict[str, Any]) -> bool:
    """判定是否为知识库缺口题的诚实拒答（gold 自认未记载 ∧ Agent 仅简短拒答）。

    record 需含 gold_answer 与 agent_answer。Agent 回答必须是「未找到/请问您指」类的
    纯拒答（≤60 字符、无实质内容），避免把带实质作答的长答案误判为拒答。
    """
    gold = record.get("gold_answer") or ""
    agent = (record.get("agent_answer") or "").strip()
    return (
        bool(gold)
        and any(m in gold for m in _GOLD_GAP_MARKERS)
        and 0 < len(agent) < 60
        and any(m in agent for m in _REFUSAL_MARKERS)
    )


def _mean(items: list[dict[str, Any]], metric_name: str) -> float | None:
    values = [
        item["ragas_metrics"][metric_name] for item in items if item["ragas_metrics"].get(metric_name) is not None
    ]
    if not values:
        return None
    return sum(values) / len(values)


def build_accuracy_json_report(combined: dict[str, Any], *, run_name: str, threshold: float = 0.80) -> dict[str, Any]:
    """组装「答案准确率」汇报 JSON 报告。"""
    headline = combined["metrics"].get(HEADLINE_METRIC)
    low = sorted(
        (
            {
                "kb_id": item["kb_id"],
                "dataset": item["dataset"],
                "index": item["index"],
                "query": item["query"],
                "answer_relevancy": item["ragas_metrics"].get(HEADLINE_METRIC),
                "agent_answer": item.get("agent_answer", ""),
                "gold_answer": item.get("gold_answer", ""),
            }
            for item in combined["items"]
            if not item.get("exclude_from_aggregate")
            and item["ragas_metrics"].get(HEADLINE_METRIC) is not None
            and item["ragas_metrics"][HEADLINE_METRIC] < threshold
        ),
        key=lambda row: row["answer_relevancy"],
    )
    justified_refusals = [
        {
            "kb_id": item["kb_id"],
            "dataset": item["dataset"],
            "index": item["index"],
            "query": item["query"],
        }
        for item in combined["items"]
        if item.get("justified_refusal")
    ]
    excluded = [
        {
            "kb_id": item["kb_id"],
            "dataset": item["dataset"],
            "index": item["index"],
            "query": item["query"],
            "reason": item.get("exclude_reason", ""),
            "answer_relevancy": item["ragas_metrics"].get(HEADLINE_METRIC),
            "faithfulness": item["ragas_metrics"].get(DUAL_METRIC),
            "answer_correctness": item["ragas_metrics"].get("answer_correctness"),
        }
        for item in combined["items"]
        if item.get("exclude_from_aggregate")
    ]
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
        "low_accuracy": low,
        "justified_refusals": justified_refusals,
        "excluded_from_aggregate": excluded,
    }


def _pct(value: float | None) -> str:
    return "-" if value is None else f"{value * 100:.1f}%"


def _status(value: float | None, threshold: float) -> str:
    if value is None:
        return "数据缺失"
    return "达标" if value >= threshold else "未达标"


def _metric_label(name: str) -> str:
    return METRIC_LABELS.get(name, name)


def build_accuracy_markdown_report(combined: dict[str, Any], *, run_name: str, threshold: float = 0.80) -> str:
    """渲染「答案准确率」汇报 Markdown 报告：口径定义 + 汇总 + 分库 + 明细 + 低分清单 + 结论。"""
    metrics = combined["metrics"]
    headline = metrics.get(HEADLINE_METRIC)
    faithfulness = metrics.get(DUAL_METRIC)
    total = combined["total_items"]
    kb_summary = " / ".join(f"{kb['kb_id']} {kb['count']} 题" for kb in combined["kbs"])
    headline_count = combined["metric_counts"].get(HEADLINE_METRIC, 0)
    missing = total - headline_count

    low_rows = [
        (item["ragas_metrics"][HEADLINE_METRIC], item["kb_id"], item["query"])
        for item in combined["items"]
        if not item.get("exclude_from_aggregate")
        and item["ragas_metrics"].get(HEADLINE_METRIC) is not None
        and item["ragas_metrics"][HEADLINE_METRIC] < threshold
    ]
    low_rows.sort(key=lambda row: row[0])

    kb_table_header = "| 知识库 | 数据集 | 题目数 |" + "".join(f" {_metric_label(name)} |" for name in metrics)
    kb_table_sep = "| --- | --- | --- |" + "".join(" --- |" for _ in metrics)

    justify_count = sum(1 for item in combined["items"] if item.get("justified_refusal"))
    clarify_count = sum(
        1
        for item in combined["items"]
        if not (item.get("agent_answer") or "").strip() and not item.get("justified_refusal")
    )
    excluded_count = sum(1 for item in combined["items"] if item.get("exclude_from_aggregate"))
    missing_detail: list[str] = []
    if clarify_count:
        missing_detail.append(f"澄清无答案 {clarify_count} 题")
    if justify_count:
        missing_detail.append(f"知识库缺口单列 {justify_count} 题")
    if excluded_count:
        missing_detail.append(f"表述冗长排除 {excluded_count} 题")

    lines = [
        f"# 答案准确率汇报报告（真实 Agent 端到端）：{run_name}",
        "",
        f"> {REPORT_DISCLAIMER}",
        "",
        "## 口径定义（请先阅读）",
        "",
        "**本期「答案准确率」= RAGAS Answer Relevancy**",
        "将系统生成答案反向生成若干问题，与原问题做语义匹配（bge-m3 余弦相似度），衡量答案对用户问题的贴合度，",
        "由评测模型自动计算，取值范围 0~1，越高越好。",
        "该指标不依赖参考答案质量——只衡量「答得贴不贴题」，不受参考答案检索错漏与详略颗粒度影响。",
        "",
        f"**测试集**：业务方人工整理的 {_dataset_desc(combined)}，覆盖产品使用/故障排查/"
        "产品规格/应用场景/部署配置/商务资质/方案整合等场景，客观可追溯。",
        "",
        "**内部辅助指标**：",
        "",
        "- **忠实度（Faithfulness）**：= 被本次检索上下文支持的陈述数 / 回答中总陈述数，衡量回答"
        "「有据可查」的程度（每个陈述能否从检索到的知识库上下文中推断出来）。其计算形式与精确率"
        "（Precision = TP/(TP+FP)）同构——都是「正确部分 / 全部输出部分」，但两者**对错标准不同**："
        "忠实度以「检索到的上下文」为依据（回答对材料的事实一致性），精确率以「标准答案"
        "（Ground Truth）」为依据（预测准确度），严谨表述不作等同。",
        "",
        "- **答案正确性（Answer Correctness）**：对照参考答案逐句对账，受参考答案检索质量影响，"
        "仅供交叉参考。",
        "",
        "**与合同验收指标的区别（重要）**",
        CONTRACT_DISCLAIMER,
        "",
        "## 本期汇总",
        "",
        "| 指标 | 本期值 | 目标 | 是否达标 |",
        "| --- | --- | --- | --- |",
        f"| {_metric_label(HEADLINE_METRIC)} | {_pct(headline)} | ≥ {threshold:.0%} | {_status(headline, threshold)} |",
        f"| {_metric_label(DUAL_METRIC)} | {_pct(faithfulness)} | 内部参考 | - |",
        "",
        f"- 共评估 **{total}** 题（{kb_summary}）。",
        f"- 答案准确率在 {headline_count}/{total} 题有值（{missing} 题缺失/为空"
        + (f"，{'、'.join(missing_detail)}" if missing_detail else "")
        + "）。",
    ]
    if excluded_count:
        excluded_indexes = "、".join(
            str(item["index"]) for item in combined["items"] if item.get("exclude_from_aggregate")
        )
        lines.append(
            f"- 其中 **{excluded_count}** 题（#{excluded_indexes}）答案正确但表述冗长、"
            "忠实度被陈述数量稀释，经业务方确认**不计入主口径均值**（详见专节）。"
        )
    if justify_count:
        lines.append(
            f"- 知识库缺口题（参考答案自认「未记载」+ Agent 诚实拒答）共 **{justify_count}** 题："
            "答案相关性 N/A、**不计入答案准确率均值**（详见专节）。"
        )
    lines += [
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
        "| # | 知识库 | 问题 | 答案准确率 | 忠实度 | 是否低于阈值 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for item in combined["items"]:
        acc = item["ragas_metrics"].get(HEADLINE_METRIC)
        fh = item["ragas_metrics"].get(DUAL_METRIC)
        if item.get("exclude_from_aggregate"):
            flag = "排除出主口径"
        elif item.get("justified_refusal"):
            flag = "缺口题不计入"
        elif acc is None or acc >= threshold:
            flag = "-"
        else:
            flag = f"低于 {threshold:.0%}"
        cells = [str(item["index"]), item["kb_id"], item["query"], _pct(acc), _pct(fh), flag]
        lines.append("| " + " | ".join(cells) + " |")

    lines += [
        "",
        "## 低准确率题目清单（优化输入）",
        "",
        f"以下 {len(low_rows)} 题答案准确率低于阈值 {threshold:.0%}，是知识库补齐 / 检索优化 / "
        "Agent 提示词优化的重点：",
        "",
        "| 答案准确率 | 知识库 | 问题 |",
        "| --- | --- | --- |",
    ]
    for value, kb_id, query in low_rows:
        lines.append(f"| {_pct(value)} | {kb_id} | {query} |")

    justified = [item for item in combined["items"] if item.get("justified_refusal")]
    if justified:
        lines += [
            "",
            "## 知识库缺口题（诚实拒答，不计入主口径）",
            "",
            f"以下 {len(justified)} 题参考答案自认「知识库未记载」，Agent 给出简短拒答、未编造内容，"
            "与参考答案结论一致。此类题无法用 Answer Relevancy 度量（拒答不对应原问题、语义贴合度失真），"
            "故**不计入本期答案准确率均值**，单列于此；答案正确性一栏按内部惯例计为 100%（表示拒答与"
            "参考答案结论一致）。若知识库补齐对应内容，应取消该口径、按常规评分：",
            "",
            "| # | 知识库 | 问题 | 答案正确性 |",
            "| --- | --- | --- | --- |",
        ]
        for item in justified:
            lines.append(f"| {item['index']} | {item['kb_id']} | {item['query']} | 按正确计 |")

    excluded_items = [item for item in combined["items"] if item.get("exclude_from_aggregate")]
    if excluded_items:
        lines += [
            "",
            "## 排除出主口径题（答案正确但表述冗长）",
            "",
            f"以下 {len(excluded_items)} 题经业务方确认不计入主口径均值、单列于此——回答正确但额外展开"
            "规格/证书/型号区分等陈述，忠实度被陈述数量稀释（答对但说多了）；个别题答案相关性为 0 是"
            "评测模型对『未记载』式非承诺回答的判定失真（非承诺回答 ×0），与表述冗长无关；每题指标仍"
            "可在明细中查看：",
            "",
            "| # | 知识库 | 问题 | 答案准确率 | 忠实度 | 答案正确性 |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
        for item in excluded_items:
            m = item["ragas_metrics"]
            lines.append(
                f"| {item['index']} | {item['kb_id']} | {item['query']} | "
                f"{_pct(m.get(HEADLINE_METRIC))} | {_pct(m.get(DUAL_METRIC))} | "
                f"{_pct(m.get('answer_correctness'))} |"
            )

    kb_conclusion = " / ".join(f"{kb['kb_id']} {_pct(kb['metrics'].get(HEADLINE_METRIC))}" for kb in combined["kbs"])
    lines += [
        "",
        "## 结论",
        "",
        f"本期「答案准确率」（真实 Agent 端到端）均值为 **{_pct(headline)}**，**"
        f"{'未达' if headline is None or headline < threshold else '达到'}** 内部目标 **{threshold:.0%}**。",
        f"分知识库：{kb_conclusion}。",
        "本报告为内部整体质量基线；据此优先补齐低分题目对应知识库内容，再行复测。",
        "",
    ]
    return "\n".join(lines)


def write_accuracy_reports(
    combined: dict[str, Any], *, run_name: str, output_dir: str = ".", threshold: float = 0.80
) -> tuple[str, str]:
    """写「答案准确率」汇报 Markdown + JSON 报告到磁盘，返回 (json_path, md_path)。"""
    safe_name = re.sub(r"[^a-zA-Z0-9_-]+", "_", run_name)
    json_path = os.path.join(output_dir, f"accuracy_report_{safe_name}.json")
    md_path = os.path.join(output_dir, f"accuracy_report_{safe_name}.md")

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(
            build_accuracy_json_report(combined, run_name=run_name, threshold=threshold),
            f,
            ensure_ascii=False,
            indent=2,
        )
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(build_accuracy_markdown_report(combined, run_name=run_name, threshold=threshold))

    return json_path, md_path
