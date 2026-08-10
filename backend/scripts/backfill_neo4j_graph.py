#!/usr/bin/env python3
"""从 PG 回填 Neo4j 图谱数据（内部运维工具，不交付甲方）。

背景：8/8 图谱构建时 PG / Milvus 数据写入成功、chunk 被标记 graph_indexed，
但 Neo4j 写入未生效，导致图谱页面查不到实体。本脚本读取 PG 中已有的
entities / triples / mentions，复用生产环境的 Cypher 模板写回 Neo4j，
不重新调用 LLM 抽取。

用法：
    # 回填单个知识库
    docker exec worker-dev python /app/scripts/backfill_neo4j_graph.py --kb-id kb_0368jjmecb

    # 先看统计，不写入
    docker exec worker-dev python /app/scripts/backfill_neo4j_graph.py --kb-id kb_0368jjmecb --dry-run

    # 多个知识库
    docker exec worker-dev python /app/scripts/backfill_neo4j_graph.py --kb-id kb_0368jjmecb,kb_3cm2gz6tyb
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "package"))

from sqlalchemy import select

from yuxi.knowledge.graphs.graph_utils import cypher_merge_chunk, cypher_merge_entity_mention, cypher_merge_relation
from yuxi.storage.neo4j import get_shared_neo4j_connection, neo4j_write, safe_neo4j_label
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_knowledge import (
    KnowledgeChunk,
    KnowledgeGraphEntity,
    KnowledgeGraphEntityMention,
    KnowledgeGraphTriple,
    KnowledgeGraphTripleMention,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="从 PG 回填 Neo4j 图谱数据")
    parser.add_argument("--kb-id", required=True, help="知识库 ID，多个用逗号分隔")
    parser.add_argument("--dry-run", action="store_true", help="只打印统计，不写入 Neo4j")
    return parser.parse_args()


async def load_kb_graph(kb_id: str) -> dict:
    """读取某知识库的全部图谱数据（chunks / entities / mentions / triples）。"""
    async with pg_manager.get_async_session_context() as session:
        chunks = (await session.scalars(select(KnowledgeChunk).where(KnowledgeChunk.kb_id == kb_id))).all()
        entities = (await session.scalars(select(KnowledgeGraphEntity).where(KnowledgeGraphEntity.kb_id == kb_id))).all()
        entity_mentions = (
            await session.scalars(
                select(KnowledgeGraphEntityMention).where(KnowledgeGraphEntityMention.kb_id == kb_id)
            )
        ).all()
        triples = (await session.scalars(select(KnowledgeGraphTriple).where(KnowledgeGraphTriple.kb_id == kb_id))).all()
        triple_mentions = (
            await session.scalars(
                select(KnowledgeGraphTripleMention).where(KnowledgeGraphTripleMention.kb_id == kb_id)
            )
        ).all()

    entity_by_id = {entity.entity_id: entity for entity in entities}
    triple_by_id = {triple.triple_id: triple for triple in triples}

    # chunk → 该 chunk 引用的实体（MENTIONS）
    mentions_by_chunk: dict[str, list[KnowledgeGraphEntity]] = defaultdict(list)
    for mention in entity_mentions:
        entity = entity_by_id.get(mention.entity_id)
        if entity is not None:
            mentions_by_chunk[mention.chunk_id].append(entity)

    # chunk → 该 chunk 的三元组（RELATION）
    triples_by_chunk: dict[str, list[KnowledgeGraphTriple]] = defaultdict(list)
    triple_mention_text: dict[str, str] = {}
    triple_mention_extractor: dict[str, str] = {}
    for mention in triple_mentions:
        triple = triple_by_id.get(mention.triple_id)
        if triple is not None:
            triples_by_chunk[mention.chunk_id].append(triple)
            triple_mention_text[mention.triple_id] = mention.text or ""
            triple_mention_extractor[mention.triple_id] = mention.extractor_type or "unknown"

    return {
        "chunks": chunks,
        "entities": entities,
        "entity_mentions": entity_mentions,
        "triples": triples,
        "triple_mentions": triple_mentions,
        "entity_by_id": entity_by_id,
        "mentions_by_chunk": mentions_by_chunk,
        "triples_by_chunk": triples_by_chunk,
        "triple_mention_text": triple_mention_text,
        "triple_mention_extractor": triple_mention_extractor,
    }


def build_chunk_write(kb_id: str, chunk, data: dict) -> None:
    """构造单个 chunk 的 Neo4j 写入事务（对齐 write_chunk_graph 的写入格式）。"""
    label = safe_neo4j_label(kb_id)
    entity_by_id = data["entity_by_id"]
    triple_mention_text = data["triple_mention_text"]
    triple_mention_extractor = data["triple_mention_extractor"]
    content_preview = (chunk.content or "")[:300]

    merge_chunk_cypher = cypher_merge_chunk(label)
    merge_entity_cypher = cypher_merge_entity_mention(label)
    merge_relation_cypher = cypher_merge_relation(label)

    def query(tx):
        # 1. MERGE Chunk 节点
        tx.run(
            merge_chunk_cypher,
            chunk_id=chunk.chunk_id,
            file_id=chunk.file_id,
            kb_id=kb_id,
            chunk_index=chunk.chunk_index,
            content_preview=content_preview,
            start_char_pos=chunk.start_char_pos,
            end_char_pos=chunk.end_char_pos,
        )

        # 2. MERGE Entity 节点 + Chunk→Entity (MENTIONS)
        for entity in data["mentions_by_chunk"].get(chunk.chunk_id, []):
            tx.run(
                merge_entity_cypher,
                chunk_id=chunk.chunk_id,
                file_id=chunk.file_id,
                kb_id=kb_id,
                entity_id=entity.entity_id,
                normalized_name=entity.normalized_name,
                entity_label=entity.label or "Entity",
                name=entity.name,
                attributes=json.dumps(entity.attributes or [], ensure_ascii=False),
            )

        # 3. MERGE Entity→Entity (RELATION) 边
        for triple in data["triples_by_chunk"].get(chunk.chunk_id, []):
            source = entity_by_id.get(triple.source_entity_id)
            target = entity_by_id.get(triple.target_entity_id)
            if source is None or target is None:
                continue
            tx.run(
                merge_relation_cypher,
                kb_id=kb_id,
                chunk_id=chunk.chunk_id,
                file_id=chunk.file_id,
                source_name=source.normalized_name,
                source_label=source.label or "Entity",
                target_name=target.normalized_name,
                target_label=target.label or "Entity",
                relation_type=triple.relation_type or "RELATED_TO",
                triple_id=triple.triple_id,
                text=triple_mention_text.get(triple.triple_id, triple.content or ""),
                extractor_type=triple_mention_extractor.get(triple.triple_id, "unknown"),
            )

    return query


def dry_run_report(kb_id: str, data: dict) -> dict:
    """生成回填统计，供 --dry-run 或写入前展示。"""
    chunks = data["chunks"]
    entities = data["entities"]
    mentions = data["entity_mentions"]
    triples = data["triples"]
    triple_mentions = data["triple_mentions"]

    chunks_with_entities = sum(1 for c in chunks if data["mentions_by_chunk"].get(c.chunk_id))
    chunks_with_triples = sum(1 for c in chunks if data["triples_by_chunk"].get(c.chunk_id))

    report = {
        "kb_id": kb_id,
        "chunks": len(chunks),
        "entities": len(entities),
        "entity_mentions": len(mentions),
        "triples": len(triples),
        "triple_mentions": len(triple_mentions),
        "chunks_with_entities": chunks_with_entities,
        "chunks_with_triples": chunks_with_triples,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


async def backfill_kb(kb_id: str, *, dry_run: bool) -> dict:
    print(f"\n===== 回填 {kb_id} =====")
    data = await load_kb_graph(kb_id)
    report = dry_run_report(kb_id, data)

    if dry_run:
        return report

    driver = get_shared_neo4j_connection().driver
    label = safe_neo4j_label(kb_id)

    # 写入前清空该知识库在 Neo4j 的旧数据（避免残留与重复）
    def clear(tx):
        tx.run(f"MATCH (n:MilvusKB:`{label}`) DETACH DELETE n")

    neo4j_write(driver, clear)

    written_chunks = 0
    for chunk in data["chunks"]:
        # 只回填有实体引用的 chunk（与 write_chunk_graph 的跳过逻辑对齐，空 chunk 无图谱价值）
        if not data["mentions_by_chunk"].get(chunk.chunk_id):
            continue
        neo4j_write(driver, build_chunk_write(kb_id, chunk, data))
        written_chunks += 1

    report["written_chunks"] = written_chunks
    print(f"已完成 {written_chunks} 个 chunk 的回填（共 {report['chunks']} 个 chunk）")
    return report


async def verify_neo4j(kb_id: str) -> dict:
    """回填后核对 Neo4j 实际写入量。"""
    label = safe_neo4j_label(kb_id)
    driver = get_shared_neo4j_connection().driver
    with driver.session() as session:
        entity_count = session.run(
            f"MATCH (n:Entity:MilvusKB:`{label}`) RETURN count(n) AS cnt"
        ).single()["cnt"]
        chunk_count = session.run(
            f"MATCH (n:Chunk:MilvusKB:`{label}`) RETURN count(n) AS cnt"
        ).single()["cnt"]
        relation_count = session.run(
            f"MATCH (:Entity:MilvusKB:`{label}`)-[r:RELATION]->(:Entity:MilvusKB:`{label}`) "
            "RETURN count(r) AS cnt"
        ).single()["cnt"]
    result = {"kb_id": kb_id, "entities": entity_count, "chunks": chunk_count, "relations": relation_count}
    print("Neo4j 核对:", json.dumps(result, ensure_ascii=False))
    return result


async def run(args: argparse.Namespace) -> int:
    pg_manager.initialize()
    kb_ids = [kb_id.strip() for kb_id in args.kb_id.split(",") if kb_id.strip()]
    try:
        for kb_id in kb_ids:
            await backfill_kb(kb_id, dry_run=args.dry_run)
            if not args.dry_run:
                await verify_neo4j(kb_id)
    finally:
        get_shared_neo4j_connection().close()
    return 0


def main() -> int:
    args = parse_args()
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
