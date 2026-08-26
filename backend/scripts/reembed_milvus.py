#!/usr/bin/env python3
"""从 postgres 已入库 chunks 重新向量化写回 Milvus（内部工具）。

场景：Milvus 集合为空（重建/降级）但 postgres 分块元数据完整时，恢复检索数据。
读取 knowledge_chunks 文本 → 各库配置的 embedding 模型批量向量化 → 直接
collection.insert 写回（schema + BM25 Function 与官方入库一致；postgres 为事实源，
不走 _insert_chunks_to_stores 避免其失败回滚误删 postgres chunks）。每库先丢弃集合
再重建保证重跑幂等；单文件向量化带超时；插入后 flush + load 立即可查。

用法：
    # 恢复全部知识库（默认）
    docker exec api-dev python /app/scripts/reembed_milvus.py

    # 只恢复指定库
    docker exec api-dev python /app/scripts/reembed_milvus.py --kb kb_3cm2gz6tyb

依赖：无需 ragas，仅 pymilvus + 系统模型链路。
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "package"))

from pymilvus import utility

from yuxi.knowledge.runtime import knowledge_base
from yuxi.repositories.knowledge_base_repository import KnowledgeBaseRepository
from yuxi.repositories.knowledge_chunk_repository import KnowledgeChunkRepository
from yuxi.storage.postgres.manager import pg_manager
from yuxi.utils import logger


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="从 postgres chunks 重新向量化写回 Milvus")
    parser.add_argument(
        "--kb", action="append", default=None, metavar="KB_ID", help="只恢复指定知识库（可重复）；默认全部"
    )
    parser.add_argument(
        "--embedding-model",
        help="覆盖各库持久化的 embedding 模型 spec（默认取各库 databases_meta.embedding_model_spec）",
    )
    parser.add_argument(
        "--file-timeout",
        type=float,
        default=300.0,
        help="单文件向量化超时（秒），默认 300；超时则该文件跳过、继续下一文件",
    )
    return parser.parse_args()


async def _build_chunk_dicts(kb_id: str, kb_instance) -> tuple[str, list[dict]]:
    """加载某库全部 postgres chunks，组装为官方入库路径所需的 chunk dict。

    返回 (embedding_model_spec, chunk_dicts)；每个 dict 携带 pg 侧全部元数据字段，
    使 batch_upsert 写回时保持图索引/实体/标签等既有值不变。
    """
    metadata = kb_instance.databases_meta.get(kb_id) or {}
    model_spec = metadata.get("embedding_model_spec")
    if not model_spec:
        raise ValueError(f"知识库 {kb_id} 缺少 embedding_model_spec，跳过")

    chunks = await KnowledgeChunkRepository().list_by_kb_id(kb_id)
    chunk_dicts = [
        {
            "id": chunk.chunk_id,
            "chunk_id": chunk.chunk_id,
            "file_id": chunk.file_id,
            "chunk_index": chunk.chunk_index,
            "content": chunk.content or "",
            "start_char_pos": chunk.start_char_pos,
            "end_char_pos": chunk.end_char_pos,
            "start_token_pos": chunk.start_token_pos,
            "end_token_pos": chunk.end_token_pos,
            "graph_indexed": bool(chunk.graph_indexed),
            "ent_ids": chunk.ent_ids or [],
            "tags": chunk.tags or [],
            "extraction_result": chunk.extraction_result,
        }
        for chunk in chunks
        if chunk.content
    ]
    return model_spec, chunk_dicts


async def reembed_kb(kb_id: str, embedding_override: str | None, file_timeout: float) -> int:
    kb_instance = await knowledge_base.aget_kb(kb_id)
    if kb_instance is None:
        print(f"知识库不存在: {kb_id}")
        return 0
    # 独立进程未走 API 启动初始化，需手动加载 KB 元数据（databases_meta）
    await kb_instance._load_metadata()

    model_spec, chunk_dicts = await _build_chunk_dicts(kb_id, kb_instance)
    if embedding_override:
        model_spec = embedding_override
    if not chunk_dicts:
        print(f"{kb_id}: 没有可用的 postgres chunks，跳过")
        return 0

    # 先丢弃该库集合再重建，保证重跑幂等（postgres 为事实源）。
    # 不用 collection.delete(expr=...) 清空：Milvus delete 是逻辑 tombstone，
    # get_collection_stats 仍计入旧行，宽表达式还可能匹配 0 行，旧数据残留。
    if await asyncio.to_thread(utility.has_collection, kb_id, using=kb_instance.connection_alias):
        await asyncio.to_thread(utility.drop_collection, kb_id, using=kb_instance.connection_alias)
    collection = await kb_instance._create_kb_instance(kb_id, {})
    if collection is None:
        raise RuntimeError(f"创建/获取集合失败: {kb_id}")
    embedding_fn = kb_instance._get_embedding_function(model_spec)

    # 按文件分组、逐文件向量化后直接插入 Milvus。
    # 不走 _insert_chunks_to_stores：postgres 元数据已正确，避免其失败回滚误删 postgres chunks。
    by_file: dict[str, list[dict]] = {}
    for chunk in chunk_dicts:
        by_file.setdefault(chunk["file_id"], []).append(chunk)

    total = 0
    for file_id, file_chunks in by_file.items():
        texts = [c["content"] for c in file_chunks]
        try:
            embeddings = await asyncio.wait_for(embedding_fn(texts), timeout=file_timeout)
        except Exception as e:
            print(f"  {file_id}: 向量化失败，跳过该文件: {e}")
            continue
        await asyncio.to_thread(
            collection.insert,
            [
                [c["id"] for c in file_chunks],
                [c["content"] for c in file_chunks],
                [c["chunk_id"] for c in file_chunks],
                [c["file_id"] for c in file_chunks],
                [c["chunk_index"] for c in file_chunks],
                embeddings,
            ],
        )
        total += len(file_chunks)
        print(f"  {file_id}: {len(file_chunks)} chunks 已入库（累计 {total}）", flush=True)

    # 新集合插入后需 flush 落盘 + load 加载，否则 get_collection_stats 报 0、查询报 "collection not loaded"
    await asyncio.to_thread(collection.flush)
    await asyncio.to_thread(collection.load)

    print(f"{kb_id}: 共 {total} chunks 已重新向量化（embedding: {model_spec}）")
    return total


async def run(args: argparse.Namespace) -> int:
    pg_manager.initialize()
    try:
        kb_repo = KnowledgeBaseRepository()
        all_kbs = [row.kb_id for row in await kb_repo.list_all()] if hasattr(kb_repo, "list_all") else []
        if not all_kbs:
            # 兼容：repository 无 list_all 时直接从表枚举
            from sqlalchemy import select

            from yuxi.storage.postgres.models_knowledge import KnowledgeBase

            async with pg_manager.get_async_session_context() as session:
                rows = (await session.execute(select(KnowledgeBase.kb_id))).scalars().all()
            all_kbs = [str(kb_id) for kb_id in rows]

        targets = args.kb or all_kbs
        if not targets:
            print("没有可恢复的知识库")
            return 1

        grand_total = 0
        for kb_id in targets:
            try:
                grand_total += await reembed_kb(kb_id, args.embedding_model, args.file_timeout)
            except Exception as e:
                print(f"{kb_id}: 恢复失败: {e}")
                logger.exception(f"reembed failed for {kb_id}")
        print(f"\n完成：{len(targets)} 个库，共 {grand_total} chunks 已向量化")
        return 0
    finally:
        await pg_manager.close()


def main() -> int:
    args = parse_args()
    try:
        return asyncio.run(run(args))
    except Exception as e:
        print(f"恢复失败: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
