#!/usr/bin/env python3
"""合并调度台 E2E：旧 83 题输出（23 good）+ 新 60 题重跑结果 → 完整 83 题合并结果。

规则：按 调度台_all.jsonl 的 index 1..83 对齐；index 在重跑集内的用新结果，否则用旧 good 记录。
输出合并后的 E2E JSONL 供 score_kefu_facts.py 评分。
"""

from __future__ import annotations

import json
from pathlib import Path

BASE = Path(__file__).resolve().parent
TESTSET = BASE / "调度台_all.jsonl"
OLD_E2E = BASE / "synthetic" / "agent_e2e_diaodutai_all_20260819.jsonl"
NEW_E2E = BASE / "synthetic" / "agent_e2e_diaodutai_retry60_20260820.jsonl"
OUT = BASE / "synthetic" / "agent_e2e_diaodutai_all_20260820.jsonl"


def load_jsonl(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            rows.append(json.loads(line))
    return rows


def main() -> int:
    testset = load_jsonl(TESTSET)
    old = load_jsonl(OLD_E2E)
    new = load_jsonl(NEW_E2E)

    new_by_q = {r["query"]: r for r in new}
    old_good = [r for r in old if "error" not in r]
    old_good_by_q = {r["query"]: r for r in old_good}

    merged = []
    used_new = used_old = 0
    for item in testset:
        q = item["query"]
        rec = new_by_q.get(q) or old_good_by_q.get(q)
        if not rec:
            print(f"警告: 未找到 {q} 的结果")
            continue
        # 规整公共字段（index/section/gold_answer 以测试集为准）
        rec = dict(rec)
        rec["index"] = item["index"]
        rec["section"] = item.get("section") or rec.get("section")
        rec["gold_answer"] = item.get("gold_answer") or rec.get("gold_answer")
        merged.append(rec)
        used_new += 1 if q in new_by_q else 0
        used_old += 1 if q in old_good_by_q and q not in new_by_q else 0

    merged.sort(key=lambda r: r["index"])
    OUT.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in merged), encoding="utf-8")

    good = [r for r in merged if "error" not in r]
    answered = [r for r in good if (r.get("agent_answer") or "").strip()]
    failed = [r for r in merged if "error" in r]
    print(f"合并完成: {len(merged)} 题（新结果 {used_new}，沿用旧 good {used_old}）")
    print(f"成功 {len(good)}（有答案 {len(answered)}），失败 {len(failed)}")
    print(f"写出: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
