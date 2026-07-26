from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from server.routers import knowledge_router
from yuxi.config.app import config
from yuxi.knowledge.enrichment import DocumentEnrichmentGenerator
from yuxi.knowledge.runtime import knowledge_base
from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository
from yuxi.services.run_queue_service import close_queue_clients
from yuxi.services.task_service import tasker

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


@pytest_asyncio.fixture
async def enrichment_knowledge_database():
    database = await knowledge_base.create_database(
        f"pytest_enrichment_{uuid.uuid4().hex}",
        "Pytest document enrichment database",
        kb_type="milvus",
        embedding_model_spec="siliconflow-cn:Pro/BAAI/bge-m3",
        created_by="pytest-enrichment-admin",
    )
    try:
        yield database
    finally:
        await knowledge_base.delete_database(database["kb_id"])


@pytest_asyncio.fixture
async def enrichment_task_runtime():
    await tasker.start()
    try:
        yield
    finally:
        await tasker._queue.join()
        await tasker.shutdown()
        await close_queue_clients()


async def _wait_for_enrichment(client, kb_id: str, file_id: str, *, min_version: int, timeout: float = 30):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        response = await client.get(f"/api/knowledge/databases/{kb_id}/documents/{file_id}/enrichment")
        assert response.status_code == 200, response.text
        payload = response.json()
        if payload["version"] >= min_version and payload["status"] not in {"generating", "not_generated"}:
            return payload
        await asyncio.sleep(0.2)
    raise AssertionError("document enrichment did not finish within the timeout")


