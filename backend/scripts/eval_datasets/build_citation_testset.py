#!/usr/bin/env python3
"""从 507 题客服/终端/定位题集分层抽样，构建引用准确率评测题集（内部工具）。

507 题分散在 7 个 jsonl 里（8 个域，mcx_loc_all.jsonl 含 MCX 与定位产品两域），
字段统一为 index/section/query/gold_answer。按 section 分层抽样保证抽出的题
覆盖全部业务域，固定 seed 保证验收数字可复现——手工挑题无法重跑复现。

输出默认写 eval_datasets/citation{N}.jsonl（synthetic/ 属容器 root，宿主不可写）。

用法（宿主机，无需容器）：
  python3 build_citation_testset.py --sample 50 --seed 42
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

BASE = Path(__file__).resolve().parent

# 507 题的来源文件；mcx_loc_all.jsonl 含 MCX 与定位产品两个 section
SOURCE_FILES = (
    "运营平台_all.jsonl",
    "调度台_all.jsonl",
    "终端-安卓_all.jsonl",
    "终端-cat1_all.jsonl",
    "MDM_all.jsonl",
    "miniserver_all.jsonl",
    "mcx_loc_all.jsonl",
)


def load_by_section() -> dict[str, list[dict]]:
    """读全部来源文件，按 section 分组。"""
    by_section: dict[str, list[dict]] = {}
    for filename in SOURCE_FILES:
        path = BASE / filename
        with open(path, encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                item = json.loads(line)
                by_section.setdefault(item["section"], []).append(item)
    return by_section


def allocate_slots(by_section: dict[str, list[dict]], sample: int) -> dict[str, int]:
    """按域分配名额：先均匀覆盖，余数给题量大的域。"""
    order = sorted(by_section, key=lambda name: -len(by_section[name]))
    base, rem = divmod(sample, len(order))
    return {name: base + (1 if i < rem else 0) for i, name in enumerate(order)}


def sample_items(by_section: dict[str, list[dict]], slots: dict[str, int], seed: int) -> list[dict]:
    """分层抽样并重新编号；同一域内保持随机顺序，域间按抽中题目数降序。"""
    rng = random.Random(seed)
    chosen: list[dict] = []
    for name in sorted(slots, key=lambda n: -len(by_section[n])):
        pool = by_section[name]
        for item in rng.sample(pool, min(slots[name], len(pool))):
            chosen.append(
                {
                    "index": len(chosen) + 1,
                    "section": item["section"],
                    "query": item["query"],
                    "gold_answer": item["gold_answer"],
                }
            )
    return chosen


def main() -> int:
    parser = argparse.ArgumentParser(description="从 507 题分层抽样构建引用准确率评测题集")
    parser.add_argument("--sample", type=int, default=50, help="抽样总题数（默认 50）")
    parser.add_argument("--seed", type=int, default=42, help="随机种子（默认 42，固定以保证可复现）")
    parser.add_argument("--out", default="", help="输出路径（默认 eval_datasets/citation{N}.jsonl）")
    args = parser.parse_args()

    by_section = load_by_section()
    slots = allocate_slots(by_section, args.sample)
    chosen = sample_items(by_section, slots, args.seed)

    out = Path(args.out) if args.out else BASE / f"citation{args.sample}.jsonl"
    with open(out, "w", encoding="utf-8") as f:
        for item in chosen:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")

    print(f"候选池 {sum(len(v) for v in by_section.values())} 题 / {len(by_section)} 个域")
    for name in sorted(slots, key=lambda n: -len(by_section[n])):
        print(f"  {name}: 池 {len(by_section[name])} -> 抽 {slots[name]}")
    print(f"\n共抽 {len(chosen)} 题 -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
