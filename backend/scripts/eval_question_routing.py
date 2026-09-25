#!/usr/bin/env python3
"""问题路由判对率跑批 CLI（内部工具，甲方验收用）。

对标注集逐条跑 classify_complexity（规则层 + 可选小模型层），输出与人工标注的
一致率，用于验收「判对率 ≥85% 才启用小模型层，否则置空判定模型只跑规则」。

输入 JSONL，每行一个对象：
    {"question": "f10 的价格是多少", "label": "simple"}
    {"question": "对比一下 F10 和 P10 再给个选型建议", "label": "complex"}

用法（容器内）：
    docker exec api-dev python /app/scripts/eval_question_routing.py \
        --dataset /app/scripts/eval_datasets/question_routing.jsonl
    # 只评估规则层（不调小模型）：
    ... --rules-only
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "package"))

import yuxi.services.question_routing as question_routing
from yuxi.services.question_routing import classify_complexity

VALID_LABELS = {"simple", "complex"}


def _load_dataset(path: str) -> list[dict]:
    rows = []
    with open(path, encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if row.get("label") not in VALID_LABELS or not str(row.get("question", "")).strip():
                raise SystemExit(f"第 {line_no} 行格式错误（需 question + label∈simple|complex）: {line[:80]}")
            rows.append(row)
    return rows


async def _run(dataset: list[dict], *, rules_only: bool) -> int:
    if rules_only:
        question_routing.QUESTION_ROUTE_JUDGE_MODEL = ""
    mismatches = []
    tier_counts: dict[str, int] = {}
    for row in dataset:
        verdict = await classify_complexity(row["question"])
        tier_counts[verdict["tier"]] = tier_counts.get(verdict["tier"], 0) + 1
        if verdict["complexity"] != row["label"]:
            mismatches.append((row["question"], row["label"], verdict))

    total = len(dataset)
    accuracy = (total - len(mismatches)) / total if total else 0.0
    print(f"样本 {total} 条，判对 {total - len(mismatches)} 条，判对率 {accuracy:.1%}")
    print(f"判定层级分布: {tier_counts}")
    print(f"判定模型: {question_routing.QUESTION_ROUTE_JUDGE_MODEL or '（未配置，仅规则层）'}")
    if mismatches:
        print("\n不一致样本：")
        for question, label, verdict in mismatches:
            note = f"标注 {label} / 判定 {verdict['complexity']} | {verdict['tier']} | {verdict['reason']}"
            print(f"  [{note}] {question}")
    return 0 if accuracy >= 0.85 else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="问题路由判对率跑批")
    parser.add_argument("--dataset", required=True, help="标注集 JSONL 路径")
    parser.add_argument("--rules-only", action="store_true", help="只评估规则层（不调小模型）")
    args = parser.parse_args()
    dataset = _load_dataset(args.dataset)
    if not dataset:
        raise SystemExit("标注集为空")
    return asyncio.run(_run(dataset, rules_only=args.rules_only))


if __name__ == "__main__":
    sys.exit(main())
