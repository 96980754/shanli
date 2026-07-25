from __future__ import annotations

from types import SimpleNamespace

import pytest
from yuxi.knowledge.implementations import milvus as milvus_module
from yuxi.knowledge.implementations.milvus import MilvusKB
from yuxi.knowledge.parser import unified as parser_module
from yuxi.knowledge.parser.ocr_routing import OCRRoutingError
from yuxi.repositories import knowledge_file_repository as repository_module
from yuxi.services.document_ingestion_service import DocumentIngestionService

pytestmark = pytest.mark.asyncio


def build_replacement_kb(monkeypatch, *, verify_result: bool):
    events = []
    updates = []
    file_meta = {
        "file_id": "new",
        "kb_id": "kb_1",
        "filename": "demo.txt",
        "status": "parsed",
        "markdown_file": "minio://knowledgebases/kb_1/parsed/new.md",
        "processing_params": {},
        "replacement_target_file_id": "old",
        "is_active": False,
    }
    claimed_record = SimpleNamespace(file_id="new")
    current_record = SimpleNamespace(file_id="new", is_active=False)

    class FakeRepository:
        async def update_fields_if_status(self, **kwargs):
            updates.append(kwargs["data"])
            return claimed_record

        async def update_fields(self, **kwargs):
            updates.append(kwargs["data"])
            return claimed_record

        async def get_by_file_id(self, file_id):
            return current_record

    monkeypatch.setattr(milvus_module, "KnowledgeFileRepository", FakeRepository)

    kb = object.__new__(MilvusKB)
    kb.databases_meta = {"kb_1": {"embedding_model_spec": "provider:model", "metadata": {}}}
    kb._file_record_to_meta = lambda _record: dict(file_meta)
    kb._get_embedding_function = lambda _spec: object()
    kb._split_text_into_chunks = lambda *_args, **_kwargs: [{"content": "chunk"}]
    kb._calculate_chunk_stats = lambda _chunks: {"chunk_count": 1, "token_count": 1}

    async def get_collection(_kb_id):
        return SimpleNamespace(flush=lambda: None)

    async def load_file_meta(_kb_id, _file_id):
        return dict(file_meta)

    async def read_markdown(_path):
        return "document content"

    async def delete_file_chunks(_kb_id, file_id):
        events.append(f"delete:{file_id}")

    async def embed(_kb_id, file_id, _collection, _chunks, _embedding):
        events.append(f"embed:{file_id}")

    async def verify(_kb_id, file_id):
        events.append(f"verify:{file_id}")
        return verify_result

    async def strict_delete(_kb_id, file_id):
        events.append(f"strict-delete:{file_id}")

    async def refresh(_kb_id):
        events.append("refresh")

    kb._get_milvus_collection = get_collection
    kb._load_file_meta = load_file_meta
    kb._read_markdown_from_minio = read_markdown
    kb.delete_file_chunks_only = delete_file_chunks
    kb._embed_and_store_chunks = embed
    kb.verify_file_vectors = verify
    kb.delete_file_vectors_and_chunks_strict = strict_delete
    kb.refresh_database_stats = refresh
    return kb, events, updates


async def test_replacement_index_failure_keeps_old_version_available(monkeypatch):
    kb, events, updates = build_replacement_kb(monkeypatch, verify_result=False)
    old_version = SimpleNamespace(is_active=True, vectors_available=True)
    activated = []

    async def fake_activate(self, **kwargs):
        activated.append(kwargs)

    monkeypatch.setattr(DocumentIngestionService, "activate_replacement", fake_activate)

    with pytest.raises(ValueError, match="not queryable"):
        await kb.index_file("kb_1", "new")

    assert old_version.is_active is True
    assert old_version.vectors_available is True
    assert activated == []
    assert events == ["strict-delete:new", "embed:new", "verify:new", "strict-delete:new"]
    assert updates[-1]["status"] == "error_indexing"
    assert updates[-1]["processing_stage"] == "verifying"


async def test_replacement_parse_failure_does_not_activate_or_modify_old_version(monkeypatch):
    old_version = SimpleNamespace(is_active=True, vectors_available=True)
    new_record = SimpleNamespace(file_id="new")
    updates = []

    class FakeRepository:
        async def update_fields_if_status(self, **_kwargs):
            return new_record

        async def update_fields(self, **kwargs):
            updates.append(kwargs["data"])
            return new_record

    async def fail_parse(**_kwargs):
        raise RuntimeError("deterministic parse failure")

    monkeypatch.setattr(repository_module, "KnowledgeFileRepository", FakeRepository)
    monkeypatch.setattr(parser_module.Parser, "aparse_result", fail_parse)

    kb = object.__new__(MilvusKB)
    kb._file_record_to_meta = lambda _record: {
        "file_id": "new",
        "path": "minio://knowledgebases/kb_1/upload/demo.txt",
        "processing_params": {},
        "replacement_target_file_id": "old",
        "is_active": False,
    }

    with pytest.raises(RuntimeError, match="deterministic parse failure"):
        await kb.parse_file("kb_1", "new")

    assert old_version.is_active is True
    assert old_version.vectors_available is True
    assert updates[-1]["status"] == "error_parsing"
    assert updates[-1]["processing_stage"] == "detecting"


