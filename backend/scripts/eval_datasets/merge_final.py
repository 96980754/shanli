#!/usr/bin/env python3
"""合并首轮成功 / retry1 / retry4/5/6 为每库最终评分文件（内部工具）。

以 testset_95 为基准（95 题，从 100 题移除九、图片识别/流程/系统对接类 5 题），把各轮
真实 Agent 运行结果按 query 归一化：
- retry7（42/45/53 改题重测的 3 条子集，最新）优先；
- 其次 rev25（甲方修订 34 题后重跑的 25 条变更子集）；
- 其次 retry6（覆盖 retry5 中 4 条检索服务故障）；
- 其次 retry5（覆盖 402 余额污染 + 2 cancelled + 8 DNS 失败）；
- 其次 retry4（覆盖 retry1 中 2 cancelled + 8 DNS 失败）；
- 其次是首轮 17 条成功；
- 其余取 retry1。
产物：synthetic/final/<kb_id>_final.jsonl，供 score_agent_results.py 逐库评分。

说明：testset_95 无 gold_chunk_ids，故统一置空，评分端引用正确率输出 N/A。
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

BASE = Path(__file__).resolve().parent
TEST = BASE / "testset_95.jsonl"
S1 = BASE / "synthetic" / "agent_e2e_20260817_success.jsonl"
R1 = BASE / "synthetic" / "agent_e2e_20260818_retry.jsonl"
R4 = BASE / "synthetic" / "agent_e2e_retry4_10.jsonl"
R5 = BASE / "synthetic" / "agent_e2e_retry5_29.jsonl"
R6 = BASE / "agent_e2e_retry6_4.jsonl"
R7 = BASE / "synthetic" / "agent_e2e_rev25_0818.jsonl"
R8 = BASE / "synthetic" / "agent_e2e_retry7_3.jsonl"
OUT = BASE / "final"


def load(p: Path) -> list[dict]:
    return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]


# deepseek 余额不足/模型调用失败/检索服务故障等错误文本被当作 agent_answer 记录的污染模式
_POLLUTED = (
    "Model call failed",
    "Insufficient Balance",
    "Error code:",
    "APIStatusError",
    "Traceback",
    "调用失败",
    "知识库服务暂时不可用",
    "服务暂时不可用",
)


def is_polluted(answer: str) -> bool:
    return any(p in answer for p in _POLLUTED)


def main() -> None:
    testset = load(TEST)
    s1 = {r["query"]: r for r in load(S1)}
    r1 = {r["query"]: r for r in load(R1)}
    r4 = {r["query"]: r for r in load(R4)}
    r5 = {r["query"]: r for r in load(R5)}
    r6 = {r["query"]: r for r in load(R6)}
    r7 = {r["query"]: r for r in load(R7)} if R7.exists() else {}
    r8 = {r["query"]: r for r in load(R8)} if R8.exists() else {}

    rows: list[dict] = []
    missing: list[str] = []
    for t in testset:
        q = t["query"]
        result = r8.get(q) or r7.get(q) or r6.get(q) or r5.get(q) or r4.get(q) or s1.get(q) or r1.get(q)
        if result is None:
            missing.append(q)
            continue
        err = result.get("error", "")
        agent_answer = result.get("agent_answer") or ""
        if agent_answer and is_polluted(agent_answer):
            status = "failed"  # 模型调用失败文本被当答案：按失败处理，避免把垃圾文本当真实回答评分
        elif agent_answer:
            status = "answered"
        elif str(err).startswith("run 未完成: interrupted"):
            status = "clarify"
        elif err:
            status = "failed"
        else:
            status = "no_answer"
        rows.append(
            {
                "index": t.get("index"),
                "query": q,
                "section": t.get("section", ""),
                "kb_id": result.get("kb_id") or t.get("kb_id"),
                "gold_answer": t.get("gold_answer", ""),
                "agent_answer": agent_answer,
                "retrieved_chunks": result.get("retrieved_chunks") or [],
                "gold_chunk_ids": [],
                "status": status,
                "exclude_reason": t.get("exclude_reason", ""),
            }
        )

    print(f"total: {len(rows)} | missing: {missing or '无'}")
    print("status:", dict(Counter(r["status"] for r in rows)))

    OUT.mkdir(parents=True, exist_ok=True)
    for kb in sorted({r["kb_id"] for r in rows}):
        krows = sorted((r for r in rows if r["kb_id"] == kb), key=lambda r: (r["index"] is None, r["index"]))
        p = OUT / f"{kb}_final.jsonl"
        p.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in krows), encoding="utf-8")
        print(f"{kb}: {len(krows)} 题 {dict(Counter(r['status'] for r in krows))} -> {p.relative_to(BASE)}")


if __name__ == "__main__":
    main()
