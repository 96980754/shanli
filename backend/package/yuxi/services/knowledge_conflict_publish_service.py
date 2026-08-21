from __future__ import annotations
import asyncio
import json
from typing import Any
from yuxi.config.app import resolve_embedding_model
from yuxi.knowledge.graphs.milvus_graph_vector_store import MilvusGraphVectorStore
from yuxi.knowledge.utils import sanitize_processing_error
from yuxi.repositories.knowledge_base_repository import KnowledgeBaseRepository
from yuxi.repositories.knowledge_publish_repository import (
    PUBLISH_TASK_STATUS_SUCCEEDED,
    KnowledgePublishRepository,
)
from yuxi.services.run_queue_service import get_arq_pool
from yuxi.storage.neo4j import get_shared_neo4j_connection, neo4j_write
from yuxi.utils.datetime_utils import utc_isoformat
from yuxi.utils.logging_config import logger

PUBLISH_JOB_NAME = "process_knowledge_conflict_publish"
PUBLISH_LEASE_SECONDS = 300


class KnowledgeConflictPublishService:
    def __init__(
        self,
        *,
        repository: KnowledgePublishRepository | None = None,
        kb_repository: KnowledgeBaseRepository | None = None,
        vector_store: MilvusGraphVectorStore | None = None,
        neo4j_connection: Any | None = None,
    ):
        self.repository = repository or KnowledgePublishRepository()
        self.kb_repository = kb_repository or KnowledgeBaseRepository()
        self._vector_store = vector_store
        self._neo4j_connection = neo4j_connection

    @property
    def vector_store(self) -> MilvusGraphVectorStore:
        if self._vector_store is None:
            self._vector_store = MilvusGraphVectorStore()
        return self._vector_store

    @property
    def neo4j_connection(self):
        if self._neo4j_connection is None:
            self._neo4j_connection = get_shared_neo4j_connection()
        return self._neo4j_connection

    async def enqueue(self, task_id: str, *, queue=None) -> bool:
        queue = queue or await get_arq_pool()
        # PostgreSQL is the idempotency boundary. Random ARQ job ids allow a failed
        # delivery to be recovered while duplicate deliveries are rejected by claim().
        job = await queue.enqueue_job(PUBLISH_JOB_NAME, task_id)
        return job is not None

    async def recover(self, *, queue=None, limit: int = 100) -> int:
        task_ids = await self.repository.list_recoverable_task_ids(limit=limit)
        enqueued = 0
        for task_id in task_ids:
            try:
                if await self.enqueue(task_id, queue=queue):
                    enqueued += 1
            except Exception as exc:  # noqa: BLE001 - durable PostgreSQL task remains recoverable
                logger.warning(
                    "Failed to enqueue knowledge publish task {}: {}", task_id, sanitize_processing_error(exc)
                )
        return enqueued

    async def process(self, task_id: str) -> str:
        task = await self.repository.claim(task_id, lease_seconds=PUBLISH_LEASE_SECONDS)
        if task is None:
            return "not_claimed"
        try:
            payload = await self.repository.load_authoritative_payload(task_id)
            if payload is None:
                raise LookupError("publish source not found")
            conflict = payload["conflict"]
            assertion = payload["assertion"]
            entity = payload["entity"]
            if conflict.version != task.expected_version:
                await self.repository.mark_stale(task_id)
                await self._remove_stale_projection(task, assertion)
                return "stale"
            if conflict.status != "resolved" or assertion.status not in {"accepted", "published"}:
                raise ValueError("reviewed assertion is not publishable")
            if entity is None:
                raise ValueError("reviewed assertion has no linked entity")
            if task.neo4j_status != PUBLISH_TASK_STATUS_SUCCEEDED:
                await self._publish_neo4j(task, conflict, assertion, entity)
                await self.repository.mark_target_succeeded(task_id, "neo4j")
            if task.vector_status != PUBLISH_TASK_STATUS_SUCCEEDED:
                await self._publish_vector(task, conflict, assertion)
                await self.repository.mark_target_succeeded(task_id, "vector")
            current = await self.repository.mark_succeeded(task_id)
            if not current:
                await self._remove_stale_projection(task, assertion)
                return "stale"
            return PUBLISH_TASK_STATUS_SUCCEEDED
        except Exception as exc:  # noqa: BLE001 - status must be persisted for every adapter failure
            message = sanitize_processing_error(exc)
            status = await self.repository.mark_failed(
                task_id,
                error_code=_publish_error_code(exc),
                message=message,
            )
            logger.warning("Knowledge publish task {} ended as {}: {}", task_id, status, message)
            return status

    async def retry(self, *, kb_id: str, conflict_id: str) -> dict[str, Any] | None:
        task = await self.repository.retry(kb_id=kb_id, conflict_id=conflict_id)
        if task is None:
            return None
        if task.status != PUBLISH_TASK_STATUS_SUCCEEDED:
            try:
                await self.enqueue(task.task_id)
            except Exception as exc:  # noqa: BLE001 - task is durably pending for recovery
                logger.warning(
                    "Failed to enqueue retried knowledge publish task {}: {}",
                    task.task_id,
                    sanitize_processing_error(exc),
                )
        return serialize_publish_task(task)

    async def _publish_neo4j(self, task, conflict, assertion, entity) -> None:
        old_ids = list(conflict.existing_assertion_ids or []) if conflict.resolution == "use_new" else []

        def write(tx):
            if old_ids:
                tx.run(
                    """
                    MATCH (old:ConflictKnowledgeAssertion {kb_id: $kb_id})
                    WHERE old.assertion_id IN $old_assertion_ids
                    SET old.is_active = false, old.publish_status = 'superseded', old.updated_at = $updated_at
                    """,
                    kb_id=task.kb_id,
                    old_assertion_ids=old_ids,
                    updated_at=utc_isoformat(),
                )
            tx.run(
                """
                MERGE (entity:ConflictKnowledgeEntity {kb_id: $kb_id, entity_id: $entity_id})
                SET entity.name = $entity_name,
                    entity.entity_type = $entity_type,
                    entity.updated_at = $updated_at
                MERGE (assertion:ConflictKnowledgeAssertion {kb_id: $kb_id, assertion_id: $assertion_id})
                SET assertion.entity_id = $entity_id,
                    assertion.predicate = $predicate,
                    assertion.raw_value = $raw_value,
                    assertion.normalized_value = $normalized_value,
                    assertion.product_version = $product_version,
                    assertion.resolution_id = $resolution_id,
                    assertion.version = $version,
                    assertion.publish_sequence = $publish_sequence,
                    assertion.file_id = $file_id,
                    assertion.chunk_id = $chunk_id,
                    assertion.is_active = true,
                    assertion.publish_status = 'succeeded',
                    assertion.updated_at = $updated_at
                MERGE (entity)-[fact:HAS_REVIEWED_FACT {
                    kb_id: $kb_id, predicate: $predicate, assertion_id: $assertion_id
                }]->(assertion)
                SET fact.resolution_id = $resolution_id,
                    fact.version = $version,
                    fact.publish_sequence = $publish_sequence,
                    fact.is_active = true,
                    fact.publish_status = 'succeeded',
                    fact.updated_at = $updated_at
                """,
                kb_id=task.kb_id,
                entity_id=entity.entity_id,
                entity_name=entity.name,
                entity_type=entity.label,
                assertion_id=assertion.assertion_id,
                predicate=assertion.predicate,
                raw_value=json.dumps(assertion.raw_value, ensure_ascii=False),
                normalized_value=json.dumps(assertion.normalized_value, ensure_ascii=False),
                product_version=assertion.product_version,
                resolution_id=task.resolution_id,
                version=task.expected_version,
                publish_sequence=task.id,
                file_id=assertion.file_id,
                chunk_id=assertion.chunk_id,
                updated_at=utc_isoformat(),
            )

        await asyncio.to_thread(neo4j_write, self.neo4j_connection.driver, write)

    async def _publish_vector(self, task, conflict, assertion) -> None:
        kb = await self.kb_repository.get_by_kb_id(task.kb_id)
        if kb is None:
            raise ValueError("Knowledge base is unavailable")
        content = _assertion_content(assertion)
        await self.vector_store.upsert_reviewed_assertion(
            kb_id=task.kb_id,
            embedding_model_spec=resolve_embedding_model(kb.embedding_model_spec),
            superseded_assertion_ids=(
                list(conflict.existing_assertion_ids or []) if conflict.resolution == "use_new" else []
            ),
            record={
                "assertion_id": assertion.assertion_id,
                "content": content,
                "kb_id": task.kb_id,
                "entity_id": assertion.linked_entity_id,
                "resolution_id": task.resolution_id,
                "version": task.id,
                "updated_at": utc_isoformat(),
                "file_id": assertion.file_id,
                "chunk_id": assertion.chunk_id,
                "predicate": assertion.predicate,
            },
        )

    async def _remove_stale_projection(self, task, assertion) -> None:
        await self.vector_store.delete_reviewed_assertions(
            task.kb_id,
            [assertion.assertion_id],
            max_version=task.id,
        )

        def write(tx):
            tx.run(
                """
                MATCH (assertion:ConflictKnowledgeAssertion {kb_id: $kb_id, assertion_id: $assertion_id})
                WHERE assertion.publish_sequence <= $publish_sequence
                SET assertion.is_active = false,
                    assertion.publish_status = 'superseded',
                    assertion.updated_at = $updated_at
                """,
                kb_id=task.kb_id,
                assertion_id=assertion.assertion_id,
                publish_sequence=task.id,
                updated_at=utc_isoformat(),
            )

        await asyncio.to_thread(neo4j_write, self.neo4j_connection.driver, write)


