from __future__ import annotations

import asyncio
import uuid
from types import SimpleNamespace

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from server.routers import knowledge_router
from yuxi.knowledge.runtime import knowledge_base
from yuxi.repositories.knowledge_chunk_repository import KnowledgeChunkRepository
from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


@pytest_asyncio.fixture
async def cleaning_knowledge_database():
    database = await knowledge_base.create_database(
        f"pytest_cleaning_{uuid.uuid4().hex}",
        "Pytest document cleaning database",
        kb_type="milvus",
        embedding_model_spec="siliconflow-cn:Pro/BAAI/bge-m3",
        created_by="pytest-cleaning-admin",
    )
    try:
        yield database
    finally:
        await knowledge_base.delete_database(database["kb_id"])


def _result_file_ids(results: list[dict]) -> set[str]:
    return {str(item.get("metadata", {}).get("file_id") or item.get("file_id") or "") for item in results}


async def _milvus_file_rows(collection, file_id: str) -> list[dict]:
    await asyncio.to_thread(collection.flush)
    return await asyncio.to_thread(
        collection.query,
        expr=f'file_id == "{file_id}"',
        output_fields=["file_id", "chunk_id"],
        limit=100,
    )


async def _wait_for_old_vectors_cleanup(
    repository: KnowledgeFileRepository,
    collection,
    *,
    new_file_id: str,
    old_file_id: str,
    timeout: float = 60.0,
):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        new_record = await repository.get_by_file_id(new_file_id)
        old_rows = await _milvus_file_rows(collection, old_file_id)
        if (
            new_record is not None
            and new_record.processing_stage is None
            and new_record.processing_task_id is None
            and not old_rows
        ):
            return new_record
        await asyncio.sleep(0.25)
    raise AssertionError("replacement cleanup did not finish within the timeout")


