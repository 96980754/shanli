#!/usr/bin/env python3
"""忠实度（答案正确率）汇报报告 CLI（内部，不交付甲方）。

对多知识库测试集复用现有 RAGAS 评估链路（run_ragas_evaluation），合并后生成管理向
「答案正确率（忠实度）」汇报报告（Markdown + JSON）。主指标 = RAGAS Faithfulness，
内部双指标 = Faithfulness + Answer Relevancy。

用法：
    # 默认 POC/MCX/LOC 三库 100 题（容器内）
    docker exec api-dev python /app/scripts/report_faithfulness.py

    # 自定义知识库与测试集（--kb 可重复）
    docker exec api-dev python /app/scripts/report_faithfulness.py \
        --kb kb_3cm2gz6tyb:/app/scripts/eval_datasets/poc.jsonl \
        --kb kb_mvng8u1201:/app/scripts/eval_datasets/mcx.jsonl \
        --kb kb_0368jjmecb:/app/scripts/eval_datasets/loc.jsonl

    # 指定 judge 模型 / 调整内部目标
    docker exec api-dev python /app/scripts/report_faithfulness.py \
        --judge-llm siliconflow-cn:Pro/MiniMaxAI/MiniMax-M2.5 --threshold 0.75

注意：judge 模型默认取系统默认模型，并强制覆盖各知识库 answer_llm，
避免命中未配置凭据的模型导致 401。可用 --judge-llm 覆盖。

依赖：需先安装 eval 组（ragas）：`uv sync --group eval`。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "package"))

from yuxi import config as sys_config
from yuxi.knowledge.base import KBNotFoundError
from yuxi.knowledge.eval.faithfulness_report import combine_results, write_faithfulness_reports
from yuxi.knowledge.eval.ragas_eval import build_embedding_adapter, build_judge_llm, run_ragas_evaluation
from yuxi.knowledge.runtime import knowledge_base
from yuxi.repositories.knowledge_base_repository import KnowledgeBaseRepository
from yuxi.storage.postgres.manager import pg_manager
from yuxi.utils import logger

DEFAULT_KB_PAIRS = [
    ("kb_3cm2gz6tyb", "/app/scripts/eval_datasets/poc.jsonl"),
    ("kb_mvng8u1201", "/app/scripts/eval_datasets/mcx.jsonl"),
    ("kb_0368jjmecb", "/app/scripts/eval_datasets/loc.jsonl"),
]
DEFAULT_EMBEDDING_MODEL = "siliconflow-cn:Pro/BAAI/bge-m3"
DEFAULT_OUTPUT = "/app/scripts/eval_datasets/reports"
DEFAULT_CACHE_DIR = "/app/saves/ragas_cache"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="忠实度（答案正确率）汇报报告")
    parser.add_argument(
        "--kb",
        action="append",
        default=None,
        metavar="KB_ID:path/to.jsonl",
        help="知识库与测试集配对（可重复）；默认 POC/MCX/LOC 三组 100 题",
    )
    parser.add_argument("--judge-llm", help="judge 模型 spec（默认系统默认模型）")
    parser.add_argument(
        "--embedding-model", default=DEFAULT_EMBEDDING_MODEL, help="embedding 模型 spec（计算答案相关性）"
    )
    parser.add_argument("--no-embedding-metrics", action="store_true", help="关闭答案相关性（默认开启）")
    parser.add_argument("--threshold", type=float, default=0.70, help="答案正确率（忠实度）内部目标")
    parser.add_argument("--concurrency", type=int, default=4, help="同时评估的题目数")
    parser.add_argument("--cache-dir", default=DEFAULT_CACHE_DIR, help="ragas 磁盘缓存目录（空串关闭）")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="报告输出目录")
    parser.add_argument("--name", default="", help="报告标题/文件名（默认当日日期）")
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


def parse_kb_pairs(args: argparse.Namespace) -> list[tuple[str, str]]:
    raw = args.kb or [f"{kb_id}:{path}" for kb_id, path in DEFAULT_KB_PAIRS]
    pairs = []
    for item in raw:
        if ":" not in item:
            raise ValueError(f"--kb 格式应为 KB_ID:path，收到: {item}")
        kb_id, path = item.split(":", 1)
        if not kb_id or not path:
            raise ValueError(f"--kb 格式应为 KB_ID:path，收到: {item}")
        pairs.append((kb_id, path))
    return pairs


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


def _build_judge_cache(cache_dir: str | None) -> Any:
    """构建 ragas judge LLM 磁盘缓存；cache_dir 为空时返回 None（关闭缓存）。"""
    if not cache_dir:
        return None
    from ragas.cache import DiskCacheBackend

    return DiskCacheBackend(cache_dir=cache_dir)


async def run(args: argparse.Namespace) -> int:
    pg_manager.initialize()
    try:
        pairs = parse_kb_pairs(args)

        # 逐个加载知识库并解析持久化检索配置；知识库不存在时显式失败退出
        kb_instances, retrieval_configs = {}, {}
        for kb_id, _path in pairs:
            try:
                kb_instance = await knowledge_base.aget_kb(kb_id)
            except KBNotFoundError:
                print(f"知识库不存在: {kb_id}")
                return 1
            # 独立进程未走 API 启动初始化，需手动加载 KB 元数据（databases_meta）
            await kb_instance._load_metadata()
            kb_instances[kb_id] = kb_instance
            retrieval_configs[kb_id] = await resolve_retrieval_config(kb_id, kb_instance)

        # judge 默认系统默认模型（deepseek，本环境可用），强制覆盖各库 answer_llm
        judge_spec = args.judge_llm or getattr(sys_config, "default_model", None)
        if not judge_spec:
            raise ValueError("无法确定 judge 模型，请用 --judge-llm 指定")
        for cfg in retrieval_configs.values():
            cfg["answer_llm"] = judge_spec

        with_embedding = not args.no_embedding_metrics
        judge_cache = _build_judge_cache(args.cache_dir)
        judge_llm = build_judge_llm(judge_spec, cache=judge_cache)
        embedding_adapter = build_embedding_adapter(args.embedding_model) if with_embedding else None
        print(f"judge 模型: {judge_spec} | embedding 指标: {with_embedding} | {args.embedding_model}")
        if judge_cache:
            print(f"已启用 ragas 磁盘缓存: {args.cache_dir}")

        segments = []
        for kb_id, path in pairs:
            questions = parse_questions_jsonl(Path(path).read_text(encoding="utf-8"))
            results = await run_ragas_evaluation(
                kb_instance=kb_instances[kb_id],
                kb_id=kb_id,
                questions=questions,
                retrieval_config=retrieval_configs[kb_id],
                judge_llm=judge_llm,
                embedding_adapter=embedding_adapter,
                with_embedding_metrics=with_embedding,
                concurrency=args.concurrency,
            )
            segments.append({"kb_id": kb_id, "dataset": Path(path).stem, "results": results})
            print(f"完成 {kb_id}（{Path(path).stem}）: {len(questions)} 题")

        combined = combine_results(segments)
        if combined["total_items"] == 0:
            print("没有任何评估结果")
            return 1

        run_name = args.name or date.today().strftime("%Y%m%d")
        Path(args.output).mkdir(parents=True, exist_ok=True)
        json_path, md_path = write_faithfulness_reports(
            combined, run_name=run_name, output_dir=args.output, threshold=args.threshold
        )
        print(f"忠实度（答案正确率）: {combined['metrics'].get('faithfulness')}")
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