async def process_knowledge_conflict_publish(_ctx: dict[str, Any], task_id: str) -> str:
    return await KnowledgeConflictPublishService().process(task_id)


async def recover_knowledge_conflict_publish_tasks(ctx: dict[str, Any]) -> int:
    return await KnowledgeConflictPublishService().recover(queue=ctx.get("redis"))


def serialize_publish_task(task) -> dict[str, Any]:
    return {
        "task_id": task.task_id,
        "resolution_id": task.resolution_id,
        "status": task.status,
        "neo4j_status": task.neo4j_status,
        "vector_status": task.vector_status,
        "attempt_count": task.attempt_count,
        "max_attempts": task.max_attempts,
        "error_code": task.error_code,
        "last_error": task.last_error,
        "updated_at": task.updated_at,
        "completed_at": task.completed_at,
    }


def _assertion_content(assertion) -> str:
    value = assertion.normalized_value if assertion.normalized_value is not None else assertion.raw_value
    version = f" ({assertion.product_version})" if assertion.product_version else ""
    return f"{assertion.entity_name}{version} {assertion.predicate}: {value}"


def _publish_error_code(error: Exception) -> str:
    if isinstance(error, LookupError):
        return "publish_source_missing"
    if "embedding" in str(error).lower():
        return "vector_embedding_unavailable"
    return "publish_adapter_failed"


__all__ = [
    "KnowledgeConflictPublishService",
    "PUBLISH_JOB_NAME",
    "process_knowledge_conflict_publish",
    "recover_knowledge_conflict_publish_tasks",
    "serialize_publish_task",
]
