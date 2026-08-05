from types import SimpleNamespace

import pytest

from yuxi.knowledge.implementations import milvus as milvus_module
from yuxi.knowledge.implementations.milvus import MilvusKB

pytestmark = pytest.mark.asyncio


class _Collection:
    def __init__(self):
        self.deleted = []
        self.inserted = []
        self.flushed = 0

    def delete(self, expr):
        self.deleted.append(expr)

    def insert(self, entities):
        self.inserted.append(entities)

    def flush(self):
        self.flushed += 1


async def test_confirmed_qa_upsert_and_delete_use_existing_collection():
    collection = _Collection()
    kb = object.__new__(MilvusKB)
    kb.databases_meta = {"kb-1": {"embedding_model_spec": "configured:embedding"}}

    async def get_collection(_kb_id):
        return collection

    async def embed(values):
        assert values == ["问题：Shanli 支持什么检索？\n答案：支持向量检索。"]
        return [[0.1, 0.2]]

    kb._get_milvus_collection = get_collection
    kb._get_embedding_function = lambda _spec: embed

    await kb.upsert_confirmed_qa(
        kb_id="kb-1",
        qa_id="qa-1",
        file_id="file-1",
        question="Shanli 支持什么检索？",
        answer="支持向量检索。",
    )
    await kb.delete_confirmed_qa("kb-1", "qa-1")

    assert collection.deleted == ['id == "qa:qa-1"', 'id == "qa:qa-1"']
    assert collection.inserted[0][2] == ["qa:qa-1"]
    assert collection.inserted[0][3] == ["file-1"]
    assert collection.flushed == 2


async def test_qa_search_result_hydrates_original_evidence(monkeypatch):
    class FakeFileRepository:
        async def get_filenames_by_file_ids(self, **_kwargs):
            return {"file-1": "guide.md"}

    class FakeChunkRepository:
        async def list_by_chunk_ids(self, _chunk_ids):
            return []

    class FakeQARepository:
        async def list_by_qa_ids(self, qa_ids):
            assert qa_ids == ["qa-1"]
            return [
                SimpleNamespace(
                    qa_id="qa-1",
                    kb_id="kb-1",
                    status="confirmed",
                    sync_status="synced",
                    source_chunk_ids=["chunk-1"],
                    evidence=[{"chunk_id": "chunk-1", "text": "支持向量检索"}],
                    source="manual",
                    confirmed_by="user-1",
                )
            ]

    monkeypatch.setattr(milvus_module, "KnowledgeFileRepository", FakeFileRepository)
    monkeypatch.setattr(milvus_module, "KnowledgeChunkRepository", FakeChunkRepository)
    monkeypatch.setattr(milvus_module, "DocumentQARepository", FakeQARepository)
    chunks = [{"metadata": {"file_id": "file-1", "chunk_id": "qa:qa-1"}}]

    await object.__new__(MilvusKB)._hydrate_chunk_sources("kb-1", chunks)

    metadata = chunks[0]["metadata"]
    assert metadata["source"] == "guide.md"
    assert metadata["source_metadata"]["source_type"] == "document_qa"
    assert metadata["source_metadata"]["source_chunk_ids"] == ["chunk-1"]
    assert metadata["source_metadata"]["evidence"][0]["text"] == "支持向量检索"


async def test_unsynced_qa_projection_is_removed_from_query_results(monkeypatch):
    class FakeFileRepository:
        async def get_filenames_by_file_ids(self, **_kwargs):
            return {"file-1": "guide.md"}

    class FakeChunkRepository:
        async def list_by_chunk_ids(self, _chunk_ids):
            return []

    class FakeQARepository:
        async def list_by_qa_ids(self, _qa_ids):
            return [
                SimpleNamespace(
                    qa_id="qa-stale",
                    kb_id="kb-1",
                    status="draft",
                    sync_status="failed",
                )
            ]

    monkeypatch.setattr(milvus_module, "KnowledgeFileRepository", FakeFileRepository)
    monkeypatch.setattr(milvus_module, "KnowledgeChunkRepository", FakeChunkRepository)
    monkeypatch.setattr(milvus_module, "DocumentQARepository", FakeQARepository)
    chunks = [
        {"content": "stale answer", "metadata": {"file_id": "file-1", "chunk_id": "qa:qa-stale"}},
        {"content": "document text", "metadata": {"file_id": "file-1", "chunk_id": "chunk-1"}},
    ]

    await object.__new__(MilvusKB)._hydrate_chunk_sources("kb-1", chunks)

    assert [chunk["metadata"]["chunk_id"] for chunk in chunks] == ["chunk-1"]


