#!/usr/bin/env python3
"""从引用准确率评分报告里挑出「没有可判定引用条目」的题，落成补测题集。

评分报告里 `entries` 为空的题有两类：答案里解析不出任何引用标注（含拒答/澄清），
以及标了来源但对不上本轮检索片段。后者多为取证缺口——跑批脚本当时没采集
`search_file` 结果，`find_kb_document`/`open_kb_document` 的窗口片段取不到文件名，
引用证据整批丢失（前端来源面板不受影响）。这批题只能用修好采集逻辑的 runner 重跑一遍，
才拿得到可归因的检索片段，进而进入引用准确率的分母。

用法：
    python3 build_citation_backfill_testset.py \
        --report reports/citation_accuracy_citation507_20260820_answered.json \
        --output citation507_unresolved78.jsonl
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="构建引用条目缺失题的补测题集")
    parser.add_argument("--report", required=True, help="引用准确率评分报告 JSON")
    parser.add_argument("--output", required=True, help="输出 JSONL 路径")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = json.loads(Path(args.report).read_text(encoding="utf-8"))
    pending = [item for item in report["items"] if not item["entries"]]
    records = [
        {
            "index": index,
            "section": item.get("section") or "",
            "query": item["query"],
            "gold_answer": item.get("gold_answer") or "",
            "source_index": item.get("index"),
        }
        for index, item in enumerate(pending, 1)
    ]

    output = Path(args.output)
    output.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records), encoding="utf-8"
    )
    sections: dict[str, int] = {}
    for record in records:
        sections[record["section"]] = sections.get(record["section"], 0) + 1
    print(f"待补测 {len(records)} 道（{len(sections)} 个分区：{sections}）→ {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
