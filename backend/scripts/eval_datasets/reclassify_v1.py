#!/usr/bin/env python3
"""修正 v1 摸底报告的「拒答误判为实质作答」，重算得分，输出 _v1fix 报告。

背景：classify() 原本只对 <60 字的短回答检查拒答标记，长回答即使开头声明
「知识库中没有 X」也被判为「实质作答」（如 终端-cat1 #2）。修正策略：

1. gate=fail 的题以 judge 归因为准重新分类（拒答 → refusal_gap，反问澄清 → clarify_missing）；
2. gate=pass 但回答开头即声明「知识库无题目所需专门说明/记载」、且未交付题目要求内容
   （给的是明确不适用于本题主体的旁证，或反向要求用户补资料）的题，按甲方口径手动重分类
   为拒答并归 0 分；原始 judge 结论保留在 reclassified 字段，便于审计。

不改写原始报告，输出 reports/kefu_facts_{run_name}_v1fix.json/.md + 404 全量明细（_v1fix）。
"""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path

from score_kefu_facts import build_report, classify

BASE = Path(__file__).resolve().parent
REP = BASE / "reports"

# (section, index) → 手动重分类理由（仅 gate=pass 但回答主体即「知识库无此内容」的题）
MANUAL_OVERRIDES = {
    ("终端-cat1", 2): "回答开头即称知识库无 E600 机型专属平台切换操作说明，所给通用方法明确不适用于 E600（小屏 RTOS 终端），未交付题目要求内容",
    ("终端-cat1", 12): "回答开头即称知识库无 E600 本机群组监听操作说明、无法提供按键步骤，仅给平台侧配置并请用户补终端手册",
    ("终端-安卓", 30): "回答开头即称未检索到小屏机/无屏机专项手册，所给录音回放仅针对大屏 APP 且明确无法确认是否一致",
    ("终端-安卓", 55): "回答开头即称未记载 mandown 不生效/轻放触发的排查与防误触方法，仅给「确认开关开启」一个检查点",
    ("MDM", 41): "回答开头即称未找到无屏机是否支持 MDM 的直接记载，且明确无法给出结论、需按文档另行评估",
    ("调度台", 49): "回答开头即称未找到轨迹回放选择时间页面语言显示的专门设置说明，并反向要求用户补充产品/版本信息",
}

V1_REPORTS = [
    ("cat1_all_20260820", "kefu_facts_cat1_all_20260820.json"),
    ("zduan_android_all_20260819", "kefu_facts_zduan_android_all_20260819.json"),
    ("MDM_all_20260819", "kefu_facts_MDM_all_20260819.json"),
    ("diaodutai_all_20260820", "kefu_facts_diaodutai_all_20260820.json"),
    ("all_运营平台_20260820_baseline", "kefu_facts_all_运营平台_20260820_baseline.json"),
    ("miniserver_all_20260819", "kefu_facts_miniserver_all_20260819.json"),
]

SHEET_ORDER = ["终端-cat1", "终端-安卓", "MDM", "调度台", "运营平台", "miniserver"]

# 甲方标准答案/文档不全的分区，暂不参与评估（待甲方补全文档后再纳入）
EXCLUDE_SECTIONS = {"终端-安卓", "终端-cat1"}

_REFUSAL_NOTE = "系统回答开头即声明知识库无题目所需专门说明/记载，未交付题目要求内容，按拒答计 0 分（口径修正）"


def _judge_of(item: dict) -> dict:
    return {"gate": item["gate"], "gate_reason": item.get("gate_reason") or ""}


def _record_of(item: dict) -> dict:
    return {"error": None, "agent_answer": item.get("agent_answer") or "", "query": item.get("query") or ""}


def _mean(vals: list) -> float | None:
    vals = [v for v in vals if v is not None]
    return sum(vals) / len(vals) if vals else None


def recompute(item: dict) -> dict:
    """重算单题的 cls/gate/score，返回新对象（含变更审计字段），不改原对象。"""
    new = dict(item)
    judge = _judge_of(item)
    rec = _record_of(item)
    cls = classify(rec, judge)

    key = (item["section"], item["index"])
    if key in MANUAL_OVERRIDES:
        new["cls"] = "refusal_gap"
        new["gate"] = "fail"
        new["score"] = 0.0
        new["reclassified"] = True
        new["judge_gate_orig"] = judge["gate"]
        new["judge_gate_reason_orig"] = judge["gate_reason"]
        new["judge_score_orig"] = item["score"]
        new["reclassify_reason"] = MANUAL_OVERRIDES[key]
        new["gate_reason"] = _REFUSAL_NOTE
    elif cls != item.get("cls"):
        new["cls"] = cls
        new["reclassify_reason"] = (
            "gate=fail，judge 判定未实质作答，原 classify 因回答超长把拒答/澄清误标为实质作答，按 judge 归因修正分类"
        )
    return new


def build_overall(items: list[dict]) -> dict:
    scored = [it for it in items if it["score"] is not None]
    answered_items = [it for it in items if it["cls"] == "answered"]
    return {
        "mean": _mean([it["score"] for it in answered_items]),
        "scored": len(scored),
        "answered": sum(1 for it in items if it["cls"] == "answered"),
        "refusal_gap": sum(1 for it in items if it["cls"] == "refusal_gap"),
        "clarify_missing": sum(1 for it in items if it["cls"] == "clarify_missing"),
        "e2e_error": sum(1 for it in items if it["cls"] == "e2e_error"),
        "judge_error": sum(1 for it in items if it["judge_error"]),
    }


def _pct(v) -> str:
    return "-" if v is None else f"{v * 100:.1f}%"


def _fmt(t, n):
    t = (t or "").strip()
    return t if len(t) <= n else t[: n - 1] + "…"


