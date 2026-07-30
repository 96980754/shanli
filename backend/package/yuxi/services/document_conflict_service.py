from __future__ import annotations

import hashlib
import re
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.knowledge.graphs.ontology.registry import OntologySpec, load_ontology
from yuxi.storage.postgres.models_knowledge import KnowledgeConflict
from yuxi.utils.datetime_utils import utc_now_naive

_KEY_VALUE_RE = re.compile(r"^\s*([^:：=]+?)\s*[:：=]\s*(.+?)\s*$")


def analyze_document_conflicts(
    old_chunks: list[dict[str, Any]],
    new_chunks: list[dict[str, Any]],
    ontology: OntologySpec,
) -> dict[str, Any]:
    rules = ontology.rules.get("conflict_detection") or {}
    single_valued_relations = {
        str(item).strip() for item in rules.get("single_valued_relations", []) if str(item).strip()
    }
    keyed_value_relations = {str(item).strip() for item in rules.get("keyed_value_relations", []) if str(item).strip()}
    old_facts = _collect_facts(old_chunks, single_valued_relations, keyed_value_relations)
    new_facts = _collect_facts(new_chunks, single_valued_relations, keyed_value_relations)
    comparable_keys = sorted(set(old_facts) & set(new_facts))

    if not old_chunks or not new_chunks:
        return _inconclusive_analysis(old_facts, new_facts, comparable_keys, "新旧版本缺少可用于比较的文档分块")
    if not old_facts or not new_facts:
        return _inconclusive_analysis(old_facts, new_facts, comparable_keys, "新旧版本未抽取到 Ontology 允许的冲突事实")
    if not comparable_keys:
        return _inconclusive_analysis(old_facts, new_facts, comparable_keys, "新旧版本没有共同的结构化事实 key")

    conflicts: list[dict[str, Any]] = []
    for conflict_key in comparable_keys:
        old_fact = old_facts[conflict_key]
        new_fact = new_facts[conflict_key]
        if old_fact["normalized_value"] == new_fact["normalized_value"]:
            continue
        conflicts.append(
            {
                "conflict_type": old_fact["conflict_type"],
                "conflict_key": conflict_key,
                "old_fact": old_fact,
                "new_fact": new_fact,
            }
        )
    return {
        "status": "conflict" if conflicts else "clear",
        "conflicts": conflicts,
        "old_fact_count": len(old_facts),
        "new_fact_count": len(new_facts),
        "comparable_fact_count": len(comparable_keys),
    }


def detect_document_conflicts(
    old_chunks: list[dict[str, Any]],
    new_chunks: list[dict[str, Any]],
    ontology: OntologySpec,
) -> list[dict[str, Any]]:
    return analyze_document_conflicts(old_chunks, new_chunks, ontology)["conflicts"]


def _inconclusive_analysis(
    old_facts: dict[str, dict[str, Any]],
    new_facts: dict[str, dict[str, Any]],
    comparable_keys: list[str],
    message: str,
) -> dict[str, Any]:
    return {
        "status": "inconclusive",
        "conflicts": [],
        "old_fact_count": len(old_facts),
        "new_fact_count": len(new_facts),
        "comparable_fact_count": len(comparable_keys),
        "message": message,
    }


def _collect_facts(
    chunks: list[dict[str, Any]],
    single_valued_relations: set[str],
    keyed_value_relations: set[str],
) -> dict[str, dict[str, Any]]:
    facts: dict[str, dict[str, Any]] = {}
    for chunk in chunks:
        extraction_result = chunk.get("extraction_result") or {}
        for relation in extraction_result.get("relations") or []:
            source = relation.get("source") or {}
            target = relation.get("target") or {}
            relation_type = str(relation.get("label") or "").strip()
            source_key = _entity_key(source)
            if not source_key or not relation_type:
                continue

            conflict_type = "single_value_changed"
            fact_key = f"{source_key}|{relation_type}"
            normalized_value = _entity_key(target)
            if relation_type in keyed_value_relations:
                parsed = _parse_keyed_value(str(target.get("text") or ""))
                if parsed is None:
                    continue
                key, value = parsed
                conflict_type = "keyed_value_changed"
                fact_key = f"{source_key}|{relation_type}|{key}"
                normalized_value = value
            elif relation_type not in single_valued_relations:
                continue

            if not normalized_value or fact_key in facts:
                continue
            facts[fact_key] = {
                "conflict_type": conflict_type,
                "subject": source,
                "relation": relation_type,
                "target": target,
                "normalized_value": normalized_value,
                "file_id": chunk.get("file_id"),
                "chunk_id": chunk.get("chunk_id"),
                "chunk_index": chunk.get("chunk_index"),
                "quote": chunk.get("content"),
            }
    return facts


def _entity_key(entity: dict[str, Any]) -> str:
    text = str(entity.get("text") or "").strip().casefold()
    label = str(entity.get("label") or "Entity").strip().casefold()
    return f"{label}:{text}" if text else ""


def _parse_keyed_value(value: str) -> tuple[str, str] | None:
    match = _KEY_VALUE_RE.fullmatch(value)
    if match is None:
        return None
    key = match.group(1).strip().casefold()
    normalized_value = match.group(2).strip().casefold()
    if not key or not normalized_value:
        return None
    return key, normalized_value


class KnowledgeConflictRepository:
    async def replace_for_candidate(
        self,
        *,
        kb_id: str,
        logical_document_id: str,
        old_file_id: str,
        new_file_id: str,
        conflicts: list[dict[str, Any]],
        session: AsyncSession,
    ) -> list[KnowledgeConflict]:
        await session.execute(KnowledgeConflict.__table__.delete().where(KnowledgeConflict.new_file_id == new_file_id))
        records = []
        for conflict in conflicts:
            digest = hashlib.sha256(
                f"{new_file_id}|{conflict['conflict_type']}|{conflict['conflict_key']}".encode()
            ).hexdigest()[:32]
            record = KnowledgeConflict(
                conflict_id=f"conflict_{digest}",
                kb_id=kb_id,
                logical_document_id=logical_document_id,
                old_file_id=old_file_id,
                new_file_id=new_file_id,
                conflict_type=conflict["conflict_type"],
                conflict_key=conflict["conflict_key"],
                old_fact=conflict["old_fact"],
                new_fact=conflict["new_fact"],
                status="open",
            )
            session.add(record)
            records.append(record)
        await session.flush()
        return records

    async def list_by_candidate(self, *, kb_id: str, new_file_id: str) -> list[KnowledgeConflict]:
        from yuxi.storage.postgres.manager import pg_manager

        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(KnowledgeConflict)
                .where(KnowledgeConflict.kb_id == kb_id, KnowledgeConflict.new_file_id == new_file_id)
                .order_by(KnowledgeConflict.id.asc())
            )
            return list(result.scalars().all())

    async def accept_candidate(self, *, new_file_id: str, operator_id: str, session: AsyncSession) -> None:
        await session.execute(
            update(KnowledgeConflict)
            .where(KnowledgeConflict.new_file_id == new_file_id, KnowledgeConflict.status == "open")
            .values(status="accepted", resolved_by=operator_id, resolved_at=utc_now_naive())
        )


def load_conflict_ontology(extractor_options: dict[str, Any]) -> OntologySpec:
    registry_id = str(extractor_options.get("ontology_registry_id") or "").strip()
    if not registry_id:
        raise ValueError("知识库图谱未配置 Core Ontology")
    return load_ontology(
        registry_id,
        str(extractor_options.get("ontology_version") or "").strip() or None,
        str(extractor_options.get("ontology_digest") or "").strip() or None,
    )
