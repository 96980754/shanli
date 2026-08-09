#!/usr/bin/env python3
"""RAGAS 内部评估 CLI（不交付甲方）。

复用现有评估链路对一批问题计算 RAGAS 指标，输出 JSON + Markdown 报告。

用法：
    # 复用现有评估数据集（推荐）
    docker exec api-dev python /app/scripts/ragas_eval.py --dataset-id dataset_xxxx

    # 独立 JSONL 文件（需指定知识库）
    docker exec api-dev python /app/scripts/ragas_eval.py --file cases.jsonl --kb-id kb_xxxx

    # 开启需要 embedding 的指标
    docker exec api-dev python /app/scripts/ragas_eval.py --dataset-id dataset_xxxx \
        --with-embedding-metrics --embedding-model siliconflow-cn:BAAI/bge-m3

依赖：需先安装 eval 组（ragas）：`uv sync --group eval`。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "package"))

from yuxi import config as sys_config
from yuxi.knowledge.eval.ragas_eval import (
    build_embedding_adapter,
    build_judge_llm,
    run_ragas_evaluation,
    write_reports,
)
from yuxi.knowledge.runtime import knowledge_base
from yuxi.repositories.evaluation_repository import EvaluationRepository
from yuxi.repositories.knowledge_base_repository import KnowledgeBaseRepository
from yuxi.storage.postgres.manager import pg_manager
from yuxi.utils import logger


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="RAGAS 内部评估")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--dataset-id", help="现有评估数据集 ID（Postgres，推荐）")
    source.add_argument("--file", help="独立 JSONL 文件路径（每行 {query, gold_chunk_ids?, gold_answer?}）")
    parser.add_argument("--kb-id", help="知识库 ID（--file 模式必填）")
    parser.add_argument("--judge-llm", help="judge 模型 spec（默认取知识库 answer_llm，回退系统默认模型）")
    parser.add_argument("--embedding-model", help="embedding 模型 spec（--with-embedding-metrics 时必填）")
    parser.add_argument(
        "--with-embedding-metrics", action="store_true", help="开启 answer_relevancy / answer_correctness"
    )
    parser.add_argument("--output", default=".", help="报告输出目录（默认当前目录）")
    parser.add_argument("--max-questions", type=int, help="最多评估前 N 道题")
    return parser.parse_args()


def parse_questions_jsonl(file_content: str) -> list[dict]:
    questions = []
    for line_num, line in enumerate(file_content.strip().split("\n"), 1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as e:
            raise ValueError(f"第{line_num}行 JSON 格式错误: {e}")
        if "query" not in item:
            raise ValueError(f"第{line_num}行缺少必需的 query 字段")
        questions.append(item)
    if not questions:
        raise ValueError("文件中没有有效的问题数据")
    return questions


async def load_dataset(dataset_id: str) -> tuple[str, list[dict]]:
    eval_repo = EvaluationRepository()
    row = await eval_repo.get_dataset(dataset_id)
    if row is None:
        raise ValueError(f"评估数据集不存在: {dataset_id}")
    if (row.build_metadata or {}).get("status", "completed") != "completed":
        raise ValueError(f"评估数据集未就绪: {dataset_id}")
    items = await eval_repo.list_all_dataset_items(dataset_id)
    questions = [
        {
            "query": item.query_text,
            "gold_chunk_ids": item.gold_chunk_ids or [],
            "gold_answer": item.gold_answer,
        }
        for item in items
    ]
    return row.kb_id, questions


async def resolve_retrieval_config(kb_id: str, kb_instance) -> dict:
    """优先取知识库持久化检索配置，缺失时回退默认配置。"""
    try:
        kb_repo = KnowledgeBaseRepository()
        kb_row = await kb_repo.get_by_kb_id(kb_id)
        query_params = (kb_row.query_params if kb_row else None) or {}
        retrieval_config = query_params.get("options", {}) if isinstance(query_params, dict) else {}
        if not retrieval_config and kb_instance is not None:
            retrieval_config = kb_instance._get_default_query_params(kb_id).get("options", {})
        return retrieval_config or {}
    except Exception as e:
        logger.error(f"获取知识库检索配置失败: {e}")
        return {}


def resolve_judge_spec(retrieval_config: dict, args: argparse.Namespace) -> str:
    if args.judge_llm:
        return args.judge_llm
    for key in ("answer_llm", "judge_llm"):
        if retrieval_config.get(key):
            return retrieval_config[key]
    default = getattr(sys_config, "default_model", None)
    if default:
        return default
    raise ValueError("无法确定 judge 模型，请用 --judge-llm 指定")


async def run(args: argparse.Namespace) -> int:
    pg_manager.initialize()
    try:
        if args.dataset_id:
            kb_id, questions = await load_dataset(args.dataset_id)
            run_name = f"{kb_id}_{args.dataset_id}"
        else:
            if not args.kb_id:
                raise ValueError("--file 模式需要指定 --kb-id")
            kb_id = args.kb_id
            questions = parse_questions_jsonl(Path(args.file).read_text(encoding="utf-8"))
            run_name = f"{kb_id}_{Path(args.file).stem}"
        if args.max_questions:
            questions = questions[: args.max_questions]
        if not questions:
            print("没有可评估的问题")
            return 1

        kb_instance = await knowledge_base.aget_kb(kb_id)
        if kb_instance is None:
            print(f"知识库不存在: {kb_id}")
            return 1
        # 独立进程未走 API 启动初始化，需手动加载 KB 元数据（databases_meta）
        await kb_instance._load_metadata()

        retrieval_config = await resolve_retrieval_config(kb_id, kb_instance)
        judge_spec = resolve_judge_spec(retrieval_config, args)
        # 简化评估链路生成答案需要 answer_llm，缺省时复用 judge 模型
        retrieval_config.setdefault("answer_llm", judge_spec)
        print(f"judge 模型: {judge_spec} | 题目数: {len(questions)} | embedding 指标: {args.with_embedding_metrics}")

        judge_llm = build_judge_llm(judge_spec)
        embedding_adapter = build_embedding_adapter(args.embedding_model) if args.with_embedding_metrics else None

        results = await run_ragas_evaluation(
            kb_instance=kb_instance,
            kb_id=kb_id,
            questions=questions,
            retrieval_config=retrieval_config,
            judge_llm=judge_llm,
            embedding_adapter=embedding_adapter,
            with_embedding_metrics=args.with_embedding_metrics,
        )

        Path(args.output).mkdir(parents=True, exist_ok=True)
        json_path, md_path = write_reports(results, run_name=run_name, output_dir=args.output)
        print(f"聚合指标: {results['metrics']}")
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
        print(f"评估失败: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