async def test_ocr_parse_failure_persists_attempt_history_and_error_status(monkeypatch):
    new_record = SimpleNamespace(file_id="new")
    updates = []

    class FakeRepository:
        async def update_fields_if_status(self, **_kwargs):
            return new_record

        async def update_fields(self, **kwargs):
            updates.append(kwargs["data"])
            return new_record

    async def fail_parse(**_kwargs):
        raise OCRRoutingError(
            "OCR failed",
            attempts=[
                {
                    "provider": "rapid_ocr",
                    "stage": "ocr_processing",
                    "status": "rejected",
                    "duration_ms": 3,
                }
            ],
            warnings=["optional providers unavailable"],
        )

    monkeypatch.setattr(repository_module, "KnowledgeFileRepository", FakeRepository)
    monkeypatch.setattr(parser_module.Parser, "aparse_result", fail_parse)

    kb = object.__new__(MilvusKB)
    kb._file_record_to_meta = lambda _record: {
        "file_id": "new",
        "path": "minio://knowledgebases/kb_1/upload/scan.png",
        "processing_params": {},
        "is_active": True,
    }

    with pytest.raises(OCRRoutingError, match="OCR failed"):
        await kb.parse_file("kb_1", "new")

    assert updates[-1]["status"] == "error_parsing"
    assert updates[-1]["processing_stage"] == "detecting"
    assert updates[-1]["parse_metadata"]["attempts"][0]["provider"] == "rapid_ocr"


async def test_replacement_switch_happens_only_after_vector_verification(monkeypatch):
    kb, events, _updates = build_replacement_kb(monkeypatch, verify_result=True)

    async def fake_activate(self, **kwargs):
        events.append(f"activate:{kwargs['old_file_id']}->{kwargs['new_file_id']}")

    monkeypatch.setattr(DocumentIngestionService, "activate_replacement", fake_activate)

    await kb.index_file("kb_1", "new")

    assert events.index("verify:new") < events.index("activate:old->new")
    assert "strict-delete:old" not in events


async def test_repeated_replacement_index_replaces_candidate_vectors_instead_of_duplicating(monkeypatch):
    kb, _events, _updates = build_replacement_kb(monkeypatch, verify_result=True)
    vector_file_ids = []

    async def delete_candidate_vectors(_kb_id, file_id):
        vector_file_ids[:] = [existing for existing in vector_file_ids if existing != file_id]

    async def embed_candidate_vectors(_kb_id, file_id, _collection, _chunks, _embedding):
        vector_file_ids.append(file_id)

    async def verify_candidate_vectors(_kb_id, file_id):
        return vector_file_ids.count(file_id) == 1

    async def fake_activate(self, **_kwargs):
        return None

    kb.delete_file_vectors_and_chunks_strict = delete_candidate_vectors
    kb._embed_and_store_chunks = embed_candidate_vectors
    kb.verify_file_vectors = verify_candidate_vectors
    monkeypatch.setattr(DocumentIngestionService, "activate_replacement", fake_activate)

    await kb.index_file("kb_1", "new")
    await kb.index_file("kb_1", "new")

    assert vector_file_ids == ["new"]


async def test_replacement_vector_cleanup_preserves_postgres_chunks(monkeypatch):
    events = []
    chunk_ids = ["chunk-old"]

    class FakeChunkRepository:
        async def count_graph_indexed_by_file_id(self, file_id):
            assert file_id == "old"
            return 0

        async def delete_by_file_id(self, _file_id):
            raise AssertionError("replacement cleanup must preserve PostgreSQL chunks")

        async def list_by_file_id(self, file_id):
            assert file_id == "old"
            return [SimpleNamespace(chunk_id=chunk_id) for chunk_id in chunk_ids]

    async def get_collection(_kb_id):
        return SimpleNamespace(flush=lambda: events.append("flush"))

    async def delete_vectors(_collection, file_id):
        events.append(f"delete-vectors:{file_id}")

    async def refresh(_kb_id):
        events.append("refresh")

    kb = object.__new__(MilvusKB)
    kb._get_milvus_collection = get_collection
    kb._delete_file_chunks_from_milvus = delete_vectors
    kb.refresh_database_stats = refresh
    monkeypatch.setattr(milvus_module, "KnowledgeChunkRepository", FakeChunkRepository)

    await kb.delete_file_vectors_strict("kb_1", "old")

    assert events == ["delete-vectors:old", "flush", "refresh"]
    assert [chunk.chunk_id for chunk in await FakeChunkRepository().list_by_file_id("old")] == ["chunk-old"]


async def test_chunk_source_metadata_uses_overlapping_parser_block_without_fake_precision():
    kb = object.__new__(MilvusKB)
    chunks = [
        {
            "chunk_id": "chunk-1",
            "start_char_pos": 12,
            "end_char_pos": 25,
            "content": "sheet content",
        }
    ]
    parse_metadata = {
        "parser_name": "openpyxl",
        "parser_version": "3.1",
        "blocks": [
            {
                "block_type": "table",
                "order": 0,
                "sheet_name": "Summary",
                "start_char_pos": 10,
                "end_char_pos": 30,
            }
        ],
    }

    kb._attach_source_metadata(chunks, parse_metadata)

    assert chunks[0]["source_metadata"] == {
        "parser_name": "openpyxl",
        "parser_version": "3.1",
        "block_type": "table",
        "block_order": 0,
        "sheet_name": "Summary",
    }


async def test_chunk_source_metadata_keeps_parser_identity_when_no_block_overlap():
    kb = object.__new__(MilvusKB)
    chunks = [{"chunk_id": "chunk-1", "content": "content"}]

    kb._attach_source_metadata(
        chunks,
        {"parser_name": "native_text", "parser_version": "1", "blocks": []},
    )

    assert chunks[0]["source_metadata"] == {
        "parser_name": "native_text",
        "parser_version": "1",
    }
