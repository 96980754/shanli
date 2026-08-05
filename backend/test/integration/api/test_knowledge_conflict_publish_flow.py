from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select

from yuxi.knowledge.graphs.milvus_graph_vector_store import MilvusGraphVectorStore
from yuxi.knowledge.implementations.milvus import MilvusKB
from yuxi.models.providers.cache import model_cache
from yuxi.repositories.knowledge_chunk_repository import KnowledgeChunkRepository
from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository
from yuxi.repositories.knowledge_publish_repository import KnowledgePublishRepository
from yuxi.services.knowledge_conflict_publish_service import KnowledgeConflictPublishService
from yuxi.storage.neo4j import get_shared_neo4j_connection, neo4j_read, neo4j_write
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_knowledge import KnowledgeConflict, KnowledgeConflictPublishTask
from yuxi.utils.datetime_utils import utc_now

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


class _FailingVectorStore:
    async def upsert_reviewed_assertion(self, **_kwargs):
        raise RuntimeError("milvus://internal-host/vector write failed")

    async def delete_reviewed_assertions(self, _kb_id, _assertion_ids, *, max_version=None):
        return None


class _DeterministicVectorStore(MilvusGraphVectorStore):
    def _get_embedding_function(self, embedding_model_spec: str):
        info = model_cache.get_model_info(embedding_model_spec)
        assert info is not None and info.dimension
        dimension = info.dimension

        async def embed(values):
            return [[float((index % 7) + 1) / 10 for index in range(dimension)] for _value in values]

        return embed


def _assertion_payload(
    *,
    file_id,
    chunk_id,
    evidence,
    linked_entity_id=None,
    raw_value=200,
    entity_name="MiniServer M200 Publish Test",
):
    return {
        "entity_type": "Specification",
        "entity_name": entity_name,
        "linked_entity_id": linked_entity_id,
        "predicate": "max_concurrent_users",
        "raw_value": raw_value,
        "value_type": "integer",
        "product_version": "V1",
        "file_id": file_id,
        "chunk_id": chunk_id,
        "evidence": evidence,
        "extraction_method": "manual",
        "confidence": 1.0,
    }


async def _wait_for_worker_attempt(repository, *, kb_id, conflict_id, timeout=45.0):
    deadline = asyncio.get_running_loop().time() + timeout
    last = None
    while asyncio.get_running_loop().time() < deadline:
        last = await repository.get_task_for_conflict(kb_id=kb_id, conflict_id=conflict_id)
        if last and last.attempt_count >= 1 and last.status in {"failed", "dead_letter", "succeeded"}:
            return last
        await asyncio.sleep(0.25)
    if last is not None and last.attempt_count >= 1:
        return last
    raise AssertionError(
        f"ARQ publish task did not reach a terminal attempt: "
        f"status={getattr(last, 'status', None)}, attempts={getattr(last, 'attempt_count', None)}"
    )


