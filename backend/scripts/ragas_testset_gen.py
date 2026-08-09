#!/usr/bin/env python3
"""RAGAS 测试集生成 CLI（不交付甲方）。

从知识库已入库 chunks 用 RAGAS 自动生成 QA 测试集，输出 JSONL，
可直接作为 ragas_eval.py --file 的输入。

用法：
    # 从知识库生成 20 道题
    docker exec api-dev python /app/scripts/ragas_testset_gen.py --kb-id kb_xxxx --size 20

    # 指定 judge LLM 与 embedding 模型
    docker exec api-dev python /app/scripts/ragas_testset_gen.py --kb-id kb_xxxx \
        --size 20 --llm deepseek:deepseek-v4-flash \
        --embedding-model "siliconflow-cn:Pro/BAAI/bge-m3"

    # 生成后直接评估
    docker exec api-dev python /app/scripts/ragas_eval.py --file testset.jsonl --kb-id kb_xxxx

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
from yuxi.knowledge.runtime import knowledge_base
from yuxi.repositories.knowledge_base_repository import KnowledgeBaseRepository
from yuxi.storage.postgres.manager import pg_manager
from yuxi.utils import logger


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="RAGAS 测试集生成")
    parser.add_argument("--kb-id", required=True, help="知识库 ID")
    parser.add_argument("--size", type=int, default=20, help="生成题目数（默认 20）")
    parser.add_argument("--llm", help="生成 LLM spec（默认系统 default_model）")
    parser.add_argument("--embedding-model", help="embedding 模型 spec（默认系统默认 embedding）")
    parser.add_argument("--output", default="testset.jsonl", help="输出 JSONL 路径")
    return parser.parse_args()


async def resolve_llm_spec(args: argparse.Namespace) -> str:
    if args.llm:
        return args.llm
    default = getattr(sys_config, "default_model", None)
    if default:
        return default
    raise ValueError("无法确定生成 LLM，请用 --llm 指定")


async def resolve_embedding_spec(kb_id: str, args: argparse.Namespace) -> str:
    if args.embedding_model:
        return args.embedding_model
    try:
        kb_repo = KnowledgeBaseRepository()
        kb_row = await kb_repo.get_by_kb_id(kb_id)
        query_params = (kb_row.query_params if kb_row else None) or {}
        embedding = (
            (query_params.get("options") or {}).get("embedding_model") if isinstance(query_params, dict) else None
        )
        if embedding:
            return embedding
    except Exception as e:
        logger.warning(f"获取知识库 embedding 配置失败: {e}")
    raise ValueError("无法确定 embedding 模型，请用 --embedding-model 指定")


async def run(args: argparse.Namespace) -> int:
    pg_manager.initialize()
    try:
        kb_instance = await knowledge_base.aget_kb(kb_id=args.kb_id)
        if kb_instance is None:
            print(f"知识库不存在: {args.kb_id}")
            return 1
        await kb_instance._load_metadata()

        llm_spec = await resolve_llm_spec(args)
        embedding_spec = await resolve_embedding_spec(args.kb_id, args)
        print(f"生成 LLM: {llm_spec} | embedding: {embedding_spec} | 目标题数: {args.size}")

        judge_llm = build_judge_llm(llm_spec)
        embedding_model = build_langchain_embeddings(embedding_spec)

        rows = await generate_testset_jsonl(
            kb_id=args.kb_id,
            size=args.size,
            judge_llm=judge_llm,
            embedding_model=embedding_model,
        )
        output = write_testset_jsonl(rows, args.output)
        print(f"已生成 {len(rows)} 道测试题 -> {output}")
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
        print(f"测试集生成失败: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