def main() -> int:
    all_items: list[dict] = []
    for run_name, fname in V1_REPORTS:
        path = REP / fname
        data = json.loads(path.read_text(encoding="utf-8"))
        old = data["overall"]
        items = [recompute(it) for it in data["items"]]
        sheet = items[0]["section"] if items else ""
        if sheet in EXCLUDE_SECTIONS:
            print(f"⏭ 排除分区「{sheet}」({run_name})：甲方标准答案/文档不全，暂不评估，不生成该分区报告")
            continue
        overall = build_overall(items)

        for it in items:
            if it.get("reclassified"):
                print(
                    f"  ⚠ [{it['section']} #{it['index']}] 手动拒答 "
                    f"(原gate={it['judge_gate_orig']}, 原分{it['judge_score_orig'] * 100:.0f}%) | {_fmt(it['query'], 40)}"
                )
        # 列出 classify 修正（原 answered → 拒答/澄清）且非手动覆盖的
        for old_it, it in zip(data["items"], items):
            if old_it.get("cls") != it["cls"] and not it.get("reclassified"):
                print(
                    f"  · [{it['section']} #{it['index']}] {old_it.get('cls')}→{it['cls']} "
                    f"(原gate={it['gate']}) | {_fmt(it['query'], 40)}"
                )

        data = dict(data)
        data["run_name"] = f"{run_name}_v1fix"
        data["overall"] = overall
        data["items"] = items
        data["reclassify_note"] = (
            "本次修正：gate=fail 题按 judge 归因重新分类（拒答/澄清）；"
            "gate=pass 但回答主体声明知识库无题目所需内容的题手动重分类为拒答并归 0 分，"
            "原始 judge 结论保留在 reclassified 字段。"
            "口径：实质作答口径——答案正确性只统计已实质作答题目的均值，拒答/澄清不计分、仅记录。"
        )
        json_path = REP / f"kefu_facts_{run_name}_v1fix.json"
        md_path = REP / f"kefu_facts_{run_name}_v1fix.md"
        build_report(data, md_path, json_path)
        all_items.extend(items)

        print(
            f"{run_name}: 答案正确性 {_pct(old['mean'])} → {_pct(overall['mean'])} | "
            f"实质作答 {old['answered']}→{overall['answered']} · "
            f"缺口拒答 {old['refusal_gap']}→{overall['refusal_gap']} · "
            f"需澄清 {old['clarify_missing']}→{overall['clarify_missing']}"
        )

    # ---- 404 全量明细（对齐 baseline 格式） ----
    all_items = [it for it in all_items if it["section"] not in EXCLUDE_SECTIONS]
    order = {name: i for i, name in enumerate(SHEET_ORDER)}
    all_items.sort(key=lambda it: (order.get(it["section"], 99), it["index"]))

    excl_names = "、".join(sorted(EXCLUDE_SECTIONS))
    excl_note = f"；排除{'、'.join(sorted(EXCLUDE_SECTIONS))}（甲方标准答案/文档不全，暂不评估）" if EXCLUDE_SECTIONS else ""
    md_lines = [
        f"# 客服知识库 {len(all_items)} 题全量明细（v1 摸底 · 分类修正{f' · 排除{excl_names}' if excl_names else ''}）",
        "",
        "> judge: deepseek:deepseek-v4-flash | 答案正确性 = 硬门槛×(0.8×关键+0.2×补充)，"
        "实质作答口径（拒答/澄清不计分、仅记录）| 摸底：未接入客服库，用系统现有库作答 | "
        f"本次修正：gate=pass 但回答主体声明知识库无题目所需内容的题按拒答归0{excl_note}",
        "",
    ]
    for sheet in SHEET_ORDER:
        if sheet in EXCLUDE_SECTIONS:
            continue
        rows = [it for it in all_items if it["section"] == sheet]
        if not rows:
            continue
        md_lines.append(f"## {sheet} ({len(rows)}题)")
        md_lines.append("")
        md_lines.append("| # | 问题 | 正确性 | 关键事实 | 补充事实 | 分类 |")
        md_lines.append("|---|------|--------|----------|----------|------|")
        for it in rows:
            flag = " ⚠" if it["score"] is not None and it["score"] < 0.60 else ""
            md_lines.append(
                f"| {it['index']} | {_fmt(it['query'], 44)} | {_pct(it['score'])}{flag} | "
                f"{it['key_facts']['hit']}/{it['key_facts']['total']} | "
                f"{it['supp_facts']['hit']}/{it['supp_facts']['total']} | "
                f"{'拒答' if it['cls'] == 'refusal_gap' else ('澄清' if it['cls'] == 'clarify_missing' else '实质作答')} |"
            )
        md_lines.append("")
    md_path = REP / "kefu_facts_404_v1fix_detail.md"
    md_path.write_text("\n".join(md_lines), encoding="utf-8")

    csv_path = REP / "kefu_facts_404_v1fix_detail.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["分区", "题号", "问题", "答案正确性", "分类", "gate", "关键事实", "补充事实", "未命中关键", "甲方答案", "系统答案"])
        for it in all_items:
            w.writerow(
                [
                    it["section"],
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
                ]
            )
    print(f"\n404 全量明细已写入:\n  {md_path.relative_to(BASE)}\n  {csv_path.relative_to(BASE)}")
    print(f"总计: {len(all_items)} 题 | 拒答 {sum(1 for it in all_items if it['cls']=='refusal_gap')} · "
          f"澄清 {sum(1 for it in all_items if it['cls']=='clarify_missing')} · "
          f"实质作答 {sum(1 for it in all_items if it['cls']=='answered')}"
          f"{'（已排除终端-安卓：甲方标准答案/文档不全）' if EXCLUDE_SECTIONS else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