async def test_resolution_publish_worker_and_graphrag_closure(
    knowledge_database,
    knowledge_router_app,
    admin_headers,
    admin_user,
    view_only_user,
    monkeypatch,
):
    pg_manager.initialize()
    await pg_manager.ensure_knowledge_schema()
    kb_id = knowledge_database["kb_id"]
    unique = uuid.uuid4().hex
    file_id = f"file-publish-{unique}"
    chunk_id = f"chunk-publish-{unique}"
    evidence = "MiniServer M200 V1 supports 100 users, then the reviewed value becomes 200 and later 300."

    await KnowledgeFileRepository().upsert(
        file_id,
        {
            "kb_id": kb_id,
            "filename": f"publish-{unique}.md",
            "original_filename": f"publish-{unique}.md",
            "file_type": "md",
            "path": f"pytest/{file_id}.md",
            "markdown_file": f"minio://pytest/{file_id}.md",
            "status": "indexed",
            "content_hash": unique,
            "file_size": len(evidence),
            "chunk_count": 1,
            "token_count": 20,
            "cleaning_version": 1,
            "is_active": True,
            "is_folder": False,
            "created_by": admin_user["uid"],
        },
    )
    await KnowledgeChunkRepository().batch_upsert(
        [
            {
                "chunk_id": chunk_id,
                "file_id": file_id,
                "kb_id": kb_id,
                "chunk_index": 0,
                "content": evidence,
                "source_metadata": {"page_number": 1},
            }
        ]
    )

    original_enqueue = KnowledgeConflictPublishService.enqueue

    async def leave_in_outbox(_self, _task_id, *, queue=None):
        return True

    monkeypatch.setattr(KnowledgeConflictPublishService, "enqueue", leave_in_outbox)
    repository = KnowledgePublishRepository()
    neo4j_connection = get_shared_neo4j_connection()
    deterministic_store = _DeterministicVectorStore()

    try:
        async with AsyncClient(
            transport=ASGITransport(app=knowledge_router_app),
            base_url="http://test",
            headers=admin_headers,
        ) as client:
            evaluated = await client.post(
                f"/api/knowledge/databases/{kb_id}/assertions/evaluate",
                json=_assertion_payload(
                    file_id=file_id,
                    chunk_id=chunk_id,
                    evidence="supports 100 users",
                    raw_value=200,
                ),
            )
            assert evaluated.status_code == 200, evaluated.text
            conflict = evaluated.json()
            assert conflict["classification"] == "LINK_AMBIGUOUS"

            resolved = await client.post(
                f"/api/knowledge/databases/{kb_id}/conflicts/{conflict['conflict_id']}/resolve",
                json={"resolution": "create_new_entity", "version": conflict["version"]},
            )
            assert resolved.status_code == 200, resolved.text
            pending = resolved.json()
            assert pending["publish_status"] == "pending"
            assert pending["incoming_assertion"]["status"] == "accepted"
            assert pending["publish_task"]["status"] == "pending"
            task_id = pending["publish_task"]["task_id"]

            before_publish = await object.__new__(MilvusKB)._build_reviewed_assertion_chunks(
                kb_id,
                [{"id": pending["incoming_assertion"]["assertion_id"], "score": 1.0}],
            )
            assert before_publish == []

            async with AsyncClient(
                transport=ASGITransport(app=knowledge_router_app),
                base_url="http://test",
                headers=view_only_user["headers"],
            ) as readonly:
                forbidden = await readonly.post(
                    f"/api/knowledge/databases/{kb_id}/conflicts/{conflict['conflict_id']}/publish/retry"
                )
                assert forbidden.status_code == 403, forbidden.text
            hidden = await client.post(
                f"/api/knowledge/databases/kb_{uuid.uuid4().hex}/conflicts/{conflict['conflict_id']}/publish/retry"
            )
            assert hidden.status_code == 404, hidden.text

            partial_service = KnowledgeConflictPublishService(
                repository=repository,
                vector_store=_FailingVectorStore(),
                neo4j_connection=neo4j_connection,
            )
            assert await partial_service.process(task_id) == "failed"
            partial = await repository.get_task(kb_id=kb_id, task_id=task_id)
            assert partial.neo4j_status == "succeeded"
            assert partial.vector_status == "pending"
            assert partial.last_error == "[service location] write failed"

            neo4j_rows = neo4j_read(
                neo4j_connection.driver,
                """
                MATCH (a:ConflictKnowledgeAssertion {kb_id: $kb_id, assertion_id: $assertion_id})
                RETURN count(a) AS count
                """,
                kb_id=kb_id,
                assertion_id=pending["incoming_assertion"]["assertion_id"],
            )
            assert neo4j_rows == [{"count": 1}]

            await repository.retry(kb_id=kb_id, conflict_id=conflict["conflict_id"])
            await original_enqueue(KnowledgeConflictPublishService(), task_id)
            worker_result = await _wait_for_worker_attempt(
                repository,
                kb_id=kb_id,
                conflict_id=conflict["conflict_id"],
            )
            assert worker_result.attempt_count >= 1

            if worker_result.status != "succeeded":
                # The real embedding provider may be unavailable, or the worker may
                # have restarted mid-job. Expire the lease, retry, and complete the
                # vector projection deterministically through the adapter boundary.
                await _update_publish_task(task_id, lease_expires_at=utc_now() - timedelta(seconds=1))
                await repository.retry(kb_id=kb_id, conflict_id=conflict["conflict_id"])
                deterministic_service = KnowledgeConflictPublishService(
                    repository=repository,
                    vector_store=deterministic_store,
                    neo4j_connection=neo4j_connection,
                )
                assert await deterministic_service.process(task_id) == "succeeded"

            published = await repository.get_task(kb_id=kb_id, task_id=task_id)
            assert published.status == "succeeded"
            assert published.neo4j_status == "succeeded"
            assert published.vector_status == "succeeded"

            hits = await deterministic_store.search_reviewed_assertions(
                kb_id=kb_id,
                query_text="MiniServer concurrent users",
                embedding_model_spec=knowledge_database["embedding_model_spec"],
                top_k=5,
            )
            assertion_id = pending["incoming_assertion"]["assertion_id"]
            assert [hit["id"] for hit in hits].count(assertion_id) == 1
            graph_chunks = await object.__new__(MilvusKB)._build_reviewed_assertion_chunks(kb_id, hits)
            assert any(chunk["metadata"]["assertion_id"] == assertion_id for chunk in graph_chunks)
            assert await partial_service.process(task_id) == "not_claimed"

            retry_succeeded = await client.post(
                f"/api/knowledge/databases/{kb_id}/conflicts/{conflict['conflict_id']}/publish/retry"
            )
            assert retry_succeeded.status_code == 200, retry_succeeded.text
            assert retry_succeeded.json()["status"] == "succeeded"

            update = await client.post(
                f"/api/knowledge/databases/{kb_id}/assertions/evaluate",
                json=_assertion_payload(
                    file_id=file_id,
                    chunk_id=chunk_id,
                    evidence="later 300",
                    linked_entity_id=pending["entity_id"],
                    raw_value=300,
                ),
            )
            assert update.status_code == 200, update.text
            assert update.json()["classification"] == "CONFLICT"
            update_conflict = update.json()
            update_resolved = await client.post(
                f"/api/knowledge/databases/{kb_id}/conflicts/{update_conflict['conflict_id']}/resolve",
                json={"resolution": "use_new", "version": update_conflict["version"]},
            )
            assert update_resolved.status_code == 200, update_resolved.text
            update_payload = update_resolved.json()
            update_service = KnowledgeConflictPublishService(
                repository=repository,
                vector_store=deterministic_store,
                neo4j_connection=neo4j_connection,
            )
            assert await update_service.process(update_payload["publish_task"]["task_id"]) == "succeeded"

            authoritative = await repository.list_published_assertions(
                kb_id=kb_id,
                assertion_ids=[assertion_id, update_payload["incoming_assertion"]["assertion_id"]],
            )
            assert [(item.assertion_id, item.normalized_value) for item in authoritative] == [
                (update_payload["incoming_assertion"]["assertion_id"], 300)
            ]
            assert (
                await repository.list_published_assertions(
                    kb_id=f"other-{kb_id}",
                    assertion_ids=[update_payload["incoming_assertion"]["assertion_id"]],
                )
                == []
            )
            filtered = await object.__new__(MilvusKB)._build_reviewed_assertion_chunks(
                kb_id,
                [
                    {"id": assertion_id, "score": 1.0},
                    {
                        "id": update_payload["incoming_assertion"]["assertion_id"],
                        "resolution_id": update_payload["publish_task"]["resolution_id"],
                        "score": 0.9,
                    },
                ],
            )
            assert [chunk["content"] for chunk in filtered] == [
                "MiniServer M200 Publish Test max_concurrent_users: 300"
            ]
    finally:
        deterministic_store.drop_graph_collections(kb_id)

        def delete_test_graph(tx):
            tx.run("MATCH (n {kb_id: $kb_id}) DETACH DELETE n", kb_id=kb_id)

        await asyncio.to_thread(neo4j_write, neo4j_connection.driver, delete_test_graph)


