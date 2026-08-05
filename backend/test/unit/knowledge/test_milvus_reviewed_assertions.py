from __future__ import annotations

import pytest

from yuxi.knowledge.graphs import milvus_graph_vector_store as vector_module
from yuxi.knowledge.graphs.milvus_graph_vector_store import MilvusGraphVectorStore


def test_assertion_upsert_uses_stable_primary_key_and_filter_metadata():
    captured = []

    class Collection:
        def upsert(self, rows):
            captured.append(rows)

    record = {
        "assertion_id": "assertion-1",
        "content": "MiniServer M200 max_concurrent_users: 200",
        "kb_id": "kb-1",
        "entity_id": "entity-1",
        "resolution_id": "resolution-1",
        "version": 9,
        "updated_at": "2026-08-03T10:00:00Z",
        "file_id": "file-1",
        "chunk_id": "chunk-1",
        "predicate": "max_concurrent_users",
    }

    store = object.__new__(MilvusGraphVectorStore)
    store._upsert_assertion(Collection(), record, [0.1, 0.2])
    rows = captured[0]
    assert rows[0] == ["assertion-1"]
    assert rows[2:9] == [
        ["kb-1"],
        ["entity-1"],
        ["resolution-1"],
        [9],
        [True],
        ["succeeded"],
        ["2026-08-03T10:00:00Z"],
    ]


@pytest.mark.asyncio
async def test_assertion_search_applies_active_and_succeeded_filter(monkeypatch):
    calls = []

    async def run_io(function, *args, **kwargs):
        calls.append((function, args, kwargs))
        if function is vector_module.utility.has_collection:
            return True
        return [{"id": "assertion-1"}]

    store = object.__new__(MilvusGraphVectorStore)
    store.connection_alias = "test"

    async def embed(_values):
        return [[0.1, 0.2]]

    store._get_embedding_function = lambda _spec: embed
    monkeypatch.setattr(vector_module, "_run_milvus_query_io", run_io)

    result = await store.search_reviewed_assertions(
        kb_id="kb-1",
        query_text="M200",
        embedding_model_spec="test:embedding",
        top_k=5,
    )

    assert result == [{"id": "assertion-1"}]
    assert calls[-1][1][-1] == 'is_active == true and publish_status == "succeeded"'


def test_assertion_collection_name_is_stable_and_kb_scoped():
    first = MilvusGraphVectorStore._assertion_collection_name("kb-1")
    assert first == MilvusGraphVectorStore._assertion_collection_name("kb-1")
    assert first != MilvusGraphVectorStore._assertion_collection_name("kb-2")
    assert first.startswith("graph_assertions_")


def test_assertion_delete_is_version_bounded(monkeypatch):
    captured = []

    class Collection:
        def __init__(self, name, **_kwargs):
            self.name = name

        def delete(self, expr=None, **_kwargs):
            captured.append(expr)

        def flush(self):
            pass

    store = object.__new__(MilvusGraphVectorStore)
    store.connection_alias = "test"
    monkeypatch.setattr(vector_module.utility, "has_collection", lambda *a, **k: True)
    monkeypatch.setattr(vector_module, "Collection", Collection)

    store._delete_ids_before_version("graph_assertions_kb", ["assertion-1", "assertion-2"], 7)

    assert captured == ['id in ["assertion-1", "assertion-2"] and version <= 7']


@pytest.mark.asyncio
async def test_assertion_delete_without_version_bound_deletes_all(monkeypatch):
    calls = []

    def delete_plain(collection_name, ids):
        calls.append((collection_name, ids, None))

    def delete_bounded(collection_name, ids, max_version):
        calls.append((collection_name, ids, max_version))

    store = object.__new__(MilvusGraphVectorStore)
    monkeypatch.setattr(store, "_delete_ids", lambda *a: delete_plain(a[0], a[1]))
    monkeypatch.setattr(store, "_delete_ids_before_version", lambda *a: delete_bounded(a[0], a[1], a[2]))
    monkeypatch.setattr(store, "_assertion_collection_name", lambda _kb_id: "graph_assertions_kb")

    await store.delete_reviewed_assertions("kb-1", ["assertion-1"], max_version=9)
    await store.delete_reviewed_assertions("kb-1", ["assertion-2"])

    assert calls == [
        ("graph_assertions_kb", ["assertion-1"], 9),
        ("graph_assertions_kb", ["assertion-2"], None),
    ]
