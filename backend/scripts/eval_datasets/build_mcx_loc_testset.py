#!/usr/bin/env python3
"""从甲方《MCX问答表.xlsx》《定位产品FAQ问答表.xlsx》构建问答测试集（内部工具）。

MCX 表：单 sheet「Sheet1」，列 = 序号/问题/答案，30 题。
定位表：sheet「FAQ问答表」，前 2 行为标题/说明，第 3 行起 = 序号/分类/问题/回答，73 题。

问题统一加分区前缀（MCX-/定位产品-）携带模块上下文，gold_answer 取表内官方答案。
过滤口径对齐 build_kefu_testset：排除线下操作类答案、封面/非问题行、短答案。

用法（宿主机，无需容器）：
  python3 build_mcx_loc_testset.py --out mcx_loc_all.jsonl
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

import openpyxl

from build_kefu_testset import _HEADER_LIKE, _NON_QUESTION

# 线下动作主导的答案词（系统无法从知识库执行）：排除。
# 注意不含「联系售后/线下」——定位产品 FAQ 的故障排查答案主体是
# 检查/重启/信号覆盖等可交付步骤，「联系售后客服」只是末尾兜底，应保留。
_OFFLINE_OPS = ("发给客户", "寄回", "提交工单", "找销售", "联系销售", "放到qt下", "发到qt", "走售后")


def is_offline_answer(answer: str) -> bool:
    return any(kw in answer for kw in _OFFLINE_OPS)

BASE = Path(__file__).resolve().parent
DOCS = BASE.parent.parent.parent / "docs"

MCX = {"file": DOCS / "MCX问答表.xlsx", "sheet": "Sheet1", "section": "MCX", "prefix": "MCX",
       "header_rows": 1, "cols": (0, 1, 2), "category": None}
LOC = {"file": DOCS / "定位产品FAQ问答表.xlsx", "sheet": "FAQ问答表", "section": "定位产品", "prefix": "定位产品",
       "header_rows": 3, "cols": (0, 2, 3), "category": 1}


def clean(text: str) -> str:
    text = re.sub(r"^\s*\d+[\.、）)]\s*", "", str(text).strip())
    return re.sub(r"\s+", " ", text).strip()


def build_table(cfg: dict) -> list[dict]:
    wb = openpyxl.load_workbook(cfg["file"], read_only=True, data_only=True)
    ws = wb[cfg["sheet"]]
    rows = list(ws.iter_rows(values_only=True))[cfg["header_rows"]:]
    wb.close()

    idx, qc, ac = cfg["cols"]
    cc = cfg["category"]
    items = []
    for n, row in enumerate(rows, 1):
        if not row or all(c is None for c in row):
            continue
        q = clean(row[qc]) if qc < len(row) else ""
        a = clean(row[ac]) if ac < len(row) else ""
        if len(q) < 4 or len(a) < 4:
            continue
        if is_offline_answer(a) or _HEADER_LIKE.match(q) or _NON_QUESTION.search(q):
            continue
        item = {
            "index": n,
            "section": cfg["section"],
            "query": f"{cfg['prefix']}-{q}",
            "gold_answer": a,
        }
        if cc is not None and cc < len(row) and row[cc]:
            item["category"] = str(row[cc]).strip()
        items.append(item)
    return items


def main() -> int:
    parser = argparse.ArgumentParser(description="从 MCX/定位产品 问答表构建测试集")
    parser.add_argument("--out", default="mcx_loc_all.jsonl", help="输出 jsonl 相对 eval_datasets")
    args = parser.parse_args()

    all_items = []
    for cfg in (MCX, LOC):
        items = build_table(cfg)
        all_items.extend(items)
        print(f"[{cfg['section']}] {cfg['file'].name} → {len(items)} 题")
        for it in items:
            tag = f"[{it['category']}] " if "category" in it else ""
            print(f"  #{it['index']:>2} {tag}{it['query'][:48]}")

    out = BASE / args.out
    with open(out, "w", encoding="utf-8") as f:
        for item in all_items:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
    print(f"\n共 {len(all_items)} 题 -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
