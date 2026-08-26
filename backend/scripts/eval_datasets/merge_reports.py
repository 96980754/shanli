#!/usr/bin/env python3
"""合并多个评分报告为一份总览（实质作答口径，内部工具）。

把甲方各批次评估合成一份：答案正确性 = 全部已实质作答题目的得分均值，
拒答/澄清不计分、仅记录。分域表按批次（sheet）给出各自正确性与分类分解。

输入：客服库 4 个 v1fix 报告 + MCX+定位产品报告。
输出：reports/kefu_facts_combined_YYYYMMDD.json / .md / ._detail.md / ._detail.csv
（_detail 为逐题全量明细）
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from datetime import date
from pathlib import Path

BASE = Path(__file__).resolve().parent
REP = BASE / "reports"

# 来源报告（含 v1fix 后的分类修正），按展示顺序
SOURCE_FILES = [
    "kefu_facts_MDM_all_20260819_v1fix.json",
    "kefu_facts_diaodutai_all_20260820_v1fix.json",
    "kefu_facts_all_运营平台_20260820_baseline_v1fix.json",
    "kefu_facts_miniserver_all_20260819_v1fix.json",
    "kefu_facts_mcx_loc_20260820.json",
]

# 每个来源的批次说明（客服库=既有摸底；新表=甲方问答表）
BATCH_NOTE = {
    "kefu_facts_MDM_all_20260819_v1fix.json": "客服库摸底",
    "kefu_facts_diaodutai_all_20260820_v1fix.json": "客服库摸底",
    "kefu_facts_all_运营平台_20260820_baseline_v1fix.json": "客服库摸底",
    "kefu_facts_miniserver_all_20260819_v1fix.json": "客服库摸底",
    "kefu_facts_mcx_loc_20260820.json": "MCX/定位产品新表",
}

_SHEET_ORDER = ["MDM", "调度台", "运营平台", "miniserver", "MCX", "定位产品"]

# 缺口声明被误判为实质作答的题（对照甲方口径手动重分类为拒答、归 0 分）。
# 判定标准：回答主体声明「知识库无题目所需专项说明/记载」、未交付题目要求内容
# （仅给旁证或反向要求补资料）。原始 judge 结论保留在 reclassified 字段供审计。
RECLASSIFY = {
    ("MDM", 9): "回答开头声明知识库未找到「配置文件下载」阶段专门故障说明，仅给注册通用排查，结尾建议补现场信息，未交付题目要求的专项排查步骤",
    ("MDM", 14): "回答声明无「网络超时」提示的直接故障说明，仅给「终端无法注册/网络类」旁证排查，未交付题目要求的批量注册网络超时定位",
    ("MDM", 18): "回答声明未记载「通过 USB 注册」流程，仅给快速适配工具排查建议，未交付题目要求的 USB 注册问题处理",
    ("MDM", 24): "回答声明未记载「该二维码已过期」提示的成因与有效期规则，仅给「二维码无效需重新扫码」通用处理，未交付题目要求内容",
    ("MDM", 26): "回答声明未查到「设备列表为空」的直接故障文档，结论为综合推断且建议补现场信息，未交付题目要求的排查步骤",
    ("定位产品", 7): "回答声明未检索到 iBeacon 术语专门定义、明确无法提供定义，仅给最接近的「蓝牙Beacon」旁证，未交付题目要求内容",
    ("定位产品", 58): "回答声明知识库未包含该现象的具体原因分析与排查步骤，仅给 P3 分级与标准流程并建议建工单跟进，未交付题目要求的处理步骤",
    ("调度台", 47): "回答声明未找到「终端定位不准确」专项排查步骤，仅给故障定级与影响因素旁证，未交付题目要求的排查处理",
    ("调度台", 54): "回答声明未检索到「调度台报错」其他专门故障说明，仅给一条相邻案例并反向要求用户补充报错内容，未交付题目要求的排查",
}


def _mean(vals: list[float]) -> float | None:
    vals = [v for v in vals if v is not None]
    return sum(vals) / len(vals) if vals else None


def _pct(v) -> str:
    return "-" if v is None else f"{v * 100:.1f}%"


def _fmt(text: str | None, limit: int = 40) -> str:
    t = (text or "").strip()
    return t if len(t) <= limit else t[: limit - 1] + "…"


def load_all(source_files: list[str]) -> list[dict]:
    items = []
    for fname in source_files:
        data = json.loads((REP / fname).read_text(encoding="utf-8"))
        batch = BATCH_NOTE.get(fname, "摸底")
        for it in data["items"]:
            it = dict(it)
            it["batch"] = batch
            items.append(it)
    return items


def apply_reclassify(items: list[dict]) -> int:
    """把缺口声明误判为实质作答的题按甲方口径重分类为拒答、归 0 分，返回重分类题数。"""
    n = 0
    for it in items:
        reason = RECLASSIFY.get((it["section"], it["index"]))
        if reason and it["cls"] == "answered":
            it["cls"] = "refusal_gap"
            it["gate"] = "fail"
            it["score"] = 0.0
            it["reclassified"] = True
            it["reclassify_reason"] = reason
            n += 1
    return n


def build_overall(items: list[dict]) -> dict:
    answered_items = [it for it in items if it["cls"] == "answered"]
    return {
        "mean": _mean([it["score"] for it in answered_items]),
        "answered": sum(1 for it in items if it["cls"] == "answered"),
        "refusal_gap": sum(1 for it in items if it["cls"] == "refusal_gap"),
        "clarify_missing": sum(1 for it in items if it["cls"] == "clarify_missing"),
        "e2e_error": sum(1 for it in items if it["cls"] == "e2e_error"),
        "judge_error": sum(1 for it in items if it["judge_error"]),
    }


def build_report(data: dict, md_path: Path, json_path: Path) -> None:
    items = data["items"]
    overall = data["overall"]
    order = {name: i for i, name in enumerate(_SHEET_ORDER)}
    by_sheet = defaultdict(list)
    for it in items:
        by_sheet[it["section"]].append(it)

    lines = [
        f"# 全部批次合并总览（{len(items)} 题 · 实质作答口径）",
        "",
        "> 汇总：客服库摸底（MDM/调度台/运营平台/miniserver 共 269 题，v1fix 分类修正后）+ "
        "MCX/定位产品新表（103 题，摸底口径）。",
        "> 摸底口径：未导入新表，直接用系统现有知识库作答——拒答反映知识库缺口，是补齐输入。",
        "",
        "## 口径定义（实质作答口径）",
        "",
        "**答案正确性 = 硬门槛 × (0.8 × 关键事实命中率 + 0.2 × 补充事实命中率)**",
        "",
        "**实质作答口径**：答案正确性 = 全部已实质作答题目的得分均值；拒答/澄清统一不计分、"
        "仅记录在缺口/澄清清单，缺口拒答反映知识库缺口而非答错。",
        "",
        "## 合并汇总",
        "",
        "| 指标 | 值 |",
        "| --- | --- |",
        f"| **答案正确性**（{overall['answered']} 题实质作答口径） | **{_pct(overall['mean'])}** |",
        f"| 实质作答 {overall['answered']} 题 · 缺口拒答 {overall['refusal_gap']} 题 · "
        f"需澄清 {overall['clarify_missing']} 题 · E2E 异常 {overall['e2e_error']} 题 · "
        f"评测失败 {overall['judge_error']} 题 | 分类归因 |",
        "",
        "## 分域汇总",
        "",
        "| 域 | 批次 | 题数 | 答案正确性 | 实质作答 | 缺口拒答 | 需澄清 | E2E 异常 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for name in sorted(by_sheet, key=lambda n: order.get(n, 99)):
        lst = by_sheet[name]
        answered = [it for it in lst if it["cls"] == "answered"]
        lines.append(
            f"| {name} | {lst[0]['batch']} | {len(lst)} | "
            f"{_pct(_mean([it['score'] for it in answered]))} | {len(answered)} | "
            f"{sum(1 for it in lst if it['cls'] == 'refusal_gap')} | "
            f"{sum(1 for it in lst if it['cls'] == 'clarify_missing')} | "
            f"{sum(1 for it in lst if it['cls'] == 'e2e_error')} |"
        )

    lines += [
        "",
        "## 缺口拒答题清单（诚实拒答，接入新表即可覆盖）",
        "",
        "| # | 域 | 问题 | 修正说明 |",
        "| --- | --- | --- | --- |",
    ]
    refusals = sorted(
        [it for it in items if it["cls"] == "refusal_gap"],
        key=lambda it: (order.get(it["section"], 99), it["index"]),
    )
    if not refusals:
        lines.append("无。")
    else:
        for it in refusals:
            flag = " ⚠修正" if it.get("reclassified") else ""
            reason = it.get("reclassify_reason") or ""
            lines.append(
                f"| {it['index']} | {it['section']} | {_fmt(it['query'], 48)} | "
                f"{_fmt(reason, 44) if it.get('reclassified') else '-'} |"
            )

    lines += [
        "",
        "## 说明与边界",
        "",
        "- 各批次为同一 judge（deepseek:deepseek-v4-flash）与同一公式评分；客服库批次含 v1fix 分类修正"
        "（gate=pass 但回答主体声明知识库无题目所需内容的题按拒答归 0），MCX/定位产品批次为直接摸底。",
        f"- {data.get('reclassify_note') or ''}",
        "- 合并正确性 = 全部已实质作答题目的得分均值（跨批次同权按题，非按域加权）。",
        "- 低分/每题明细见各批次原始报告（reports/kefu_facts_*.md）。",
        "",
    ]
    json_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text("\n".join(lines), encoding="utf-8")
    json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def write_detail(items: list[dict], md_path: Path, csv_path: Path) -> None:
    """逐题全量明细：MD 分域表格 + CSV（含未命中关键事实/甲方答案/系统回答）。"""
    order = {name: i for i, name in enumerate(_SHEET_ORDER)}
    by_sheet = defaultdict(list)
    for it in items:
        by_sheet[it["section"]].append(it)

    lines = [
        f"# 全部批次合并明细（{len(items)} 题 · 实质作答口径）",
        "",
        "> 与合并总览同一批数据，逐题列出。分类：实质作答 / 缺口拒答 / 需澄清 / E2E 异常。",
        "> 正确性 < 60% 标 ⚠（低分，需逐题归因）。",
        "",
    ]
    for name in sorted(by_sheet, key=lambda n: order.get(n, 99)):
        rows = by_sheet[name]
        lines.append(f"## {name} ({len(rows)}题)")
        lines.append("")
        lines.append("| # | 问题 | 正确性 | 关键事实 | 补充事实 | 分类 |")
        lines.append("|---|------|--------|----------|----------|------|")
        for it in sorted(rows, key=lambda x: x["index"]):
            flag = " ⚠" if it["score"] is not None and it["score"] < 0.60 else ""
            cls_label = {
                "answered": "实质作答",
                "refusal_gap": "拒答",
                "clarify_missing": "澄清",
                "e2e_error": "E2E异常",
            }.get(it["cls"], it["cls"])
            if it.get("reclassified"):
                cls_label += " ⚠修正"
            lines.append(
                f"| {it['index']} | {_fmt(it['query'], 44)} | {_pct(it['score'])}{flag} | "
                f"{it['key_facts']['hit']}/{it['key_facts']['total']} | "
                f"{it['supp_facts']['hit']}/{it['supp_facts']['total']} | {cls_label} |"
            )
        lines.append("")

    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text("\n".join(lines), encoding="utf-8")

    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["分区", "批次", "题号", "问题", "答案正确性", "分类", "gate", "关键事实", "补充事实", "未命中关键", "甲方答案", "系统答案", "修正说明"])
        for it in sorted(items, key=lambda x: (order.get(x["section"], 99), x["index"])):
            w.writerow(
                [
                    it["section"],
                    it.get("batch", ""),
                    it["index"],
                    it["query"],
                    _pct(it["score"]),
                    it["cls"],
                    it["gate"],
                    f"{it['key_facts']['hit']}/{it['key_facts']['total']}",
                    f"{it['supp_facts']['hit']}/{it['supp_facts']['total']}",
                    "；".join(it["key_missed"][:4]),
                    it.get("gold_answer") or "",
                    (it.get("agent_answer") or "").replace("\n", " "),
                    it.get("reclassify_reason") or "",
                ]
            )


def _quote(text: str) -> list[str]:
    """把多行文本转成 blockquote 行，保留结构便于阅读。"""
    return [f"> {ln}" if ln.strip() else ">" for ln in (text or "").splitlines()] or ["> （无）"]


def write_per_question_md(items: list[dict], md_path: Path) -> None:
    """每题一条详细记录：甲方标准答案 / 系统回答全文 / 硬门槛依据 / 事实命中与未命中 / 修正说明。"""
    order = {name: i for i, name in enumerate(_SHEET_ORDER)}
    by_sheet = defaultdict(list)
    for it in items:
        by_sheet[it["section"]].append(it)

    cls_label = {
        "answered": "实质作答",
        "refusal_gap": "拒答",
        "clarify_missing": "需澄清",
        "e2e_error": "E2E异常",
    }
    lines = [
        f"# 全部批次每题详细记录（{len(items)} 题 · 实质作答口径）",
        "",
        "> 每题含：分类 / 答案正确性 / 硬门槛（依据）/ 甲方标准答案 / 系统回答全文 / "
        "关键·补充事实命中与未命中清单。重分类题标 ⚠修正（见「修正说明」）。",
        "",
    ]
    for name in sorted(by_sheet, key=lambda n: order.get(n, 99)):
        rows = sorted(by_sheet[name], key=lambda x: x["index"])
        lines.append(f"## {name}（{len(rows)}题）")
        for it in rows:
            label = cls_label.get(it["cls"], it["cls"])
            if it.get("reclassified"):
                label += " ⚠修正"
            lines += [
                f"### #{it['index']} {it['query']}",
                "",
                f"- **分类**：{label} ｜ **答案正确性**：{_pct(it['score'])} ｜ "
                f"**硬门槛**：{'通过' if it['gate'] == 'pass' else '不通过'}",
                f"- **关键事实**：{it['key_facts']['hit']}/{it['key_facts']['total']} ｜ "
                f"**补充事实**：{it['supp_facts']['hit']}/{it['supp_facts']['total']}",
                "",
                "**甲方标准答案**：",
                *_quote(it.get("gold_answer") or ""),
                "",
                "**系统回答**：",
                *_quote(it.get("agent_answer") or ""),
                "",
            ]
            if it.get("gate_reason"):
                lines += [f"**硬门槛依据**：{it['gate_reason']}", ""]
            if it.get("key_missed"):
                lines += ["**未命中的关键事实**：", ""]
                lines += [f"- {m}" for m in it["key_missed"]]
                lines.append("")
            if it.get("supp_missed"):
                lines += ["**未命中的补充事实**：", ""]
                lines += [f"- {m}" for m in it["supp_missed"]]
                lines.append("")
            if it.get("reclassify_reason"):
                lines += [f"**修正说明**：{it['reclassify_reason']}", ""]
        lines.append("")
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="合并各批次评分报告为总览")
    parser.add_argument("--name", default=f"combined_{date.today():%Y%m%d}", help="运行名")
    parser.add_argument("--sources", nargs="*", default=SOURCE_FILES, help="来源报告文件名（相对 reports/）")
    args = parser.parse_args()

    items = load_all(args.sources)
    n_rec = apply_reclassify(items)
    data = {
        "run_name": args.name,
        "formula": "答案正确性 = 硬门槛 × (0.8 × 关键事实命中率 + 0.2 × 补充事实命中率)，实质作答口径",
        "judge_llm": "deepseek:deepseek-v4-flash",
        "reclassify_note": (
            f"本次修正 {n_rec} 题：回答主体声明知识库无题目所需专项说明/记载、未交付题目要求内容"
            "（仅给旁证或反向要求补资料）的题，按甲方口径手动重分类为拒答并归 0 分；"
            "原始 judge 结论保留在 reclassified/reclassify_reason 字段供审计。"
        ),
        "overall": build_overall(items),
        "items": items,
    }
    json_path = REP / f"kefu_facts_{args.name}.json"
    md_path = REP / f"kefu_facts_{args.name}.md"
    build_report(data, md_path, json_path)
    write_detail(items, REP / f"kefu_facts_{args.name}_detail.md", REP / f"kefu_facts_{args.name}_detail.csv")
    write_per_question_md(items, REP / f"kefu_facts_{args.name}_每题详细.md")

    o = data["overall"]
    print(f"{args.name}: 合并 {len(items)} 题 | 答案正确性（{o['answered']} 题实质作答口径）: {_pct(o['mean'])}")
    print(f"分类: 实质作答 {o['answered']} · 缺口拒答 {o['refusal_gap']} · 需澄清 {o['clarify_missing']} · "
          f"E2E 异常 {o['e2e_error']} · 评测失败 {o['judge_error']}")
    print(f"报告已写入:\n  {json_path}\n  {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
