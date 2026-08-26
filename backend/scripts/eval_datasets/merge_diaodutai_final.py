#!/usr/bin/env python3
"""调度台最终合并：83 题测试集 → 每题取最高优先级结果（批次 > 冻结前重跑 > 旧 8/19 good）。

优先级：同一 query，批次结果（新鲜）优先；其次冻结中断那轮的成功记录；最后旧 8/19 的 good。
输出 agent_e2e_diaodutai_all_20260820.jsonl（83 行，含 index/section/gold_answer）供评分。
"""

from __future__ import annotations

import json
from pathlib import Path

BASE = Path(__file__).resolve().parent
TESTSET = BASE / "调度台_all.jsonl"
OLD = BASE / "synthetic" / "agent_e2e_diaodutai_all_20260819.jsonl"
FROZEN = BASE / "synthetic" / "agent_e2e_diaodutai_retry60_20260820.jsonl"
BATCHES = [BASE / "synthetic" / f"agent_e2e_diaodutai_batch{i}_20260820.jsonl" for i in (1, 2, 3, 4)] + [
    BASE / "synthetic" / "agent_e2e_diaodutai_retry2_20260820.jsonl"
]
OUT = BASE / "agent_e2e_diaodutai_all_20260820.jsonl"


def load(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def good_of(rows: list[dict]) -> dict[str, dict]:
    return {r["query"]: r for r in rows if "error" not in r and (r.get("agent_answer") or "").strip()}


def main() -> int:
    testset = load(TESTSET)
    old_good = good_of(load(OLD))
    frozen_good = good_of(load(FROZEN))
    batch_by_q: dict[str, dict] = {}
    for b in BATCHES:
        batch_by_q.update(good_of(load(b)))

    merged = []
    for item in testset:
        q = item["query"]
        rec = batch_by_q.get(q) or frozen_good.get(q) or old_good.get(q)
        if rec is None:
            print(f"警告: {q} 无任何成功结果，保留占位")
            rec = {"query": q, "error": "无成功结果"}
        rec = dict(rec)
        rec["index"] = item["index"]
        rec["section"] = item.get("section") or rec.get("section") or "调度台"
        rec["gold_answer"] = item.get("gold_answer") or rec.get("gold_answer") or ""
        merged.append(rec)

    merged.sort(key=lambda r: r["index"])
    OUT.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in merged), encoding="utf-8")

    ok = [r for r in merged if "error" not in r]
    answered = [r for r in ok if (r.get("agent_answer") or "").strip()]
    failed = [r for r in merged if "error" in r]
    print(f"最终合并: {len(merged)} 题（成功 {len(ok)}，有答案 {len(answered)}，失败 {len(failed)}）")
    for r in failed:
        print(f"  failed: {r.get('query')} → {str(r.get('error'))[:80]}")
    print(f"写出: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