async def test_cleaning_preview_edit_confirm_and_safe_reindex(
    cleaning_knowledge_database,
    monkeypatch,
) -> None:
    kb_id = cleaning_knowledge_database["kb_id"]
    unique_id = uuid.uuid4().hex
    original_marker = f"rawclean{unique_id}"
    first_marker = f"confirmedclean{unique_id}"
    revised_marker = f"revisedclean{unique_id}"
    filename = f"cleaning-{unique_id}.txt"

    kb = await knowledge_base.aget_kb(kb_id)
    if kb_id not in kb.databases_meta:
        await kb._load_metadata()
    collection = await kb._get_milvus_collection(kb_id)
    assert collection is not None
    embedding_field = next(field for field in collection.schema.fields if field.name == "embedding")
    dimension = int(embedding_field.params["dim"])
    assert dimension >= 3

    marker_dimensions = {
        first_marker: 0,
        revised_marker: 1,
        original_marker: 2,
    }

    def deterministic_vectors(texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            vector = [0.0] * dimension
            for marker, index in marker_dimensions.items():
                if marker in text:
                    vector[index] = 1.0
                    break
            else:
                vector[-1] = 1.0
            vectors.append(vector)
        return vectors

    async def async_embed(texts: list[str]) -> list[list[float]]:
        return deterministic_vectors(texts)

    monkeypatch.setattr(
        kb,
        "_get_embedding_function",
        lambda _model_spec, *, sync=False: deterministic_vectors if sync else async_embed,
    )

    app = FastAPI()
    app.include_router(knowledge_router.knowledge, prefix="/api")

    async def local_superadmin():
        return SimpleNamespace(uid="pytest-cleaning-admin", role="superadmin", department_id=None)

    app.dependency_overrides[knowledge_router.get_required_user] = local_superadmin

    repository = KnowledgeFileRepository()
    chunk_repository = KnowledgeChunkRepository()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        upload = await client.post(
            "/api/knowledge/files/upload",
            params={"kb_id": kb_id},
            files={
                "file": (
                    filename,
                    f"{original_marker}  original   parsed text.\n".encode(),
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
                    "content_hashes": {storage_item: "client-hash-is-not-trusted"},
                    "file_sizes": {storage_item: 1},
                    "auto_confirm": False,
                },
            },
        )
        assert create.status_code == 200, create.text
        original_file_id = create.json()["items"][0]["file_id"]

        parsed = await kb.parse_file(kb_id, original_file_id, operator_id="pytest-cleaning-admin")
        assert parsed["status"] == "parsed"
        parsed_record = await repository.get_by_file_id(original_file_id)
        assert parsed_record is not None
        assert parsed_record.original_markdown_file

        regenerate = await client.post(
            f"/api/knowledge/databases/{kb_id}/documents/{original_file_id}/cleaning/regenerate",
            json={"version": parsed_record.cleaning_version, "use_ai": False},
        )
        assert regenerate.status_code == 200, regenerate.text
        generated = regenerate.json()
        assert generated["status"] == "waiting_confirmation"
        assert original_marker in generated["original_markdown"]
        assert await chunk_repository.list_by_file_id(original_file_id) == []
        assert await _milvus_file_rows(collection, original_file_id) == []

        first_content = f"# Confirmed\n\n{first_marker} edited clean content.\n"
        save = await client.put(
            f"/api/knowledge/databases/{kb_id}/documents/{original_file_id}/cleaning/draft",
            json={
                "content": first_content,
                "version": generated["cleaning_version"],
            },
        )
        assert save.status_code == 200, save.text
        saved = save.json()
        assert saved["status"] == "waiting_confirmation"
        assert first_marker in saved["cleaned_markdown"]
        assert await chunk_repository.list_by_file_id(original_file_id) == []
        assert await _milvus_file_rows(collection, original_file_id) == []

        confirm = await client.post(
            f"/api/knowledge/databases/{kb_id}/documents/{original_file_id}/cleaning/confirm",
            json={"version": saved["cleaning_version"]},
        )
        assert confirm.status_code == 200, confirm.text
        assert confirm.json()["file_id"] == original_file_id

        first_record = await repository.get_by_file_id(original_file_id)
        assert first_record is not None
        assert first_record.status == "indexed"
        assert first_record.is_active is True
        assert first_record.confirmed_at is not None
        first_chunks = await chunk_repository.list_by_file_id(original_file_id)
        first_chunk_ids = [chunk.chunk_id for chunk in first_chunks]
        first_chunk_count = int(first_record.chunk_count or 0)
        first_token_count = int(first_record.token_count or 0)
        assert first_chunk_ids
        assert first_chunk_count > 0
        assert first_token_count > 0
        assert first_marker in "\n".join(chunk.content for chunk in first_chunks)
        original_download = await kb.get_file_download(
            kb_id,
            original_file_id,
            variant="original",
        )
        parsed_download = await kb.get_file_download(
            kb_id,
            original_file_id,
            variant="parsed",
        )
        assert original_marker.encode() in original_download["content"]
        assert first_marker.encode() in parsed_download["content"]
        first_results = await kb.aquery(
            first_marker,
            kb_id,
            search_mode="vector",
            final_top_k=10,
            similarity_threshold=0.5,
        )
        assert original_file_id in _result_file_ids(first_results)

        preview = await client.get(f"/api/knowledge/databases/{kb_id}/documents/{original_file_id}/cleaning")
        assert preview.status_code == 200, preview.text
        assert original_marker in preview.json()["original_markdown"]

        revised_content = f"# Revised\n\n{revised_marker} second confirmed content.\n"
        revised_save = await client.put(
            f"/api/knowledge/databases/{kb_id}/documents/{original_file_id}/cleaning/draft",
            json={
                "content": revised_content,
                "version": preview.json()["cleaning_version"],
            },
        )
        assert revised_save.status_code == 200, revised_save.text
        revised_draft = revised_save.json()
        assert revised_draft["status"] == "waiting_confirmation"
        assert await _milvus_file_rows(collection, original_file_id)
        before_switch = await kb.aquery(
            revised_marker,
            kb_id,
            search_mode="vector",
            final_top_k=10,
            similarity_threshold=0.5,
        )
        assert original_file_id not in _result_file_ids(before_switch)

        revised_confirm = await client.post(
            f"/api/knowledge/databases/{kb_id}/documents/{original_file_id}/cleaning/confirm",
            json={"version": revised_draft["cleaning_version"]},
        )
        assert revised_confirm.status_code == 200, revised_confirm.text
        revised_payload = revised_confirm.json()
        revised_file_id = revised_payload["file_id"]
        assert revised_file_id != original_file_id
        assert revised_payload["previous_file_id"] == original_file_id

        revised_record = await repository.get_by_file_id(revised_file_id)
        historical_record = await repository.get_by_file_id(original_file_id)
        assert revised_record is not None
        assert historical_record is not None
        assert revised_record.is_active is True
        assert revised_record.previous_version_id == original_file_id
        assert historical_record.is_active is False
        assert historical_record.superseded_at is not None
        assert historical_record.cleaning_draft_file == historical_record.markdown_file

        revised_results = await kb.aquery(
            revised_marker,
            kb_id,
            search_mode="vector",
            final_top_k=10,
            similarity_threshold=0.5,
        )
        assert revised_file_id in _result_file_ids(revised_results)

        await _wait_for_old_vectors_cleanup(
            repository,
            collection,
            new_file_id=revised_file_id,
            old_file_id=original_file_id,
        )
        assert await _milvus_file_rows(collection, original_file_id) == []

        historical_chunks = await chunk_repository.list_by_file_id(original_file_id)
        assert [chunk.chunk_id for chunk in historical_chunks] == first_chunk_ids
        assert await chunk_repository.get_by_chunk_id(first_chunk_ids[0]) is not None
        historical_record = await repository.get_by_file_id(original_file_id)
        assert historical_record is not None
        assert int(historical_record.chunk_count or 0) == first_chunk_count
        assert int(historical_record.token_count or 0) == first_token_count

        historical_original_download = await kb.get_file_download(
            kb_id,
            original_file_id,
            variant="original",
        )
        historical_parsed_download = await kb.get_file_download(
            kb_id,
            original_file_id,
            variant="parsed",
        )
        assert original_marker.encode() in historical_original_download["content"]
        assert first_marker.encode() in historical_parsed_download["content"]

        historical_preview = await client.get(f"/api/knowledge/databases/{kb_id}/documents/{original_file_id}/cleaning")
        assert historical_preview.status_code == 200, historical_preview.text
        assert original_marker in historical_preview.json()["original_markdown"]
        assert first_marker in historical_preview.json()["cleaned_markdown"]

        repeated = await client.post(
            f"/api/knowledge/databases/{kb_id}/documents/{revised_file_id}/cleaning/confirm",
            json={"version": revised_record.cleaning_version},
        )
        assert repeated.status_code == 200, repeated.text
        assert repeated.json()["idempotent"] is True