async def _seed_evidence_file(*, kb_id: str, admin_user: dict, unique: str, evidence: str) -> tuple[str, str]:
    file_id = f"file-outbox-{unique}"
    chunk_id = f"chunk-outbox-{unique}"
    await KnowledgeFileRepository().upsert(
        file_id,
        {
            "kb_id": kb_id,
            "filename": f"outbox-{unique}.md",
            "original_filename": f"outbox-{unique}.md",
            "file_type": "md",
            "path": f"pytest/{file_id}.md",
            "markdown_file": f"minio://pytest/{file_id}.md",
            "status": "indexed",
            "content_hash": unique,
            "file_size": len(evidence),
            "chunk_count": 1,
            "token_count": 20,
            "cleaning_version": 1,
            "is_active": True,
            "is_folder": False,
            "created_by": admin_user["uid"],
        },
    )
    await KnowledgeChunkRepository().batch_upsert(
        [
            {
                "chunk_id": chunk_id,
                "file_id": file_id,
                "kb_id": kb_id,
                "chunk_index": 0,
                "content": evidence,
                "source_metadata": {"page_number": 1},
            }
        ]
    )
    return file_id, chunk_id


async def _count_publish_tasks(*, kb_id: str, conflict_id: str) -> int:
    async with pg_manager.get_async_session_context() as session:
        return await session.scalar(
            select(func.count())
            .select_from(KnowledgeConflictPublishTask)
            .where(
                KnowledgeConflictPublishTask.kb_id == kb_id,
                KnowledgeConflictPublishTask.conflict_id == conflict_id,
            )
        )