async def test_reviewed_assertion_hydration_rechecks_postgres_publish_state(monkeypatch):
    class FakeFileRepository:
        async def get_filenames_by_file_ids(self, **_kwargs):
            return {"file-1": "specification.md"}

    class FakeChunkRepository:
        async def list_by_chunk_ids(self, _chunk_ids):
            return []

    class FakeQARepository:
        async def list_by_qa_ids(self, _qa_ids):
            return []

    class FakePublishRepository:
        async def list_published_assertions(self, *, kb_id, assertion_ids):
            assert kb_id == "kb-1"
            assert assertion_ids == ["assertion-1", "assertion-stale"]
            return [
                SimpleNamespace(
                    assertion_id="assertion-1",
                    linked_entity_id="entity-1",
                    predicate="max_concurrent_users",
                    file_id="file-1",
                    chunk_id="source-chunk-1",
                    evidence="M200 V2 supports 200 concurrent users.",
                )
            ]

    monkeypatch.setattr(milvus_module, "KnowledgeFileRepository", FakeFileRepository)
    monkeypatch.setattr(milvus_module, "KnowledgeChunkRepository", FakeChunkRepository)
    monkeypatch.setattr(milvus_module, "DocumentQARepository", FakeQARepository)
    monkeypatch.setattr(milvus_module, "KnowledgePublishRepository", FakePublishRepository)
    chunks = [
        {"metadata": {"file_id": "file-1", "chunk_id": "assertion:assertion-1"}},
        {"metadata": {"file_id": "file-1", "chunk_id": "assertion:assertion-stale"}},
    ]

    await object.__new__(MilvusKB)._hydrate_chunk_sources("kb-1", chunks)

    assert [chunk["metadata"]["chunk_id"] for chunk in chunks] == ["assertion:assertion-1"]
    metadata = chunks[0]["metadata"]
    assert metadata["source"] == "specification.md"
    assert metadata["source_metadata"] == {
        "source_type": "reviewed_knowledge_assertion",
        "assertion_id": "assertion-1",
        "entity_id": "entity-1",
        "predicate": "max_concurrent_users",
        "file_id": "file-1",
        "chunk_id": "source-chunk-1",
        "evidence": "M200 V2 supports 200 concurrent users.",
    }


async def test_reviewed_assertion_chunks_filter_wrong_kb_and_unpublished_hits(monkeypatch):
    class FakePublishRepository:
        async def list_published_assertions(self, *, kb_id, assertion_ids):
            assert kb_id == "kb-1"
            assert assertion_ids == ["assertion-1", "assertion-pending", "assertion-other-kb"]
            return [
                SimpleNamespace(
                    assertion_id="assertion-1",
                    entity_name="MiniServer M200",
                    predicate="max_concurrent_users",
                    normalized_value=200,
                    raw_value=200,
                    file_id="file-1",
                    chunk_id="source-chunk-1",
                    linked_entity_id="entity-1",
                    evidence="M200 V2 supports 200 concurrent users.",
                )
            ]

    monkeypatch.setattr(milvus_module, "KnowledgePublishRepository", FakePublishRepository)
    hits = [
        {"id": "assertion-1", "resolution_id": "resolution-1", "score": 0.98},
        {"id": "assertion-pending", "resolution_id": "resolution-2", "score": 0.95},
        {"id": "assertion-other-kb", "resolution_id": "resolution-3", "score": 0.91},
    ]

    chunks = await object.__new__(MilvusKB)._build_reviewed_assertion_chunks("kb-1", hits)

    assert len(chunks) == 1
    assert chunks[0]["content"] == "MiniServer M200 max_concurrent_users: 200"
    assert chunks[0]["metadata"]["assertion_id"] == "assertion-1"
    assert chunks[0]["metadata"]["resolution_id"] == "resolution-1"
