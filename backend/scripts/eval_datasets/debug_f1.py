#!/usr/bin/env python3
"""演示 answer_correctness 的 F1 逐句判定（内部诊断，不交付）。

对指定一题：跑 StatementGeneratorPrompt 拆句 + CorrectnessClassifier 判 TP/FP/FN，
打印完整判定结果与 F1，说明为何语义相似度高但 answer_correctness 低。
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "package"))

from yuxi.knowledge.eval.ragas_eval import build_judge_llm

sys.path.insert(0, "/usr/local/lib/python3.13/site-packages")


async def main() -> None:
    idx = int(sys.argv[1]) if len(sys.argv) > 1 else 28
    row = None
    for f in ["kb_3cm2gz6tyb", "kb_mvng8u1201", "kb_0368jjmecb"]:
        p = Path(__file__).resolve().parent / "final" / f"{f}_final.jsonl"
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip() and json.loads(line).get("index") == idx:
                row = json.loads(line)
    if row is None:
        print(f"idx {idx} 未找到")
        return

    judge = build_judge_llm("deepseek:deepseek-v4-flash", max_tokens=8192)
    from ragas.metrics._answer_correctness import CorrectnessClassifier, QuestionAnswerGroundTruth
    from ragas.metrics._faithfulness import StatementGeneratorInput, StatementGeneratorPrompt

    q = row["query"]
    agent, gold = row["agent_answer"], row["gold_answer"]

    sg = StatementGeneratorPrompt()
    print(f"=== idx {idx} | {q} | sim 参考见 semantic_sim ===")
    out_r = await sg.generate(llm=judge, data=StatementGeneratorInput(question=q, answer=agent))
    out_g = await sg.generate(llm=judge, data=StatementGeneratorInput(question=q, answer=gold))
    st_r = list(out_r.statements)
    st_g = list(out_g.statements)
    print(f"Agent 回答拆出 {len(st_r)} 句 | gold 拆出 {len(st_g)} 句")

    cls = CorrectnessClassifier()
    ans = await cls.generate(
        llm=judge,
        data=QuestionAnswerGroundTruth(question=q, answer=st_r, ground_truth=st_g),
    )
    tp, fp, fn = list(ans.TP), list(ans.FP), list(ans.FN)
    print(f"\nTP={len(tp)} FP={len(fp)} FN={len(fn)}")
    f1 = 2 * len(tp) / (2 * len(tp) + len(fp) + len(fn)) if (tp or fp or fn) else 1.0
    print(f"F1 = 2·{len(tp)}/(2·{len(tp)}+{len(fp)}+{len(fn)}) = {f1:.3f}")

    print("\n—— Agent 陈述中被判定为【命中 gold】(TP) ——")
    for s in tp:
        print(f"  ✓ {s.statement[:70]}")
    print("\n—— Agent 陈述中被判定为【gold 没有】(FP，扣分项） ——")
    for s in fp[:12]:
        print(f"  ✗ {s.statement[:70]}")
    print(f"  …… 共 {len(fp)} 条" if len(fp) > 12 else "")
    print("\n—— gold 陈述被判定为【Agent 没覆盖】(FN，扣分项） ——")
    for s in fn[:12]:
        print(f"  ▲ {s.statement[:70]}")
    print(f"  …… 共 {len(fn)} 条" if len(fn) > 12 else "")


if __name__ == "__main__":
    asyncio.run(main())