async def _get_conflict(*, kb_id: str, conflict_id: str) -> KnowledgeConflict:
    async with pg_manager.get_async_session_context() as session:
        return await session.scalar(
            select(KnowledgeConflict).where(
                KnowledgeConflict.kb_id == kb_id,
                KnowledgeConflict.conflict_id == conflict_id,
            )
        )


async def _update_publish_task(task_id: str, **fields) -> None:
    async with pg_manager.get_async_session_context() as session:
        task = await session.scalar(
            select(KnowledgeConflictPublishTask).where(KnowledgeConflictPublishTask.task_id == task_id)
        )
        assert task is not None
        for key, value in fields.items():
            setattr(task, key, value)
        await session.flush()


async def test_resolve_creates_single_durable_task_and_outbox_recovers(
    knowledge_database,
    knowledge_router_app,
    admin_headers,
    admin_user,
    monkeypatch,
):
    pg_manager.initialize()
    await pg_manager.ensure_knowledge_schema()
    kb_id = knowledge_database["kb_id"]
    unique = uuid.uuid4().hex
    evidence = "Outbox MiniServer M200 V1 supports 100 users, later 200 and 300."
    file_id, chunk_id = await _seed_evidence_file(kb_id=kb_id, admin_user=admin_user, unique=unique, evidence=evidence)

    async def leave_in_outbox(_self, _task_id, *, queue=None):
        return True

    monkeypatch.setattr(KnowledgeConflictPublishService, "enqueue", leave_in_outbox)
    repository = KnowledgePublishRepository()

    async with AsyncClient(
        transport=ASGITransport(app=knowledge_router_app),
        base_url="http://test",
        headers=admin_headers,
    ) as client:
        evaluated = await client.post(
            f"/api/knowledge/databases/{kb_id}/assertions/evaluate",
            json=_assertion_payload(
                file_id=file_id,
                chunk_id=chunk_id,
                evidence="supports 100 users",
                entity_name="MiniServer Outbox A",
                raw_value=100,
            ),
        )
        assert evaluated.status_code == 200, evaluated.text
        conflict = evaluated.json()

        resolved = await client.post(
            f"/api/knowledge/databases/{kb_id}/conflicts/{conflict['conflict_id']}/resolve",
            json={"resolution": "create_new_entity", "version": conflict["version"]},
        )
        assert resolved.status_code == 200, resolved.text
        pending = resolved.json()
        assert pending["publish_status"] == "pending"
        assert pending["publish_task"]["status"] == "pending"
        task_id = pending["publish_task"]["task_id"]
        assert await _count_publish_tasks(kb_id=kb_id, conflict_id=conflict["conflict_id"]) == 1

        stale = await client.post(
            f"/api/knowledge/databases/{kb_id}/conflicts/{conflict['conflict_id']}/resolve",
            json={"resolution": "use_new", "version": conflict["version"]},
        )
        assert stale.status_code == 409, stale.text
        assert await _count_publish_tasks(kb_id=kb_id, conflict_id=conflict["conflict_id"]) == 1

        batch_items = []
        for suffix, value, batch_evidence in (("B", 200, "later 200"), ("C", 300, "and 300")):
            evaluated_b = await client.post(
                f"/api/knowledge/databases/{kb_id}/assertions/evaluate",
                json=_assertion_payload(
                    file_id=file_id,
                    chunk_id=chunk_id,
                    evidence=batch_evidence,
                    entity_name=f"MiniServer Outbox {suffix}",
                    raw_value=value,
                ),
            )
            assert evaluated_b.status_code == 200, evaluated_b.text
            batch_items.append(evaluated_b.json())

        batch = await client.post(
            f"/api/knowledge/databases/{kb_id}/conflicts/batch-resolve",
            json={
                "items": [
                    {
                        "conflict_id": item["conflict_id"],
                        "resolution": "create_new_entity",
                        "version": item["version"],
                    }
                    for item in batch_items
                ]
            },
        )
        assert batch.status_code == 200, batch.text
        for item in batch.json()["items"]:
            assert item["publish_status"] == "pending"
            assert item["publish_task"]["status"] == "pending"
            assert await _count_publish_tasks(kb_id=kb_id, conflict_id=item["conflict_id"]) == 1
            batch_task_id = item["publish_task"]["task_id"]
            await repository.mark_target_succeeded(batch_task_id, "neo4j")
            await repository.mark_target_succeeded(batch_task_id, "vector")
            assert await repository.mark_succeeded(batch_task_id) is True

        claimed = await asyncio.gather(
            repository.claim(task_id, lease_seconds=30),
            repository.claim(task_id, lease_seconds=30),
        )
        assert len([item for item in claimed if item is not None]) == 1

        await _update_publish_task(task_id, lease_expires_at=utc_now() - timedelta(seconds=1))
        recoverable = await repository.list_recoverable_task_ids(limit=100)
        assert task_id in recoverable
        assert await repository.claim(task_id, lease_seconds=30) is not None
        task = await repository.get_task(kb_id=kb_id, task_id=task_id)
        assert task.attempt_count >= 2

        await _update_publish_task(
            task_id,
            status="pending",
            attempt_count=5,
            lease_expires_at=None,
            next_attempt_at=None,
        )
        assert await repository.claim(task_id, lease_seconds=30) is None
        task = await repository.get_task(kb_id=kb_id, task_id=task_id)
        assert task.status == "dead_letter"
        conflict_record = await _get_conflict(kb_id=kb_id, conflict_id=conflict["conflict_id"])
        assert conflict_record.publish_status == "dead_letter"


