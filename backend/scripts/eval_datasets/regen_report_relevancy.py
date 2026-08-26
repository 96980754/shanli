#!/usr/bin/env python3
"""把「答案准确率」报告主口径切换到 Answer Relevancy 并重渲染（内部工具）。

数据来源：既有评分结果 JSON（每题的 answer_relevancy 等指标已算好，无需重评）。
以主口径为 answer_relevancy 的 report 模块逻辑重新渲染 Markdown + JSON。

用法（容器内）:
  docker exec -w /app/scripts/eval_datasets api-dev python regen_report_relevancy.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "package"))

from yuxi.knowledge.eval.agent_accuracy_report import write_accuracy_reports

SRC = Path(__file__).resolve().parent / "reports" / "accuracy_report_20260818_rev.json"
OUT = Path(__file__).resolve().parent / "reports"
RUN_NAME = "20260818_rel"
THRESHOLD = 0.80


def main() -> None:
    rep = json.loads(SRC.read_text(encoding="utf-8"))
    combined = {
        "metrics": rep["metrics"],
        "metric_counts": rep["metric_counts"],
        "total_items": rep["total_items"],
        "kbs": rep["kbs"],
        "items": rep["items"],
    }
    json_path, md_path = write_accuracy_reports(combined, run_name=RUN_NAME, output_dir=str(OUT), threshold=THRESHOLD)
    print(f"答案准确率(Answer Relevancy) = {rep['metrics'].get('answer_relevancy')}")
    print(f"有值题数: {rep['metric_counts'].get('answer_relevancy')} / {rep['total_items']}")
    print(f"已写入:\n  {json_path}\n  {md_path}")


if __name__ == "__main__":
    main()
