#!/usr/bin/env python3
"""按甲方评价筛选既有评分报告，重新出一份同口径的报告（不重跑判定）。

交付口径是「只统计系统实际作答的题」，而整批评分报告覆盖全部 507 题。本脚本复用既有报告里
已经判好的条目结论，只换分母重新汇总，避免为同一批数据再花一次判定成本。

用法：
    python /app/scripts/eval_datasets/filter_citation_report.py \
        --report  /app/scripts/eval_datasets/reports/citation_accuracy_citation507_20260820.json \
        --ratings /app/scripts/eval_datasets/aip_ratings.jsonl \
        --output  /app/scripts/eval_datasets/reports \
        --name    citation507_20260820_answered \
        --keep-rating 可接受 --keep-rating 勉强可接受 --keep-rating 不可接受
"""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path

SCORER_PATH = Path(__file__).with_name("score_citation_accuracy.py")


def load_scorer():
    spec = importlib.util.spec_from_file_location("score_citation_accuracy", SCORER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="按甲方评价筛选评分报告")
    parser.add_argument("--report", required=True, help="整批评分报告 JSON")
    parser.add_argument("--ratings", required=True, help="每行 {query, aip_rating} 的 JSONL")
    parser.add_argument("--output", required=True, help="报告输出目录")
    parser.add_argument("--name", required=True, help="新报告名")
    parser.add_argument(
        "--keep-rating",
        action="append",
        required=True,
        help="保留哪些甲方评价（可重复）；不在该清单里的题一律剔除",
    )
    return parser.parse_args()


def dedupe_by_query(items: list[dict]) -> list[dict]:
    """同一问题只计一次（保留首条）。

    507 题集里有两处问题文本重复（`miniserver-miniserver支持监听吗?`、
    `miniserver-Miniserver的OS（操作系统）是什么`），不去重会把同一问题算两遍，
    与合同「系统针对每个测试问题」的计数单位不符。
    """
    unique: dict[str, dict] = {}
    for item in items:
        unique.setdefault(item["query"], item)
    return list(unique.values())


def load_ratings(path: str) -> dict[str, str]:
    ratings = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            record = json.loads(line)
            ratings[record["query"]] = record.get("aip_rating") or ""
    return ratings


def main() -> int:
    args = parse_args()
    scorer = load_scorer()
    ratings = load_ratings(args.ratings)

    report = json.loads(Path(args.report).read_text(encoding="utf-8"))
    keep = set(args.keep_rating)
    kept = [item for item in report["items"] if ratings.get(item["query"]) in keep]
    items = dedupe_by_query(kept)
    print(
        f"保留 {len(kept)} 条记录（剔除 {len(report['items']) - len(kept)} 条，"
        f"保留评价：{'/'.join(args.keep_rating)}），去重后 {len(items)} 题"
    )

    overall = scorer.summarize(items, report["overall"]["threshold"])
    markdown, payload = scorer.build_report(items, overall, args.name, report["judge_llm"], report["results_path"])
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    (output / f"citation_accuracy_{args.name}.md").write_text(markdown, encoding="utf-8")
    (output / f"citation_accuracy_{args.name}.json").write_text(payload, encoding="utf-8")
    print(
        f"{args.name}: 引用正确 {overall['hit']}/{overall['denominator']}，"
        f"准确率 {100.0 * overall['accuracy']:.1f}%（阈值 ≥ 95.0%）"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
