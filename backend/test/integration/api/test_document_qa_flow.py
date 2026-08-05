from __future__ import annotations

import asyncio
import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from yuxi.knowledge.document_qa import DocumentQAGenerator
from yuxi.knowledge.runtime import knowledge_base
from yuxi.repositories.knowledge_chunk_repository import KnowledgeChunkRepository
from yuxi.repositories.document_qa_repository import DocumentQARepository
from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository
from yuxi.storage.postgres.manager import pg_manager

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


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


async def test_draft_mode_qa_generated_before_cleaning_confirmation(
    knowledge_database,
    knowledge_router_app,
    admin_headers,
    admin_user,
    view_only_user,
    task_runtime,
    tasker_idle_waiter,
    kb_background_waiter,
    monkeypatch,
) -> None:
    pg_manager.initialize()
    await pg_manager.ensure_knowledge_schema()
    kb_id = knowledge_database["kb_id"]
    unique = uuid.uuid4().hex
    body_marker = f"draftqa{unique}"
    qa_marker = f"draftqaretrieval{unique}"
    filename = f"draft-qa-{unique}.txt"

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
                "question": f"{qa_marker} 对应的清洗版本是多少？",
                "answer": "当前清洗版本即为待确认草稿版本。",
                "source_chunk_ids": [source.chunk_id],
                "evidence": [{"chunk_id": source.chunk_id, "text": body_marker}],
                "model_name": "pytest-draft-qa",
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

    async with AsyncClient(
        transport=ASGITransport(app=knowledge_router_app),
        base_url="http://test",
        headers=admin_headers,
    ) as client:
        upload = await client.post(
            "/api/knowledge/files/upload",
            params={"kb_id": kb_id},
            files={
                "file": (
                    filename,
                    f"{body_marker}  原始 排版混乱 文本。\n".encode(),
                    "text/plain",
                )
            },
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

        pending_record = await file_repository.get_by_file_id(file_id)
        assert pending_record is not None
        assert pending_record.status == "waiting_confirmation"
        assert pending_record.confirmed_at is None
        assert await KnowledgeChunkRepository().list_by_file_id(file_id) == []

        generate = await client.post(
            f"/api/knowledge/databases/{kb_id}/documents/{file_id}/qa/generate",
            json={"source_chunk_ids": [], "replace_generated": False},
        )
        assert generate.status_code == 200, generate.text
        list_payload, draft = await _wait_for_qa_draft(client, kb_id, file_id)
        assert list_payload["draft_mode"] is True
        assert list_payload["confirmable"] is False
        assert draft["status"] == "draft"
        assert draft["source"] == "generated"
        assert draft["source_chunk_ids"]
        assert draft["cleaning_version"] == pending["cleaning_version"]

        edited_question = f"{qa_marker} 的草稿版本校验？"
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
        edited = update.json()
        assert edited["source"] == "manual"
        assert edited["status"] == "draft"

        manual = await client.post(
            f"/api/knowledge/databases/{kb_id}/documents/{file_id}/qa",
            json={
                "question": f"{qa_marker} 手工新增问题？",
                "answer": f"{body_marker} 手工答案。",
                "source_chunk_ids": draft["source_chunk_ids"],
                "evidence": draft["evidence"],
            },
        )
        assert manual.status_code == 200, manual.text
        manual_qa = manual.json()
        assert manual_qa["source"] == "manual"

        rejected = await client.post(
            f"/api/knowledge/databases/{kb_id}/documents/{file_id}/qa/{manual_qa['qa_id']}/reject",
            json={"version": manual_qa["version"]},
        )
        assert rejected.status_code == 200, rejected.text
        assert rejected.json()["status"] == "rejected"

        reloaded = await client.get(f"/api/knowledge/databases/{kb_id}/documents/{file_id}/qa")
        assert reloaded.status_code == 200, reloaded.text
        reloaded_items = reloaded.json()["items"]
        assert any(item["qa_id"] == draft["qa_id"] and item["question"] == edited_question for item in reloaded_items)
        assert all(item["qa_id"] != manual_qa["qa_id"] for item in reloaded_items)

        async with AsyncClient(
            transport=ASGITransport(app=knowledge_router_app),
            base_url="http://test",
            headers=view_only_user["headers"],
        ) as readonly_client:
            readonly_qa = await readonly_client.get(f"/api/knowledge/databases/{kb_id}/documents/{file_id}/qa")
            assert readonly_qa.status_code == 200, readonly_qa.text
            assert readonly_qa.json()["readonly"] is True
            forbidden_generate = await readonly_client.post(
                f"/api/knowledge/databases/{kb_id}/documents/{file_id}/qa/generate",
                json={"source_chunk_ids": [], "replace_generated": False},
            )
            assert forbidden_generate.status_code == 403, forbidden_generate.text

        cross_kb = await client.get(f"/api/knowledge/databases/kb_{uuid.uuid4().hex}/documents/{file_id}/qa")
        assert cross_kb.status_code == 404, cross_kb.text

        confirm_body = await client.post(
            f"/api/knowledge/databases/{kb_id}/documents/{file_id}/cleaning/confirm",
            json={"version": pending["cleaning_version"]},
        )
        assert confirm_body.status_code == 200, confirm_body.text
        confirmed_payload = confirm_body.json()
        assert confirmed_payload["file_id"] == file_id

        indexed_record = await file_repository.get_by_file_id(file_id)
        assert indexed_record is not None
        assert indexed_record.status == "indexed"
        assert indexed_record.is_active is True
        assert indexed_record.confirmed_at is not None
        chunks = await KnowledgeChunkRepository().list_by_file_id(file_id)
        assert chunks
        assert body_marker in "\n".join(chunk.content for chunk in chunks)

        bound = await client.get(f"/api/knowledge/databases/{kb_id}/documents/{file_id}/qa")
        assert bound.status_code == 200, bound.text
        bound_payload = bound.json()
        assert bound_payload["draft_mode"] is False
        assert bound_payload["confirmable"] is True
        bound_item = next(item for item in bound_payload["items"] if item["qa_id"] == draft["qa_id"])
        real_chunk_ids = {chunk.chunk_id for chunk in chunks}
        assert bound_item["source_chunk_ids"] and all(cid in real_chunk_ids for cid in bound_item["source_chunk_ids"])

        qa_confirm = await client.post(
            f"/api/knowledge/databases/{kb_id}/documents/{file_id}/qa/{draft['qa_id']}/confirm",
            json={"version": bound_item["version"]},
        )
        assert qa_confirm.status_code == 200, qa_confirm.text
        assert qa_confirm.json()["sync_status"] == "synced"

        search = await kb.aquery(
            qa_marker,
            kb_id,
            search_mode="vector",
            final_top_k=10,
            similarity_threshold=0.5,
            use_reranker=False,
        )
        qa_hit = next(item for item in search if item["metadata"]["chunk_id"] == f"qa:{draft['qa_id']}")
        assert qa_hit["metadata"]["file_id"] == file_id
        assert body_marker in qa_hit["metadata"]["source_metadata"]["evidence"][0]["text"]

        repeated = await client.post(
            f"/api/knowledge/databases/{kb_id}/documents/{file_id}/cleaning/confirm",
            json={"version": pending["cleaning_version"]},
        )
        assert repeated.status_code == 200, repeated.text
        assert repeated.json()["idempotent"] is True

        await tasker_idle_waiter()
        await kb_background_waiter(kb_id)


async def test_document_qa_generate_edit_confirm_search_and_body_version(
    knowledge_database,
    knowledge_router_app,
    admin_headers,
    admin_user,
    view_only_user,
    task_runtime,
    tasker_idle_waiter,
    kb_background_waiter,
    monkeypatch,
) -> None:
    pg_manager.initialize()
    await pg_manager.ensure_knowledge_schema()
    kb_id = knowledge_database["kb_id"]
    unique = uuid.uuid4().hex
    body_marker = f"qaevidence{unique}"
    qa_marker = f"qaretrieval{unique}"
    revised_marker = f"qarevised{unique}"
    filename = f"document-qa-{unique}.txt"

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
            elif revised_marker in text:
                vector[1] = 1.0
            else:
                vector[2] = 1.0
            vectors.append(vector)
        return vectors

    async def async_embed(texts: list[str]) -> list[list[float]]:
        return deterministic_vectors(texts)

    async def mock_generate(_self, chunks, **_kwargs):
        source = chunks[0]
        evidence = body_marker
        assert evidence in source.content
        return [
            {
                "question": f"{qa_marker} 对应的批次大小是多少？",
                "answer": "默认批次大小为 40。",
                "source_chunk_ids": [source.chunk_id],
                "evidence": [{"chunk_id": source.chunk_id, "text": f"{body_marker} 默认批次大小为 40。"}],
                "model_name": "pytest-document-qa",
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
    qa_repository = DocumentQARepository()

    async with AsyncClient(
        transport=ASGITransport(app=knowledge_router_app),
        base_url="http://test",
        headers=admin_headers,
    ) as client:
        upload = await client.post(
            "/api/knowledge/files/upload",
            params={"kb_id": kb_id},
            files={
                "file": (
                    filename,
                    f"# QA Source\n\n{body_marker} 默认批次大小为 40。\n".encode(),
                    "text/plain",
                )
            },
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

        parsed = await kb.parse_file(kb_id, file_id, operator_id=admin_user["uid"])
        assert parsed["status"] == "parsed"
        record = await file_repository.get_by_file_id(file_id)
        regenerate = await client.post(
            f"/api/knowledge/databases/{kb_id}/documents/{file_id}/cleaning/regenerate",
            json={"version": record.cleaning_version, "use_ai": False},
        )
        assert regenerate.status_code == 200, regenerate.text
        confirm_body = await client.post(
            f"/api/knowledge/databases/{kb_id}/documents/{file_id}/cleaning/confirm",
            json={"version": regenerate.json()["cleaning_version"]},
        )
        assert confirm_body.status_code == 200, confirm_body.text
        indexed_record = await file_repository.get_by_file_id(file_id)
        assert indexed_record is not None
        assert indexed_record.is_active is True
        assert indexed_record.status == "indexed"

        generate = await client.post(
            f"/api/knowledge/databases/{kb_id}/documents/{file_id}/qa/generate",
            json={"source_chunk_ids": [], "replace_generated": False},
        )
        assert generate.status_code == 200, generate.text
        _list_payload, draft = await _wait_for_qa_draft(client, kb_id, file_id)
        assert draft["status"] == "draft"
        assert draft["source"] == "generated"
        assert draft["source_chunk_ids"]
        async with AsyncClient(
            transport=ASGITransport(app=knowledge_router_app),
            base_url="http://test",
            headers=view_only_user["headers"],
        ) as readonly_client:
            readonly_qa = await readonly_client.get(f"/api/knowledge/databases/{kb_id}/documents/{file_id}/qa")
            assert readonly_qa.status_code == 200, readonly_qa.text
            forbidden_generate = await readonly_client.post(
                f"/api/knowledge/databases/{kb_id}/documents/{file_id}/qa/generate",
                json={"source_chunk_ids": [], "replace_generated": False},
            )
            assert forbidden_generate.status_code == 403, forbidden_generate.text

        cross_kb = await client.get(f"/api/knowledge/databases/kb_{uuid.uuid4().hex}/documents/{file_id}/qa")
        assert cross_kb.status_code == 404, cross_kb.text

        before_confirm = await kb.aquery(
            qa_marker,
            kb_id,
            search_mode="vector",
            final_top_k=10,
            similarity_threshold=0.5,
            use_reranker=False,
        )
        assert all(item["metadata"]["chunk_id"] != f"qa:{draft['qa_id']}" for item in before_confirm)

        confirm = await client.post(
            f"/api/knowledge/databases/{kb_id}/documents/{file_id}/qa/{draft['qa_id']}/confirm",
            json={"version": draft["version"]},
        )
        assert confirm.status_code == 200, confirm.text
        confirmed = confirm.json()
        assert confirmed["status"] == "confirmed"
        assert confirmed["sync_status"] == "synced"

        search = await kb.aquery(
            qa_marker,
            kb_id,
            search_mode="vector",
            final_top_k=10,
            similarity_threshold=0.5,
            use_reranker=False,
        )
        qa_hit = next(item for item in search if item["metadata"]["chunk_id"] == f"qa:{draft['qa_id']}")
        assert "默认批次大小为 40" in qa_hit["content"]
        assert qa_hit["metadata"]["source_metadata"]["source_chunk_ids"] == draft["source_chunk_ids"]
        assert body_marker in qa_hit["metadata"]["source_metadata"]["evidence"][0]["text"]

        edited_question = f"{qa_marker} 的默认处理批次是多少？"
        update = await client.put(
            f"/api/knowledge/databases/{kb_id}/documents/{file_id}/qa/{draft['qa_id']}",
            json={
                "question": edited_question,
                "answer": draft["answer"],
                "source_chunk_ids": draft["source_chunk_ids"],
                "evidence": draft["evidence"],
                "version": confirmed["version"],
            },
        )
        assert update.status_code == 200, update.text
        edited = update.json()
        assert edited["source"] == "manual"
        assert edited["status"] == "draft"

        after_edit = await kb.aquery(
            qa_marker,
            kb_id,
            search_mode="vector",
            final_top_k=10,
            similarity_threshold=0.5,
            use_reranker=False,
        )
        assert all(item["metadata"]["chunk_id"] != f"qa:{draft['qa_id']}" for item in after_edit)

        reconfirm = await client.post(
            f"/api/knowledge/databases/{kb_id}/documents/{file_id}/qa/{draft['qa_id']}/confirm",
            json={"version": edited["version"]},
        )
        assert reconfirm.status_code == 200, reconfirm.text
        assert reconfirm.json()["sync_status"] == "synced"

        preview = await client.get(f"/api/knowledge/databases/{kb_id}/documents/{file_id}/cleaning")
        revised = await client.put(
            f"/api/knowledge/databases/{kb_id}/documents/{file_id}/cleaning/draft",
            json={
                "version": preview.json()["cleaning_version"],
                "content": f"# Revised\n\n{revised_marker} 默认批次大小为 50。\n",
            },
        )
        assert revised.status_code == 200, revised.text
        revised_confirm = await client.post(
            f"/api/knowledge/databases/{kb_id}/documents/{file_id}/cleaning/confirm",
            json={"version": revised.json()["cleaning_version"]},
        )
        assert revised_confirm.status_code == 200, revised_confirm.text
        revised_file_id = revised_confirm.json()["file_id"]
        assert revised_file_id != file_id
        await tasker_idle_waiter()

        old_qas = await qa_repository.list_by_file_id(
            kb_id=kb_id,
            file_id=file_id,
            include_rejected=True,
        )
        assert len(old_qas) == 1
        assert old_qas[0].status == "confirmed"
        assert old_qas[0].possibly_outdated is True
        assert old_qas[0].version > reconfirm.json()["version"]
        old_file = await file_repository.get_by_file_id(file_id)
        new_file = await file_repository.get_by_file_id(revised_file_id)
        assert old_file is not None and old_file.is_active is False
        assert new_file is not None and new_file.is_active is True
        assert new_file.previous_version_id == file_id

        stale_search = await kb.aquery(
            qa_marker,
            kb_id,
            search_mode="vector",
            final_top_k=10,
            similarity_threshold=0.5,
            use_reranker=False,
        )
        assert all(item["metadata"]["chunk_id"] != f"qa:{draft['qa_id']}" for item in stale_search)
        await kb_background_waiter(kb_id)
