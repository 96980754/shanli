#!/usr/bin/env python3
"""把评测 runner 的结果 jsonl 导出成 Excel 详细输出表（每个数据集一个 sheet）。

一题一行，逐题给出问题、标准答案、系统答案、回答类型/拒答原因、检索片段数与耗时，
供人工翻阅与横向对比。不含评分列（答案正确性等由 score_agent_results.py 另行产出）。

一个 sheet 可以指定多个来源（用 `;` 分隔，每个写成 `来源标签=结果.jsonl`）：
以第一个来源的题目顺序与题集为准，后面来源里的同题**覆盖**前面的，并在「数据来源」
列标明该行最终取自哪次运行。用于把某次运行的故障题用另一次重跑结果补齐。

用法（宿主或容器内均可）：
    python scripts/export_agent_eval_xlsx.py \
        --sheet MCX:9/10=scripts/eval_datasets/agent_e2e_mcx30_glm53flash_20260910.jsonl \
        --sheet "安卓:9/10=.../agent_e2e_终端-安卓_glm53flash_20260910.jsonl;9/12 重跑=.../回归_安卓35.jsonl" \
        --output scripts/eval_datasets/reports/评测详细输出_20260912.xlsx
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

COLUMNS = [
    ("题号", 6),
    ("问题", 46),
    ("标准答案", 34),
    ("系统答案", 90),
    ("回答类型", 16),
    ("拒答原因", 20),
    ("检索片段数", 11),
    ("耗时秒", 9),
    ("线程ID", 38),
    ("Run ID", 38),
    ("数据来源", 14),
]

HEADER_FILL = PatternFill("solid", fgColor="DDEBF7")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="导出评测详细输出 Excel")
    parser.add_argument(
        "--sheet",
        action="append",
        required=True,
        metavar="名称:来源[;来源]",
        help="数据集来源（可重复，按给定顺序生成 sheet）；来源写成 标签=结果.jsonl，"
        "多个来源用 ; 分隔，后面的同题覆盖前面的",
    )
    parser.add_argument("--output", required=True, help="输出的 xlsx 路径")
    return parser.parse_args()


def parse_sources(spec: str) -> list[tuple[str, Path]]:
    """把 `标签=路径[;标签=路径]` 解析成来源列表；未写标签时标签为空。"""
    sources = []
    for part in spec.split(";"):
        label, sep, path = part.partition("=")
        if not sep:
            label, path = "", label
        sources.append((label.strip(), Path(path.strip())))
    return sources


def load_records(sources: list[tuple[str, Path]]) -> list[tuple[str, dict[str, Any]]]:
    """按来源顺序载入，以第一个来源的题目顺序为准，后面的同题覆盖前面的。"""
    order: list[str] = []
    merged: dict[str, tuple[str, dict[str, Any]]] = {}
    for label, path in sources:
        with path.open(encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                record = json.loads(line)
                query = record.get("query") or ""
                if query not in merged:
                    order.append(query)
                merged[query] = (label, record)
    return [merged[query] for query in order]


def row_of(record: dict[str, Any], index: int, label: str) -> list[Any]:
    disposition = record.get("knowledge_disposition") or {}
    return [
        index,
        record.get("query") or "",
        record.get("gold_answer") or "",
        record.get("agent_answer") or "",
        disposition.get("type") or "",
        disposition.get("reason") or "",
        len(record.get("retrieved_chunks") or []),
        record.get("elapsed_s"),
        record.get("thread_id") or "",
        record.get("run_id") or "",
        label,
    ]


def write_sheet(workbook: Workbook, name: str, sources: list[tuple[str, Path]]) -> int:
    records = load_records(sources)

    sheet = workbook.create_sheet(title=name)
    sheet.append([title for title, _ in COLUMNS])
    for cell in sheet[1]:
        cell.font = Font(bold=True)
        cell.fill = HEADER_FILL
        cell.alignment = Alignment(vertical="center", horizontal="center")
    for column, (_, width) in enumerate(COLUMNS, start=1):
        sheet.column_dimensions[get_column_letter(column)].width = width

    for index, (label, record) in enumerate(records, start=1):
        sheet.append(row_of(record, index, label))
    for row in sheet.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)

    sheet.freeze_panes = "A2"
    return len(records)


def main() -> int:
    args = parse_args()
    workbook = Workbook()
    workbook.remove(workbook.active)

    for spec in args.sheet:
        name, _, rest = spec.partition(":")
        if not rest:
            raise SystemExit(f"--sheet 参数需为 名称:来源 形式，收到：{spec}")
        sources = parse_sources(rest)
        count = write_sheet(workbook, name, sources)
        via = "、".join(label or path.name for label, path in sources)
        print(f"{name}：{count} 题（来源：{via}）")

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(output)
    print(f"已写入 {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
