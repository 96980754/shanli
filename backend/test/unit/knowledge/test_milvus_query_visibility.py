from __future__ import annotations

import pytest
from yuxi.knowledge.implementations import milvus as milvus_module
from yuxi.knowledge.implementations.milvus import MilvusKB


pytestmark = pytest.mark.asyncio


class _FakeHit:
    def __init__(self, file_id: str):
        self.file_id = file_id
        self.distance = 1.0


class _FakeAnnSearchRequest:
    def __init__(self, **kwargs):
        self.expr = kwargs.get("expr")


class _VisibilityCollection:
    def __init__(self):
        self.expressions: list[str | None] = []

    @staticmethod
    def _visible_file_id(expr: str | None) -> str | None:
        if expr and 'file_id not in ["candidate"]' in expr:
            return "active"
        if expr and 'file_id not in ["active"]' in expr:
            return "candidate"
        return "candidate"

    def search(self, **kwargs):
        expr = kwargs.get("expr")
        self.expressions.append(expr)
        file_id = self._visible_file_id(expr)
        return [[_FakeHit(file_id)]] if file_id else [[]]

    def hybrid_search(self, **kwargs):
        requests = kwargs["reqs"]
        expressions = [request.expr for request in requests]
        self.expressions.extend(expressions)
        assert expressions[0] == expressions[1]
        file_id = self._visible_file_id(expressions[0])
        return [[_FakeHit(file_id)]] if file_id else [[]]


def _build_query_kb(monkeypatch, inactive_file_ids: list[str]):
    collection = _VisibilityCollection()

    class FakeRepository:
        async def list_inactive_file_ids(self, *, kb_id: str):
            assert kb_id == "kb_1"
            return list(inactive_file_ids)

    async def get_collection(_kb_id):
        return collection

    async def hydrate(_kb_id, _chunks):
        return None

    kb = object.__new__(MilvusKB)
    kb.databases_meta = {"kb_1": {"embedding_model_spec": "provider:model"}}
    kb._get_milvus_collection = get_collection
    kb._get_query_params = lambda _kb_id: {}
    kb._get_embedding_function = lambda *_args, **_kwargs: lambda _texts: [[0.1]]
    kb._hydrate_chunk_sources = hydrate
    kb._build_chunk_from_hit = lambda hit, *_args, **_kwargs: {
        "content": hit.file_id,
        "metadata": {"file_id": hit.file_id, "chunk_id": f"chunk-{hit.file_id}"},
        "score": 1.0,
    }

    monkeypatch.setattr(milvus_module, "KnowledgeFileRepository", FakeRepository)
    monkeypatch.setattr(milvus_module, "AnnSearchRequest", _FakeAnnSearchRequest)
    return kb, collection


@pytest.mark.parametrize("search_mode", ["vector", "keyword", "hybrid"])
async def test_aquery_excludes_inactive_files_in_every_search_mode(monkeypatch, search_mode):
    kb, collection = _build_query_kb(monkeypatch, ["candidate"])

    results = await kb.aquery("query", "kb_1", search_mode=search_mode, similarity_threshold=0)

    assert [item["metadata"]["file_id"] for item in results] == ["active"]
    assert collection.expressions
    assert all(expr == 'file_id not in ["candidate"]' for expr in collection.expressions)


async def test_aquery_does_not_add_filter_when_every_file_is_active(monkeypatch):
    kb, collection = _build_query_kb(monkeypatch, [])

    results = await kb.aquery("query", "kb_1", search_mode="vector", similarity_threshold=0)

    assert [item["metadata"]["file_id"] for item in results] == ["candidate"]
    assert collection.expressions == [None]


async def test_inactive_file_ids_are_escaped_in_milvus_expression(monkeypatch):
    kb, _collection = _build_query_kb(monkeypatch, ['bad"\\id'])

    expression, inactive_ids = await kb._build_active_file_expr("kb_1")

    assert expression == 'file_id not in ["bad\\"\\\\id"]'
    assert inactive_ids == {'bad"\\id'}


async def test_replacement_becomes_queryable_after_switching_active(monkeypatch):
    inactive_file_ids = ["candidate"]
    kb, _collection = _build_query_kb(monkeypatch, inactive_file_ids)

    before_switch = await kb.aquery("query", "kb_1", search_mode="vector", similarity_threshold=0)
    inactive_file_ids.clear()
    after_switch = await kb.aquery("query", "kb_1", search_mode="vector", similarity_threshold=0)

    assert [item["metadata"]["file_id"] for item in before_switch] == ["active"]
    assert [item["metadata"]["file_id"] for item in after_switch] == ["candidate"]


async def test_visibility_lookup_failure_fails_closed_without_unfiltered_search(monkeypatch):
    collection = _VisibilityCollection()

    class FailingRepository:
        async def list_inactive_file_ids(self, *, kb_id: str):
            raise RuntimeError(f"cannot load visibility for {kb_id}")

    async def get_collection(_kb_id):
        return collection

    kb = object.__new__(MilvusKB)
    kb.databases_meta = {"kb_1": {"embedding_model_spec": "provider:model"}}
    kb._get_milvus_collection = get_collection
    kb._get_query_params = lambda _kb_id: {}
    monkeypatch.setattr(milvus_module, "KnowledgeFileRepository", FailingRepository)

    assert await kb.aquery("query", "kb_1") == []
    assert collection.expressions == []
