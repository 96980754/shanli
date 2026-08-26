#!/usr/bin/env python3
"""诊断：对单题重算 answer_relevancy，打印 judge 生成的问题（内部工具，不交付甲方）。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from yuxi.knowledge.eval.ragas_eval import build_embedding_adapter, build_judge_llm, build_ragas_metrics
from yuxi.storage.postgres.manager import pg_manager

BASE = Path("/app/scripts/eval_datasets")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--query", required=True, help="要诊断的问题（需在 final 合并文件中找到）")
    p.add_argument("--judge-max-tokens", type=int, default=8192, help="judge LLM 输出上限")
    p.add_argument("--no-cache", action="store_true", help="关闭 ragas 磁盘缓存")
    return p.parse_args()


async def run(args: argparse.Namespace) -> int:
    pg_manager.initialize()
    try:
        from yuxi import config as sys_config
        from ragas import SingleTurnSample
        from ragas.metrics import answer_relevancy
        from ragas.run_config import RunConfig

        judge_spec = getattr(sys_config, "default_model", None)
        if not judge_spec:
            print("无法确定 judge 模型")
            return 1
        cache = None if args.no_cache else __import__("ragas").cache.DiskCacheBackend(
            cache_dir="/app/saves/ragas_cache"
        )
        judge = build_judge_llm(judge_spec, cache=cache, max_tokens=args.judge_max_tokens)
        emb = build_embedding_adapter("siliconflow-cn:Pro/BAAI/bge-m3")
        print(f"judge: {judge_spec} | max_tokens: {args.judge_max_tokens} | cache: {not args.no_cache}")

        record = None
        for f in sorted(Path(BASE / "final").glob("*_final.jsonl")):
            for line in f.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                r = json.loads(line)
                if r.get("query") == args.query:
                    record = r
                    break
            if record:
                break
        if not record:
            print("final 合并文件中找不到该问题")
            return 1

        sample = SingleTurnSample(
            user_input=record["query"],
            response=record.get("agent_answer") or "",
            retrieved_contexts=[c.get("content", "") for c in record.get("retrieved_chunks") or []],
            reference=record.get("gold_answer"),
        )
        metric = answer_relevancy
        metric.llm = judge
        metric.embeddings = emb

        # 直接生成「答案可回答的问题」，看 judge 认为答案是否回答了具体问题（noncommittal）
        prompt_input = metric.question_generation.input_model(response=sample.response)
        responses = await metric.question_generation.generate_multiple(
            data=prompt_input, llm=judge, n=metric.strictness
        )
        print(f"\nstrictness={metric.strictness}，judge 生成 {len(responses)} 个（问题, noncommittal）：")
        for r in responses:
            print(f"  - question={r.question!r} noncommittal={r.noncommittal}")

        # 与原始问题做语义相似度
        sim = metric.calculate_similarity(sample.user_input, [r.question for r in responses])
        all_nc = all(r.noncommittal for r in responses)
        print(f"\n余弦相似度: {[round(float(s), 4) for s in sim]} | mean={float(sim.mean()):.4f} | all_noncommittal={all_nc}")
        print(f"  -> score = mean × int(not all_noncommittal) = {float(sim.mean()) * (not all_nc):.4f}")

        score = await metric.single_turn_ascore(sample)
        print(f"\nanswer_relevancy（完整指标）= {score}")
        return 0
    finally:
        await pg_manager.close()


def main() -> int:
    import asyncio
    import sys

    try:
        return asyncio.run(run(parse_args()))
    except ModuleNotFoundError as e:
        if "ragas" in str(e):
            print("未安装 ragas，请先 docker exec api-dev uv sync --group eval", file=sys.stderr)
            return 2
        raise


if __name__ == "__main__":
    import sys

    sys.exit(main())
