#!/usr/bin/env python3
"""汇总「最大执行步数」扫参结果：把各档位的 run_agent_e2e 结果 + 评分报告拼成一张对照表。

扫参流程（每个档位三步，档位名以 `_<步数>` 结尾以便本脚本解析）：
    docker exec api-dev python /app/scripts/run_agent_e2e.py \
        --testset ... --name stepsweep_60 --max-steps 60 ...
    docker exec api-dev python /app/scripts/eval_datasets/score_kefu_facts.py \
        --e2e .../agent_e2e_stepsweep_60.jsonl --testset ... --name stepsweep_60
    docker exec api-dev python /app/scripts/eval_datasets/summarize_step_sweep.py

判读口径：
- 步数与作答分类取 run 记录里服务端自己的 disposition（真值），不取评分器的文本启发式；
- 未完成 = run 未 completed（GraphRecursionError → 终答为空、不落 disposition），这是硬红线：
  调低步数不能把「本可作答」变成「空白」；
- 答案正确性 = 评分器的「实质作答口径」均值，分母只是存活下来的实质作答题，
  **不能跨档位直接比较**（未完成的题被排除，分母变小会让正确性虚高）。
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from statistics import median

DEFAULT_E2E_DIR = "/app/scripts/eval_datasets/synthetic"
DEFAULT_REPORTS_DIR = "/app/scripts/eval_datasets/reports"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="汇总最大执行步数扫参结果")
    parser.add_argument("--e2e-dir", default=DEFAULT_E2E_DIR, help="run_agent_e2e 结果目录")
    parser.add_argument("--reports-dir", default=DEFAULT_REPORTS_DIR, help="评分报告目录")
    parser.add_argument("--prefix", default="agent_e2e_stepsweep_", help="扫参结果文件名前缀")
    return parser.parse_args()


def load_point(e2e_path: Path, reports_dir: Path) -> dict:
    """读一个档位：步数与作答分类取 E2E 结果里服务端自己的 disposition（真值），
    只有「正确性」取评分报告——那是评分器独有、且只在实质作答题上取均值的存活题口径。"""
    name = e2e_path.stem.removeprefix("agent_e2e_")
    records = [json.loads(line) for line in e2e_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    # 评分器报告带 kefu_facts_ 前缀，按后缀匹配避免写死前缀
    report_path = next(iter(reports_dir.glob(f"*{name}.json")), None)
    report = json.loads(report_path.read_text(encoding="utf-8")) if report_path else {}

    steps_by_kind: dict[str, list[int]] = {"answered": [], "knowledge_refusal": []}
    counts = {"answered": 0, "knowledge_refusal": 0, "unfinished": 0}
    for record in records:
        if record.get("run_status") != "completed":
            # 触顶/异常时服务端不落 disposition，这类题的答案为空
            counts["unfinished"] += 1
            continue
        kind = (record.get("disposition") or {}).get("type")
        steps = (record.get("steps") or {}).get("est_steps")
        if kind in steps_by_kind:
            counts[kind] += 1
            if steps is not None:
                steps_by_kind[kind].append(steps)

    return {
        "name": name,
        "steps_limit": int(re.search(r"_(\d+)$", name).group(1)) if re.search(r"_(\d+)$", name) else None,
        "count": len(records),
        "counts": counts,
        "mean_score": report.get("overall", {}).get("mean"),
        "steps": steps_by_kind,
    }


def find_points(e2e_dir: Path, prefix: str) -> list[Path]:
    return sorted(e2e_dir.glob(f"{prefix}*.jsonl"), key=lambda p: _sort_key(p.stem))


def _sort_key(stem: str) -> int:
    match = re.search(r"_(\d+)$", stem)
    return int(match.group(1)) if match else 10**9


def render(points: list[dict]) -> str:
    """判读提醒：正确性只在「实质作答」的题目上取均值，未完成的题被排除在外，
    所以正确性升高往往只是分母变小——红线是「未完成题数」，不是正确性。"""
    lines = [
        "| 步数上限 | 完成题 | 未完成(空白答案) | 作答 | 拒答 | 作答中位步数 | 拒答中位步数 | 正确性(存活题口径) |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for p in points:
        counts = p["counts"]
        mean = p["mean_score"]
        lines.append(
            f"| {p['steps_limit'] if p['steps_limit'] is not None else p['name']} | "
            f"{p['count'] - counts['unfinished']}/{p['count']} | {counts['unfinished']} | "
            f"{counts['answered']} | {counts['knowledge_refusal']} | "
            f"{median(p['steps']['answered']) if p['steps']['answered'] else '—'} | "
            f"{median(p['steps']['knowledge_refusal']) if p['steps']['knowledge_refusal'] else '—'} | "
            f"{f'{mean * 100:.1f}%' if mean is not None else '—'} |"
        )
    lines.append("")
    lines.append(
        "注：未完成的题服务端不落中间消息，`steps` 恒为 1（只有那条空的终答），"
        "故「超了多少步」无法测量；这张表的步数只覆盖完成的题。"
    )
    return "\n".join(lines)


def main() -> int:
    args = parse_args()
    points = [load_point(p, Path(args.reports_dir)) for p in find_points(Path(args.e2e_dir), args.prefix)]
    if not points:
        print(f"未找到扫参结果：{args.e2e_dir}/{args.prefix}*.jsonl")
        return 1
    print(render(points))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
