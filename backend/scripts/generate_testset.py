#!/usr/bin/env python3
"""RAGAS 合成测试集生成 CLI（内部工具）。

从知识库 postgres 已入库 chunks 用 RAGAS TestsetGenerator 自动生成
「问题 + 参考答案 + 引用 chunk id」测试集 JSONL，供真实 Agent 端到端测试与评分使用。
测试集基于知识库自动生成，覆盖全部业务场景，客观可追溯，可人工抽查。

用法：
    # 默认按全部知识库 chunk 比例生成共 150 题
    docker exec api-dev python /app/scripts/generate_testset.py --total 150

    # 指定库与每题数量（--kb 可重复）
    docker exec api-dev python /app/scripts/generate_testset.py \
        --kb kb_3cm2gz6tyb:40 --kb kb_mvng8u1201:30 --kb kb_0368jjmecb:20

依赖：需先安装 eval 组（ragas）：`uv sync --group eval`。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "package"))

from yuxi import config as sys_config
from yuxi.knowledge.eval.ragas_eval import build_judge_llm
from yuxi.knowledge.eval.ragas_testset_gen import (
    build_langchain_embeddings,
    generate_testset_jsonl,
    write_testset_jsonl,
)
from yuxi.storage.postgres.manager import pg_manager

DEFAULT_EMBEDDING_MODEL = "siliconflow-cn:Pro/BAAI/bge-m3"
DEFAULT_OUTPUT = "/app/scripts/eval_datasets/synthetic"
DEFAULT_TOTAL = 150


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="RAGAS 合成测试集生成")
    parser.add_argument("--kb", action="append", default=None, metavar="KB_ID:SIZE", help="指定库与题数（可重复）")
    parser.add_argument("--total", type=int, default=DEFAULT_TOTAL, help="全部测试题总数（按各库 chunk 比例分配）")
    parser.add_argument("--judge-llm", help="生成 LLM spec（默认系统默认模型）")
    parser.add_argument(
        "--max-tokens",
        type=int,
        default=65536,
        help="生成 LLM 输出上限（默认 65536）。deepseek-v4-flash 为推理型模型，max_tokens 是"
        "「推理+可见输出」共享预算：默认过小（如 4096/8192）时推理先耗尽预算、可见输出被饿死，"
        "ragas 收到截断响应判为 LLMDidNotFinish 导致整个库生成失败，故需给足预算。",
    )
    parser.add_argument("--embedding-model", default=DEFAULT_EMBEDDING_MODEL, help="embedding 模型 spec")
    parser.add_argument("--concurrency", type=int, default=16, help="RAGAS 生成并发（默认 16）")
    parser.add_argument(
        "--max-chunks",
        type=int,
        default=500,
        help="每库喂给生成器的 chunk 上限（按文件均摊采样，约束大库 NER transform 成本，默认 500）",
    )
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="测试集输出目录")
    return parser.parse_args()


async def _parse_kb_pairs(args: argparse.Namespace) -> list[tuple[str, int]]:
    """解析 --kb KB_ID:SIZE 列表；未指定时按全部知识库 chunk 比例分配 total 题。"""
    if args.kb:
        pairs = []
        for item in args.kb:
            if ":" not in item:
                raise ValueError(f"--kb 格式应为 KB_ID:SIZE，收到: {item}")
            kb_id, size = item.split(":", 1)
            pairs.append((kb_id, int(size)))
        if not pairs:
            raise ValueError("--kb 未指定任何配对")
        return pairs

    from sqlalchemy import func, select

    from yuxi.storage.postgres.models_knowledge import KnowledgeBase, KnowledgeChunk

    async with pg_manager.get_async_session_context() as session:
        kb_rows = (await session.execute(select(KnowledgeBase.kb_id))).scalars().all()
        counts: dict[str, int] = {}
        for kb_id in kb_rows:
            n = await session.execute(
                select(func.count(KnowledgeChunk.id)).where(
                    KnowledgeChunk.kb_id == kb_id, KnowledgeChunk.content.is_not(None)
                )
            )
            counts[str(kb_id)] = int(n.scalar_one())

    total_chunks = sum(counts.values())
    if total_chunks <= 0:
        raise ValueError("所有知识库都没有可用 chunks")
    pairs = []
    for kb_id in (str(k) for k in kb_rows):
        size = max(1, round(args.total * counts[kb_id] / total_chunks))
        pairs.append((kb_id, size))
    return pairs


async def run(args: argparse.Namespace) -> int:
    pg_manager.initialize()
    try:
        pairs = await _parse_kb_pairs(args)
        judge_spec = args.judge_llm or getattr(sys_config, "default_model", None)
        if not judge_spec:
            raise ValueError("无法确定生成 LLM，请用 --judge-llm 指定")

        print(f"生成 LLM: {judge_spec} | embedding: {args.embedding_model}")
        judge_llm = build_judge_llm(judge_spec, max_tokens=args.max_tokens)
        embeddings = build_langchain_embeddings(args.embedding_model)

        Path(args.output).mkdir(parents=True, exist_ok=True)
        for kb_id, size in pairs:
            try:
                rows = await generate_testset_jsonl(
                    kb_id=kb_id,
                    size=size,
                    judge_llm=judge_llm,
                    embedding_model=embeddings,
                    concurrency=args.concurrency,
                    max_chunks=args.max_chunks,
                )
            except Exception as e:
                # 单库失败只跳过该库，不中断其余库（RAGAS 会把单个坏 chunk 的异常
                # 在 transform 全部跑完后统一抛出，整库报废；这里隔离到库粒度）
                print(f"{kb_id}: 生成失败: {e}，跳过该库")
                continue
            if not rows:
                print(f"{kb_id}: 未能生成测试题，跳过")
                continue
            out = str(Path(args.output) / f"{kb_id}.jsonl")
            write_testset_jsonl(rows, out)
            print(f"{kb_id}: 生成 {len(rows)} 题 → {out}")
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
        print(f"生成失败: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
