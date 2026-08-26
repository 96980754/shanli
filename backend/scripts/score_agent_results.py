#!/usr/bin/env python3
"""真实 Agent 端到端结果评分 CLI（内部工具）。

对 run_agent_e2e.py 产出的每题结果（系统答案 + 实际检索上下文）评分。
主指标 = RAGAS Answer Correctness（答案正确性，对照参考标准答案）；
另有忠实度 / 答案贴合度 / 上下文指标 / 引用正确率（gold_chunk_ids vs 实际检索），
但按业务决策「忠实度、贴合度不再计算」，默认只算答案正确性（--metrics 可显式选择）。

用法（容器内）：
    docker exec api-dev python /app/scripts/score_agent_results.py \
        --results kb_3cm2gz6tyb:/app/scripts/eval_datasets/synthetic/agent_e2e_20260817.jsonl \
        --results kb_mvng8u1201:/app/scripts/eval_datasets/synthetic/agent_e2e_20260817.jsonl

依赖：需先安装 eval 组（ragas）：`uv sync --group eval`。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "package"))

from yuxi import config as sys_config
from yuxi.knowledge.eval.agent_accuracy_report import is_justified_refusal, write_accuracy_reports
from yuxi.knowledge.eval.faithfulness_report import combine_results
from yuxi.knowledge.eval.metrics import RetrievalMetrics
from yuxi.knowledge.eval.ragas_eval import (
    build_embedding_adapter,
    build_judge_llm,
    build_ragas_metrics,
    score_sample,
)
from yuxi.storage.postgres.manager import pg_manager

DEFAULT_EMBEDDING_MODEL = "siliconflow-cn:Pro/BAAI/bge-m3"
DEFAULT_OUTPUT = "/app/scripts/eval_datasets/reports"
DEFAULT_CACHE_DIR = "/app/saves/ragas_cache"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="真实 Agent 端到端结果评分")
    parser.add_argument(
        "--results",
        action="append",
        required=True,
        metavar="KB_ID:path.jsonl",
        help="runner 结果与知识库配对（可重复）",
    )
    parser.add_argument("--judge-llm", help="judge 模型 spec（默认系统默认模型）")
    parser.add_argument(
        "--judge-max-tokens",
        type=int,
        default=8192,
        help="judge LLM 输出上限（deepseek 推理模型为共享推理+可见预算；长答案评分需足够大，"
        "默认 8192，避免 finish_reason=length 被 ragas 判为生成未完成）",
    )
    parser.add_argument("--embedding-model", default=DEFAULT_EMBEDDING_MODEL, help="embedding 模型 spec")
    parser.add_argument("--threshold", type=float, default=0.80, help="答案准确率内部目标")
    parser.add_argument("--concurrency", type=int, default=4, help="同时评分的题目数")
    parser.add_argument("--cache-dir", default=DEFAULT_CACHE_DIR, help="ragas 磁盘缓存目录（空串关闭）")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="报告输出目录")
    parser.add_argument("--name", default="", help="报告标题/文件名（默认当日日期）")
    parser.add_argument(
        "--no-context-metrics",
        action="store_true",
        help="跳过 context_precision/context_recall（两者对每个检索 chunk 单独调 judge LLM，耗时最大；"
        "管理报告主指标 answer_correctness 不依赖它们）",
    )
    parser.add_argument(
        "--metrics",
        default="answer_correctness",
        help="要计算的指标，逗号分隔（默认只算 answer_correctness；业务决策：忠实度/贴合度不再计算）。"
        "可选：answer_correctness / faithfulness / answer_relevancy / context_precision / context_recall",
    )
    return parser.parse_args()


def _citation_metrics(retrieved_chunks: list[dict], gold_chunk_ids: list[str]) -> dict:
    """gold_chunk_ids 与 Agent 实际检索到 chunk 的引用匹配度（recall@1/recall@5/f1@5）。"""
    retrieved_ids = [str(c.get("id") or c.get("chunk_id") or "") for c in retrieved_chunks if c.get("content")]
    if not retrieved_ids or not gold_chunk_ids:
        return {"citation_recall@1": None, "citation_recall@5": None, "citation_f1@5": None}
    return {
        "citation_recall@1": RetrievalMetrics.recall_at_k(retrieved_ids, gold_chunk_ids, 1),
        "citation_recall@5": RetrievalMetrics.recall_at_k(retrieved_ids, gold_chunk_ids, 5),
        "citation_f1@5": RetrievalMetrics.f1_score_at_k(retrieved_ids, gold_chunk_ids, 5),
    }


async def score_one(record: dict, metrics: list, semaphore: asyncio.Semaphore) -> dict:
    async with semaphore:
        query = record["query"]
        agent_answer = record.get("agent_answer") or ""
        gold_answer = record.get("gold_answer")
        retrieved_chunks = record.get("retrieved_chunks") or []
        if not agent_answer or not gold_answer:
            return {
                "index": None,
                "query": query,
                "ragas_metrics": _citation_metrics(retrieved_chunks, record.get("gold_chunk_ids") or []),
                "answer_scores": {},
                "retrieval_scores": {},
                "agent_answer": agent_answer,
                "gold_answer": gold_answer,
            }
        # 知识库缺口题的诚实拒答（gold 自认未记载 ∧ Agent 仅简短拒答）不调 judge：
        # 答案相关性结构性失真（拒答不对应原问题）故置 N/A、不计入主口径均值；
        # 答案正确性按正确（1.0）计入仅供参考（口径见 is_justified_refusal）。
        justified_refusal = is_justified_refusal(record)
        if justified_refusal:
            rag_scores = {"answer_correctness": 1.0, "faithfulness": None, "answer_relevancy": None}
        else:
            from ragas import SingleTurnSample

            sample = SingleTurnSample(
                user_input=query,
                retrieved_contexts=[c.get("content", "") for c in retrieved_chunks if c.get("content")],
                response=agent_answer,
                reference=gold_answer,
            )
            # 无检索上下文（如 Agent 走文件检索回退作答）时忠实度无从判定：置 None 而非算 0，
            # 避免把「文件检索作答」误判为「无据可查」而拖低忠实度均值。
            metrics_for_record = [m for m in metrics if m.name != "faithfulness"] if not retrieved_chunks else metrics
            rag_scores = await score_sample(sample, metrics_for_record)
            if not retrieved_chunks:
                rag_scores["faithfulness"] = None
        rag_scores.update(_citation_metrics(retrieved_chunks, record.get("gold_chunk_ids") or []))
        return {
            "index": None,
            "query": query,
            "ragas_metrics": rag_scores,
            "answer_scores": {},
            "retrieval_scores": {},
            "agent_answer": agent_answer,
            "gold_answer": gold_answer,
            "justified_refusal": justified_refusal,
            # 排除出主口径标记（如表述冗长题）：指标照常计算展示，但不计入聚合均值
            "exclude_from_aggregate": bool(record.get("exclude_reason")),
            "exclude_reason": record.get("exclude_reason", ""),
        }


async def run(args: argparse.Namespace) -> int:
    pg_manager.initialize()
    try:
        judge_spec = args.judge_llm or getattr(sys_config, "default_model", None)
        if not judge_spec:
            raise ValueError("无法确定 judge 模型，请用 --judge-llm 指定")

        judge_cache = None
        if args.cache_dir:
            from ragas.cache import DiskCacheBackend

            judge_cache = DiskCacheBackend(cache_dir=args.cache_dir)
        judge_llm = build_judge_llm(judge_spec, cache=judge_cache, max_tokens=args.judge_max_tokens)
        embedding_adapter = build_embedding_adapter(args.embedding_model)
        metrics = build_ragas_metrics(judge_llm, embedding_adapter, with_embedding_metrics=True)
        if args.no_context_metrics:
            metrics = [m for m in metrics if m.name not in ("context_precision", "context_recall")]
        requested = {s.strip() for s in args.metrics.split(",") if s.strip()}
        metrics = [m for m in metrics if m.name in requested]
        print(f"judge 模型: {judge_spec} | embedding: {args.embedding_model} | 指标: {[m.name for m in metrics]}")
        if judge_cache:
            print(f"已启用 ragas 磁盘缓存: {args.cache_dir}")

        pairs = []
        for item in args.results:
            if ":" not in item:
                raise ValueError(f"--results 格式应为 KB_ID:path.jsonl，收到: {item}")
            kb_id, path = item.split(":", 1)
            pairs.append((kb_id, path))

        semaphore = asyncio.Semaphore(max(1, args.concurrency))
        segments = []
        for kb_id, path in pairs:
            records = [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]
            if not records:
                print(f"{kb_id}: 结果文件为空，跳过")
                continue
            items = await asyncio.gather(*[score_one(r, metrics, semaphore) for r in records])
            for i, item in enumerate(items):
                item["index"] = i
            # 无答案/无参考答案的题不参与聚合（置 None 后 combine_results 自动跳过）
            segments.append({"kb_id": kb_id, "dataset": Path(path).stem, "results": {"metrics": {}, "items": items}})
            print(f"{kb_id}: 评分完成 {len(records)} 题")

        combined = combine_results(segments)
        if combined["total_items"] == 0:
            print("没有任何评分结果")
            return 1

        run_name = args.name or date.today().strftime("%Y%m%d")
        Path(args.output).mkdir(parents=True, exist_ok=True)
        json_path, md_path = write_accuracy_reports(
            combined, run_name=run_name, output_dir=args.output, threshold=args.threshold
        )
        print(f"答案准确率(Answer Relevancy): {combined['metrics'].get('answer_relevancy')}")
        print(f"报告已写入:\n  {json_path}\n  {md_path}")
        return 0
    finally:
        await pg_manager.close()


def main() -> int:
    args = parse_args()
    try:
        return asyncio.run(run(args))
    except ModuleNotFoundError as e:
        if "ragas" in str(e):
            print("未安装 ragas 依赖。请先运行: docker exec api-dev uv sync --group eval", file=sys.stderr)
            return 2
        raise
    except Exception as e:
        print(f"评分失败: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
