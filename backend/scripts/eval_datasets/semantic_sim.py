#!/usr/bin/env python3
"""计算「Agent 答案 vs gold 参考答案」的 bge-m3 语义相似度（内部诊断工具）。

对比现行 answer_correctness（F1 语句比对 + 语义相似度混合），
回答「若按纯语义相似度评分，结果会怎样」。
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "package"))

from yuxi.knowledge.eval.ragas_eval import build_embedding_adapter

FILES = ["kb_3cm2gz6tyb", "kb_mvng8u1201", "kb_0368jjmecb"]
REPORT = Path(__file__).resolve().parent / "reports" / "accuracy_report_20260818_rev.json"


def cos(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b)) / (
        math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    )


def main() -> None:
    rows: list[dict] = []
    for kb in FILES:
        p = Path(__file__).resolve().parent / "final" / f"{kb}_final.jsonl"
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                r["_kb"] = kb
                rows.append(r)

    answered = [r for r in rows if (r.get("agent_answer") or "").strip() and (r.get("gold_answer") or "").strip()]
    emb = build_embedding_adapter("siliconflow-cn:Pro/BAAI/bge-m3")

    def batch(texts: list[str], bs: int = 32) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), bs):
            out.extend(emb.embed_documents(texts[i : i + bs]))
        return out

    av = batch([r["agent_answer"] for r in answered])
    gv = batch([r["gold_answer"] for r in answered])
    sims = [cos(a, g) for a, g in zip(av, gv)]

    rep = json.loads(REPORT.read_text(encoding="utf-8"))
    nb = {r["query"]: r for r in rep["items"]}
    just = {r["query"] for r in rep["justified_refusals"]}

    raw = sum(sims) / len(sims)
    adj = sum(1.0 if r["query"] in just else s for r, s in zip(answered, sims)) / len(sims)
    print(f"已评分 answered 行: {len(answered)} / 100（另 {100-len(answered)} 条无答案不计）")
    print(f"语义相似度均值（原始）      : {raw:.4f} = {raw*100:.1f}%")
    print(f"语义相似度均值（缺口题按1.0）: {adj:.4f} = {adj*100:.1f}%")
    print(f"现行 answer_correctness     : {rep['metrics']['answer_correctness']:.4f} = {rep['metrics']['answer_correctness']*100:.1f}%")
    for kb in FILES:
        vals = [s for r, s in zip(answered, sims) if r["_kb"] == kb]
        if vals:
            print(f"  {kb}: {len(vals)} 题 语义sim={sum(vals)/len(vals)*100:.1f}%")

    print("\n== 阈值达标率 ==")
    scored = [(r, s, 1.0 if r["query"] in just else s) for r, s in zip(answered, sims)]
    for th in (0.7, 0.8):
        n1 = sum(1 for _, s, _ in scored if s >= th)
        n2 = sum(1 for _, _, s2 in scored if s2 >= th)
        print(f"  阈值 {th:.1f}: 原始 {n1}/93 ({n1/93*100:.0f}%) | 缺口题按1.0 {n2}/93 ({n2/93*100:.0f}%)")
    n = sum(1 for _, _, s2 in scored if s2 >= 0.8)
    print(f"  语义口径 ≥80%（缺口题按1.0）: {n}/93 = {n/93*100:.0f}% 达标")
    accs = [r["ragas_metrics"].get("answer_correctness") for r in rep["items"]]
    have = [a for a in accs if a is not None]
    print(f"  现行 answer_correctness ≥80%: {sum(1 for a in have if a>=0.8)}/{len(have)} = {sum(1 for a in have if a>=0.8)/len(have)*100:.0f}% 达标")

    print("\n== 语义相似度高(≥0.7)但 answer_correctness 低(<0.45) 的行 ==")
    for r, s in zip(answered, sims):
        n = nb.get(r["query"])
        acc = n["ragas_metrics"].get("answer_correctness") if n else None
        if s >= 0.7 and acc is not None and acc < 0.45:
            print(f"  idx={r.get('index'):>3} sim={s:.2f} acc={acc:.2f} | {r['query'][:36]}")

    print("\n== 差距最大的 15 行（sim - acc 降序）==")
    diff = []
    for r, s in zip(answered, sims):
        n = nb.get(r["query"])
        acc = n["ragas_metrics"].get("answer_correctness") if n else None
        if acc is not None:
            diff.append((round(s - acc, 2), s, acc, r.get("index"), r["query"][:30]))
    for d, s, acc, i, q in sorted(diff, reverse=True)[:15]:
        print(f"  idx={i:>3} sim={s:.2f} acc={acc:.2f} 差={d:+.2f} | {q}")


if __name__ == "__main__":
    main()