async def test_projection_idempotency_and_stale_version_protection(
    knowledge_database,
    knowledge_router_app,
    admin_headers,
    admin_user,
    monkeypatch,
):
    pg_manager.initialize()
    await pg_manager.ensure_knowledge_schema()
    kb_id = knowledge_database["kb_id"]
    unique = uuid.uuid4().hex
    evidence = "Idem MiniServer M200 V1 supports 100 users, later 200."
    file_id, chunk_id = await _seed_evidence_file(kb_id=kb_id, admin_user=admin_user, unique=unique, evidence=evidence)

    async def leave_in_outbox(_self, _task_id, *, queue=None):
        return True

    monkeypatch.setattr(KnowledgeConflictPublishService, "enqueue", leave_in_outbox)
    repository = KnowledgePublishRepository()
    neo4j_connection = get_shared_neo4j_connection()
    deterministic_store = _DeterministicVectorStore()

    try:
        async with AsyncClient(
            transport=ASGITransport(app=knowledge_router_app),
            base_url="http://test",
            headers=admin_headers,
        ) as client:
            evaluated = await client.post(
                f"/api/knowledge/databases/{kb_id}/assertions/evaluate",
                json=_assertion_payload(
                    file_id=file_id,
                    chunk_id=chunk_id,
                    evidence="supports 100 users",
                    entity_name="MiniServer Idem A",
                    raw_value=100,
                ),
            )
            assert evaluated.status_code == 200, evaluated.text
            conflict = evaluated.json()

            resolved = await client.post(
                f"/api/knowledge/databases/{kb_id}/conflicts/{conflict['conflict_id']}/resolve",
                json={"resolution": "create_new_entity", "version": conflict["version"]},
            )
            assert resolved.status_code == 200, resolved.text
            pending = resolved.json()
            task_id = pending["publish_task"]["task_id"]
            assertion_id = pending["incoming_assertion"]["assertion_id"]

            payload = await repository.load_authoritative_payload(task_id)
            assert payload is not None
            task = payload["task"]
            conflict_obj = payload["conflict"]
            assertion = payload["assertion"]
            entity = payload["entity"]
            assert entity is not None
            service = KnowledgeConflictPublishService(
                repository=repository,
                vector_store=deterministic_store,
                neo4j_connection=neo4j_connection,
            )

            await service._publish_neo4j(task, conflict_obj, assertion, entity)
            await service._publish_neo4j(task, conflict_obj, assertion, entity)
            rows = neo4j_read(
                neo4j_connection.driver,
                """
                MATCH (a:ConflictKnowledgeAssertion {kb_id: $kb_id, assertion_id: $assertion_id})
                RETURN count(a) AS count
                """,
                kb_id=kb_id,
                assertion_id=assertion_id,
            )
            assert rows == [{"count": 1}]

            await service._publish_vector(task, conflict_obj, assertion)
            await service._publish_vector(task, conflict_obj, assertion)
            hits = await deterministic_store.search_reviewed_assertions(
                kb_id=kb_id,
                query_text="MiniServer concurrent users",
                embedding_model_spec=knowledge_database["embedding_model_spec"],
                top_k=5,
            )
            assert [hit["id"] for hit in hits].count(assertion_id) == 1

            update = await client.post(
                f"/api/knowledge/databases/{kb_id}/conflicts/{conflict['conflict_id']}/resolve",
                json={"resolution": "use_new", "version": pending["version"]},
            )
            assert update.status_code == 200, update.text
            new_task_id = update.json()["publish_task"]["task_id"]
            assert new_task_id != task_id

            assert await service.process(task_id) == "stale"
            old_task = await repository.get_task(kb_id=kb_id, task_id=task_id)
            assert old_task.status == "succeeded"
            assert old_task.error_code == "stale_version"
            hits = await deterministic_store.search_reviewed_assertions(
                kb_id=kb_id,
                query_text="MiniServer concurrent users",
                embedding_model_spec=knowledge_database["embedding_model_spec"],
                top_k=5,
            )
            assert [hit["id"] for hit in hits].count(assertion_id) == 0

            assert await service.process(new_task_id) == "succeeded"
            hits = await deterministic_store.search_reviewed_assertions(
                kb_id=kb_id,
                query_text="MiniServer concurrent users",
                embedding_model_spec=knowledge_database["embedding_model_spec"],
                top_k=5,
            )
            assert [hit["id"] for hit in hits].count(assertion_id) == 1
            authoritative = await repository.list_published_assertions(
                kb_id=kb_id,
                assertion_ids=[assertion_id],
            )
            assert [item.assertion_id for item in authoritative] == [assertion_id]
    finally:
        deterministic_store.drop_graph_collections(kb_id)

        def delete_test_graph(tx):
            tx.run("MATCH (n {kb_id: $kb_id}) DETACH DELETE n", kb_id=kb_id)

        await asyncio.to_thread(neo4j_write, neo4j_connection.driver, delete_test_graph)
