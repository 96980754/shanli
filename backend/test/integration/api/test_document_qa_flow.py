from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from server.routers import knowledge_router
from yuxi.knowledge.document_qa import DocumentQAGenerator
from yuxi.knowledge.runtime import knowledge_base
from yuxi.repositories.document_qa_repository import DocumentQARepository
from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository
from yuxi.services.run_queue_service import close_queue_clients
from yuxi.services.task_service import tasker
from yuxi.storage.postgres.manager import pg_manager

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


@pytest_asyncio.fixture
async def qa_knowledge_database():
    pg_manager.initialize()
    await pg_manager.ensure_knowledge_schema()
    database = await knowledge_base.create_database(
        f"pytest_document_qa_{uuid.uuid4().hex}",
        "Pytest document QA database",
        kb_type="milvus",
        embedding_model_spec="siliconflow-cn:Pro/BAAI/bge-m3",
        created_by="pytest-qa-admin",
    )
    try:
        yield database
    finally:
        await knowledge_base.delete_database(database["kb_id"])


@pytest_asyncio.fixture
async def qa_task_runtime():
    await tasker.start()
    try:
        yield
    finally:
        await tasker._queue.join()
        await tasker.shutdown()
        await close_queue_clients()


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


async def test_document_qa_generate_edit_confirm_search_and_body_version(
    qa_knowledge_database,
    qa_task_runtime,
    monkeypatch,
) -> None:
    kb_id = qa_knowledge_database["kb_id"]
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

    app = FastAPI()
    app.include_router(knowledge_router.knowledge, prefix="/api")

    async def local_superadmin():
        return SimpleNamespace(uid="pytest-qa-admin", role="superadmin", department_id=None)

    app.dependency_overrides[knowledge_router.get_required_user] = local_superadmin
    file_repository = KnowledgeFileRepository()
    qa_repository = DocumentQARepository()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
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

        parsed = await kb.parse_file(kb_id, file_id, operator_id="pytest-qa-admin")
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

        before_confirm = await kb.aquery(
            qa_marker,
            kb_id,
            search_mode="vector",
            final_top_k=10,
            similarity_threshold=0.5,
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
        await tasker._queue.join()

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
        )
        assert all(item["metadata"]["chunk_id"] != f"qa:{draft['qa_id']}" for item in stale_search)
