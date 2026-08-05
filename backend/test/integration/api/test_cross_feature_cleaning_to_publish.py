"""Cross-feature integration: messy upload -> cleaning review -> QA -> confirm ->
ingested chunks -> reviewed assertion conflict -> publish closure -> GraphRAG."""

from __future__ import annotations

import asyncio
import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from yuxi.knowledge.document_qa import DocumentQAGenerator
from yuxi.knowledge.graphs.milvus_graph_vector_store import MilvusGraphVectorStore
from yuxi.knowledge.implementations.milvus import MilvusKB
from yuxi.knowledge.runtime import knowledge_base
from yuxi.models.providers.cache import model_cache
from yuxi.repositories.knowledge_chunk_repository import KnowledgeChunkRepository
from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository
from yuxi.repositories.knowledge_publish_repository import KnowledgePublishRepository
from yuxi.services.knowledge_conflict_publish_service import KnowledgeConflictPublishService
from yuxi.storage.neo4j import get_shared_neo4j_connection, neo4j_read, neo4j_write
from yuxi.storage.postgres.manager import pg_manager

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


class _DeterministicVectorStore(MilvusGraphVectorStore):
    """Deterministic adapter boundary for graph vector projection tests."""

    def _get_embedding_function(self, embedding_model_spec: str):
        info = model_cache.get_model_info(embedding_model_spec)
        assert info is not None and info.dimension
        dimension = info.dimension

        async def embed(values):
            return [[float((index % 7) + 1) / 10 for index in range(dimension)] for _value in values]

        return embed


async def _wait_for_qa_draft(client, kb_id: str, file_id: str, timeout: float = 30):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        response = await client.get(f"/api/knowledge/databases/{kb_id}/documents/{file_id}/qa")
        assert response.status_code == 200, response.text
        items = response.json()["items"]
        if items:
            return response.json(), items[0]
        await asyncio.sleep(0.2)
    raise AssertionError("document QA draft was not generated within the timeout")