async def test_document_enrichment_generation_manual_protection_and_body_version(
    enrichment_knowledge_database,
    enrichment_task_runtime,
    monkeypatch,
) -> None:
    kb_id = enrichment_knowledge_database["kb_id"]
    unique = uuid.uuid4().hex
    original_marker = f"enrichoriginal{unique}"
    revised_marker = f"enrichrevised{unique}"
    filename = f"enrichment-{unique}.txt"

    kb = await knowledge_base.aget_kb(kb_id)
    if kb_id not in kb.databases_meta:
        await kb._load_metadata()
    collection = await kb._get_milvus_collection(kb_id)
    assert collection is not None
    embedding_field = next(field for field in collection.schema.fields if field.name == "embedding")
    dimension = int(embedding_field.params["dim"])

    def deterministic_vectors(texts: list[str]) -> list[list[float]]:
        vectors = []
        for text in texts:
            vector = [0.0] * dimension
            vector[0 if original_marker in text else 1] = 1.0
            vectors.append(vector)
        return vectors

    async def async_embed(texts: list[str]) -> list[list[float]]:
        return deterministic_vectors(texts)

    async def mock_generate(
        _self,
        markdown,
        *,
        components,
        model_spec,
        **_kwargs,
    ):
        assert model_spec == "pytest:document-enrichment"
        marker = revised_marker if revised_marker in markdown else original_marker
        result = {
            "model_name": "pytest-enrichment-model",
            "model_version": "1",
        }
        if "summary" in components:
            result["summary"] = f"{marker} 文档摘要。"
        if "keywords" in components:
            result["keywords"] = [marker, "知识库"]
        if "tags" in components:
            result["tags"] = ["RAG", "rag"]
        return result

    monkeypatch.setattr(
        kb,
        "_get_embedding_function",
        lambda _model_spec, *, sync=False: deterministic_vectors if sync else async_embed,
    )
    monkeypatch.setattr(DocumentEnrichmentGenerator, "generate", mock_generate)
    monkeypatch.setattr(config, "document_enrichment_auto_generate", True)
    monkeypatch.setattr(config, "document_enrichment_model", "pytest:document-enrichment")

    app = FastAPI()
    app.include_router(knowledge_router.knowledge, prefix="/api")

    async def local_superadmin():
        return SimpleNamespace(uid="pytest-enrichment-admin", role="superadmin", department_id=None)

    app.dependency_overrides[knowledge_router.get_required_user] = local_superadmin
    repository = KnowledgeFileRepository()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        upload = await client.post(
            "/api/knowledge/files/upload",
            params={"kb_id": kb_id},
            files={
                "file": (
                    filename,
                    f"{original_marker} 知识库 2.1 正式正文。\n".encode(),
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

        parsed = await kb.parse_file(kb_id, file_id, operator_id="pytest-enrichment-admin")
        assert parsed["status"] == "parsed"
        record = await repository.get_by_file_id(file_id)
        assert record is not None

        draft_response = await client.post(
            f"/api/knowledge/databases/{kb_id}/documents/{file_id}/cleaning/regenerate",
            json={"version": record.cleaning_version, "use_ai": False},
        )
        assert draft_response.status_code == 200, draft_response.text
        draft = draft_response.json()
        confirm = await client.post(
            f"/api/knowledge/databases/{kb_id}/documents/{file_id}/cleaning/confirm",
            json={"version": draft["cleaning_version"]},
        )
        assert confirm.status_code == 200, confirm.text

        generated = await _wait_for_enrichment(client, kb_id, file_id, min_version=2)
        assert generated["status"] == "ready"
        assert generated["content_version"] == draft["cleaning_version"]
        assert generated["summary"]["source"] == "generated"
        assert generated["summary"]["text"] == f"{original_marker} 文档摘要。"
        assert [item["normalized_value"] for item in generated["keywords"]] == [
            original_marker,
            "知识库",
        ]
        assert [item["normalized_name"] for item in generated["tags"]] == ["rag"]

        manual_summary = f"{original_marker} 人工摘要 2.1。"
        summary_response = await client.put(
            f"/api/knowledge/databases/{kb_id}/documents/{file_id}/enrichment/summary",
            json={"version": generated["version"], "text": manual_summary},
        )
        assert summary_response.status_code == 200, summary_response.text
        summary_payload = summary_response.json()
        keywords_response = await client.put(
            f"/api/knowledge/databases/{kb_id}/documents/{file_id}/enrichment/keywords",
            json={
                "version": summary_payload["version"],
                "values": [original_marker, "知识库"],
            },
        )
        assert keywords_response.status_code == 200, keywords_response.text
        keywords_payload = keywords_response.json()
        tags_response = await client.put(
            f"/api/knowledge/databases/{kb_id}/documents/{file_id}/enrichment/tags",
            json={"version": keywords_payload["version"], "values": ["人工标签"]},
        )
        assert tags_response.status_code == 200, tags_response.text
        manual = tags_response.json()
        assert manual["summary"]["source"] == "manual"
        assert all(item["source"] == "manual" for item in manual["keywords"])
        assert all(item["source"] == "manual" for item in manual["tags"])

        regenerate = await client.post(
            f"/api/knowledge/databases/{kb_id}/documents/{file_id}/enrichment/generate",
            json={"components": ["summary", "keywords", "tags"], "overwrite_manual": False},
        )
        assert regenerate.status_code == 200, regenerate.text
        await tasker._queue.join()
        protected_response = await client.get(f"/api/knowledge/databases/{kb_id}/documents/{file_id}/enrichment")
        assert protected_response.status_code == 200, protected_response.text
        protected = protected_response.json()
        assert protected["summary"]["text"] == manual_summary
        assert protected["tags"][0]["name"] == "人工标签"

        cleaning_preview = await client.get(f"/api/knowledge/databases/{kb_id}/documents/{file_id}/cleaning")
        assert cleaning_preview.status_code == 200, cleaning_preview.text
        revised_draft = await client.put(
            f"/api/knowledge/databases/{kb_id}/documents/{file_id}/cleaning/draft",
            json={
                "version": cleaning_preview.json()["cleaning_version"],
                "content": f"# Revised\n\n{revised_marker} 知识库 2.1 更新正文。\n",
            },
        )
        assert revised_draft.status_code == 200, revised_draft.text
        revised_confirm = await client.post(
            f"/api/knowledge/databases/{kb_id}/documents/{file_id}/cleaning/confirm",
            json={"version": revised_draft.json()["cleaning_version"]},
        )
        assert revised_confirm.status_code == 200, revised_confirm.text
        revised_file_id = revised_confirm.json()["file_id"]
        assert revised_file_id != file_id

        await tasker._queue.join()
        revised_response = await client.get(f"/api/knowledge/databases/{kb_id}/documents/{revised_file_id}/enrichment")
        assert revised_response.status_code == 200, revised_response.text
        revised = revised_response.json()
        assert revised["summary"]["text"] == manual_summary
        assert revised["summary"]["source"] == "manual"
        assert revised["summary"]["status"] == "possibly_outdated"
        assert revised["possibly_outdated"] is True
        assert revised["content_version"] == revised_draft.json()["cleaning_version"]

        old_record = await repository.get_by_file_id(file_id)
        new_record = await repository.get_by_file_id(revised_file_id)
        assert old_record is not None and old_record.is_active is False
        assert new_record is not None and new_record.is_active is True
        assert new_record.previous_version_id == file_id

    await tasker._queue.join()