async def test_messy_upload_cleaning_qa_confirm_to_conflict_publish_closure(
    knowledge_database,
    knowledge_router_app,
    admin_headers,
    admin_user,
    task_runtime,
    tasker_idle_waiter,
    kb_background_waiter,
    monkeypatch,
) -> None:
    pg_manager.initialize()
    await pg_manager.ensure_knowledge_schema()
    kb_id = knowledge_database["kb_id"]
    unique = uuid.uuid4().hex
    body_marker = f"crossfeature{unique}"
    qa_marker = f"crossqa{unique}"
    final_value = 600
    entity_name = f"MiniServer X200 {unique[:6]}"
    final_assertion = f"{entity_name} 最大并发用户数为 {final_value}"
    raw_text = f"{body_marker} 原始  排版 混乱  文本 {entity_name} 最大并发用户数为 500。\n"
    edited_text = f"# 修订\n\n{body_marker} 最终确认 {final_assertion}。\n"
    filename = f"cross-feature-{unique}.txt"

    kb = await knowledge_base.aget_kb(kb_id)
    if kb_id not in kb.databases_meta:
        await kb._load_metadata()
    collection = await kb._get_milvus_collection(kb_id)
    assert collection is not None
    embedding_field = next(field for field in collection.schema.fields if field.name == "embedding")
    dimension = int(embedding_field.params["dim"])
    assert dimension >= 3

    def deterministic_vectors(texts: list[str]) -> list[list[float]]:
        vectors = []
        for text in texts:
            vector = [0.0] * dimension
            if qa_marker in text:
                vector[0] = 1.0
            else:
                vector[-1] = 1.0
            vectors.append(vector)
        return vectors

    async def async_embed(texts: list[str]) -> list[list[float]]:
        return deterministic_vectors(texts)

    async def mock_generate(_self, chunks, **_kwargs):
        source = chunks[0]
        return [
            {
                "question": f"{qa_marker} 对应的最终确认值是多少？",
                "answer": f"最终确认值为 {final_value}。",
                "source_chunk_ids": [source.chunk_id],
                "evidence": [{"chunk_id": source.chunk_id, "text": final_assertion}],
                "model_name": "pytest-cross-feature-qa",
                "model_version": "1",
            }
        ]

    monkeypatch.setattr(
        kb,
        "_get_embedding_function",
        lambda _model_spec, *, sync=False: deterministic_vectors if sync else async_embed,
    )
    monkeypatch.setattr(DocumentQAGenerator, "generate", mock_generate)

    file_repository = KnowledgeFileRepository()
    repository = KnowledgePublishRepository()
    neo4j_connection = get_shared_neo4j_connection()
    deterministic_store = _DeterministicVectorStore()

    async def leave_in_outbox(_self, _task_id, *, queue=None):
        return True

    monkeypatch.setattr(KnowledgeConflictPublishService, "enqueue", leave_in_outbox)

    try:
        async with AsyncClient(
            transport=ASGITransport(app=knowledge_router_app),
            base_url="http://test",
            headers=admin_headers,
        ) as client:
            # 1-2. Upload a messy document with auto-confirm disabled.
            upload = await client.post(
                "/api/knowledge/files/upload",
                params={"kb_id": kb_id},
                files={"file": (filename, raw_text.encode(), "text/plain")},
            )
            assert upload.status_code == 200, upload.text
            upload_payload = upload.json()
            storage_item = upload_payload["file_path"]

            create = await client.post(
                f"/api/knowledge/databases/{kb_id}/documents/add",
                json={
                    "items": [storage_item],
                    "params": {
                        "source_paths": {storage_item: upload_payload["filename"]},
                        "auto_confirm": False,
                    },
                },
            )
            assert create.status_code == 200, create.text
            file_id = create.json()["items"][0]["file_id"]

            # 3. Parse then deterministic cleaning enters waiting_confirmation.
            parsed = await kb.parse_file(kb_id, file_id, operator_id=admin_user["uid"])
            assert parsed["status"] == "parsed"
            record = await file_repository.get_by_file_id(file_id)
            regenerate = await client.post(
                f"/api/knowledge/databases/{kb_id}/documents/{file_id}/cleaning/regenerate",
                json={"version": record.cleaning_version, "use_ai": False},
            )
            assert regenerate.status_code == 200, regenerate.text
            pending = regenerate.json()
            assert pending["status"] == "waiting_confirmation"
            assert body_marker in pending["cleaned_markdown"]

            # 4. Edit the cleaned draft to the final reviewed text.
            revised = await client.put(
                f"/api/knowledge/databases/{kb_id}/documents/{file_id}/cleaning/draft",
                json={"version": pending["cleaning_version"], "content": edited_text},
            )
            assert revised.status_code == 200, revised.text
            draft_version = revised.json()["cleaning_version"]

            # 5. Generate and edit a QA draft bound to the cleaning draft.
            generate = await client.post(
                f"/api/knowledge/databases/{kb_id}/documents/{file_id}/qa/generate",
                json={"source_chunk_ids": [], "replace_generated": False},
            )
            assert generate.status_code == 200, generate.text
            list_payload, draft = await _wait_for_qa_draft(client, kb_id, file_id)
            assert list_payload["draft_mode"] is True
            assert draft["source"] == "generated"
            assert draft["cleaning_version"] == draft_version
            edited_question = f"{qa_marker} 的最终确认值复核？"
            update = await client.put(
                f"/api/knowledge/databases/{kb_id}/documents/{file_id}/qa/{draft['qa_id']}",
                json={
                    "question": edited_question,
                    "answer": draft["answer"],
                    "source_chunk_ids": draft["source_chunk_ids"],
                    "evidence": draft["evidence"],
                    "version": draft["version"],
                },
            )
            assert update.status_code == 200, update.text
            assert update.json()["source"] == "manual"

            # 6. Confirm cleaning: final text + QA saved, then indexed.
            confirm_body = await client.post(
                f"/api/knowledge/databases/{kb_id}/documents/{file_id}/cleaning/confirm",
                json={"version": draft_version},
            )
            assert confirm_body.status_code == 200, confirm_body.text
            await tasker_idle_waiter()
            indexed_record = await file_repository.get_by_file_id(file_id)
            assert indexed_record is not None
            assert indexed_record.status == "indexed"
            assert indexed_record.is_active is True

            # 7. Official chunks come from the final confirmed text.
            chunks = await KnowledgeChunkRepository().list_by_file_id(file_id)
            assert chunks
            chunk_text = "\n".join(chunk.content for chunk in chunks)
            assert body_marker in chunk_text
            assert f"{entity_name} 最大并发用户数为 {final_value}" in chunk_text
            assert f"{entity_name} 最大并发用户数为 500" not in chunk_text
            evidence_chunk = next(chunk for chunk in chunks if final_assertion in chunk.content)

            # 8. QA evidence re-bound to official chunks after confirmation.
            bound = await client.get(f"/api/knowledge/databases/{kb_id}/documents/{file_id}/qa")
            assert bound.status_code == 200, bound.text
            bound_payload = bound.json()
            assert bound_payload["draft_mode"] is False
            bound_item = next(item for item in bound_payload["items"] if item["qa_id"] == draft["qa_id"])
            real_chunk_ids = {chunk.chunk_id for chunk in chunks}
            assert bound_item["source_chunk_ids"]
            assert all(cid in real_chunk_ids for cid in bound_item["source_chunk_ids"])
            qa_confirm = await client.post(
                f"/api/knowledge/databases/{kb_id}/documents/{file_id}/qa/{draft['qa_id']}/confirm",
                json={"version": bound_item["version"]},
            )
            assert qa_confirm.status_code == 200, qa_confirm.text
            assert qa_confirm.json()["sync_status"] == "synced"

            # 9. Form a reviewable assertion/conflict from the ingested knowledge.
            evaluated = await client.post(
                f"/api/knowledge/databases/{kb_id}/assertions/evaluate",
                json={
                    "entity_type": "Specification",
                    "entity_name": entity_name,
                    "linked_entity_id": None,
                    "predicate": "max_concurrent_users",
                    "raw_value": final_value,
                    "value_type": "integer",
                    "product_version": "V2",
                    "file_id": file_id,
                    "chunk_id": evidence_chunk.chunk_id,
                    "evidence": final_assertion,
                    "extraction_method": "manual",
                    "confidence": 1.0,
                },
            )
            assert evaluated.status_code == 200, evaluated.text
            conflict = evaluated.json()
            assert conflict["classification"] == "LINK_AMBIGUOUS"
            assertion_id = conflict["incoming_assertion"]["assertion_id"]

            # 10. GraphRAG must not read the reviewed knowledge before publish.
            before_publish = await object.__new__(MilvusKB)._build_reviewed_assertion_chunks(
                kb_id,
                [{"id": assertion_id, "score": 1.0}],
            )
            assert before_publish == []

            # 11. Human resolution creates a durable publish task.
            resolved = await client.post(
                f"/api/knowledge/databases/{kb_id}/conflicts/{conflict['conflict_id']}/resolve",
                json={"resolution": "create_new_entity", "version": conflict["version"]},
            )
            assert resolved.status_code == 200, resolved.text
            pending_publish = resolved.json()
            assert pending_publish["publish_status"] == "pending"
            assert pending_publish["incoming_assertion"]["status"] == "accepted"
            task_id = pending_publish["publish_task"]["task_id"]
            resolution_id = pending_publish["publish_task"]["resolution_id"]

            # 12-14. Execute the worker job function with real PG/Neo4j and the
            # deterministic vector adapter; task must reach succeeded.
            service = KnowledgeConflictPublishService(
                repository=repository,
                vector_store=deterministic_store,
                neo4j_connection=neo4j_connection,
            )
            assert await service.process(task_id) == "succeeded"
            task = await repository.get_task(kb_id=kb_id, task_id=task_id)
            assert task.status == "succeeded"
            assert task.neo4j_status == "succeeded"
            assert task.vector_status == "succeeded"

            # 15. Neo4j projection written exactly once.
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

            # 16. Graph vector projection written exactly once and searchable.
            hits = await deterministic_store.search_reviewed_assertions(
                kb_id=kb_id,
                query_text=final_assertion,
                embedding_model_spec=knowledge_database["embedding_model_spec"],
                top_k=5,
            )
            assert [hit["id"] for hit in hits].count(assertion_id) == 1

            # 17. GraphRAG only reads the published final knowledge with traceability.
            graph_chunks = await object.__new__(MilvusKB)._build_reviewed_assertion_chunks(
                kb_id,
                [{"id": assertion_id, "resolution_id": resolution_id, "score": 1.0}],
            )
            assert len(graph_chunks) == 1
            assert entity_name in graph_chunks[0]["content"]
            assert f"max_concurrent_users: {final_value}" in graph_chunks[0]["content"]
            metadata = graph_chunks[0]["metadata"]
            assert metadata["assertion_id"] == assertion_id
            assert metadata["resolution_id"] == resolution_id
            assert metadata["entity_id"] == pending_publish["entity_id"]
            assert metadata["predicate"] == "max_concurrent_users"
            assert metadata["file_id"] == file_id
            assert metadata["source_chunk_id"] == evidence_chunk.chunk_id
            assert metadata["evidence"] == final_assertion
            authoritative = await repository.list_published_assertions(
                kb_id=kb_id,
                assertion_ids=[assertion_id],
            )
            assert [item.assertion_id for item in authoritative] == [assertion_id]

            # 18. Traceability chain: raw file -> cleaning version -> QA -> assertion -> resolution.
            original = await file_repository.get_by_file_id(file_id)
            assert original is not None and original.cleaning_version >= 1
            assert bound_item["cleaning_version"] == original.cleaning_version
            assert conflict["incoming_assertion"]["chunk_id"] == evidence_chunk.chunk_id
            assert conflict["incoming_assertion"]["evidence"] == final_assertion
            assert pending_publish["resolution"] == "create_new_entity"
            assert resolution_id == task.resolution_id

        await tasker_idle_waiter()
        await kb_background_waiter(kb_id)
    finally:
        deterministic_store.drop_graph_collections(kb_id)

        def delete_test_graph(tx):
            tx.run("MATCH (n {kb_id: $kb_id}) DETACH DELETE n", kb_id=kb_id)

        await asyncio.to_thread(neo4j_write, neo4j_connection.driver, delete_test_graph)
