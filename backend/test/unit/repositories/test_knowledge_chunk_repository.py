from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from yuxi.repositories import knowledge_chunk_repository as repo_module
from yuxi.repositories.knowledge_chunk_repository import KnowledgeChunkRepository, SQL_IN_BATCH_SIZE
from yuxi.storage.postgres.models_knowledge import KnowledgeChunk


def _extract_id_batch(statement) -> list[str]:
    id_batches = [value for value in statement.compile().params.values() if isinstance(value, list | tuple) and value]
    assert len(id_batches) == 1
    return list(id_batches[0])


def test_iter_batches_limits_sql_in_arguments():
    ids = [f"id-{index}" for index in range(SQL_IN_BATCH_SIZE * 2 + 1)]

    batches = list(KnowledgeChunkRepository._iter_batches(ids))

    assert [len(batch) for batch in batches] == [SQL_IN_BATCH_SIZE, SQL_IN_BATCH_SIZE, 1]
    assert [item for batch in batches for item in batch] == ids


def test_extraction_result_column_writes_none_as_sql_null():
    implementation = KnowledgeChunk.extraction_result.type.dialect_impl(postgresql.dialect())

    assert implementation.none_as_null is True


@pytest.mark.asyncio
async def test_count_with_extraction_result_excludes_json_null(monkeypatch):
    captured_conditions = ()

    async def fake_count(_self, kb_id, *conditions):
        nonlocal captured_conditions
        assert kb_id == "kb-1"
        captured_conditions = conditions
        return 1

    monkeypatch.setattr(KnowledgeChunkRepository, "_count_by_kb_id", fake_count)

    count = await KnowledgeChunkRepository().count_with_extraction_result_by_kb_id("kb-1")

    assert count == 1
    statement = select(KnowledgeChunk).where(*captured_conditions)
    compiled = str(statement.compile(dialect=postgresql.dialect()))
    assert "extraction_result IS NOT NULL" in compiled
    assert "jsonb_typeof" in compiled
    assert "null" in statement.compile().params.values()


@pytest.mark.asyncio
async def test_count_by_file_ids_splits_large_inputs(monkeypatch):
    file_ids = [f"file-{index}" for index in range(SQL_IN_BATCH_SIZE + 5)]
    batch_lengths: list[int] = []
    seen_file_ids: list[str] = []

    class FakeResult:
        def __init__(self, batch: list[str]):
            self.batch = batch

        def all(self):
            return [(file_id, 1) for file_id in self.batch]

    class FakeSession:
        async def execute(self, statement):
            batch = _extract_id_batch(statement)
            batch_lengths.append(len(batch))
            seen_file_ids.extend(batch)
            return FakeResult(batch)

    @asynccontextmanager
    async def fake_session_context():
        yield FakeSession()

    monkeypatch.setattr(repo_module.pg_manager, "get_async_session_context", fake_session_context)

    counts = await KnowledgeChunkRepository().count_by_file_ids(file_ids)

    assert batch_lengths == [SQL_IN_BATCH_SIZE, 5]
    assert seen_file_ids == file_ids
    assert counts["file-0"] == 1
    assert counts[f"file-{SQL_IN_BATCH_SIZE + 4}"] == 1


@pytest.mark.asyncio
async def test_list_by_file_ids_splits_large_inputs(monkeypatch):
    file_ids = [
        *(f"file-b-{index:05d}" for index in range(SQL_IN_BATCH_SIZE)),
        *(f"file-a-{index:05d}" for index in range(5)),
    ]
    batch_lengths: list[int] = []

    class FakeScalarResult:
        def __init__(self, chunks: list[SimpleNamespace]):
            self.chunks = chunks

        def all(self):
            return self.chunks

    class FakeResult:
        def __init__(self, chunks: list[SimpleNamespace]):
            self.chunks = chunks

        def scalars(self):
            return FakeScalarResult(self.chunks)

    class FakeSession:
        async def execute(self, statement):
            batch = _extract_id_batch(statement)
            batch_lengths.append(len(batch))
            return FakeResult([SimpleNamespace(file_id=file_id, chunk_index=0) for file_id in batch])

    @asynccontextmanager
    async def fake_session_context():
        yield FakeSession()

    monkeypatch.setattr(repo_module.pg_manager, "get_async_session_context", fake_session_context)

    chunks = await KnowledgeChunkRepository().list_by_file_ids(file_ids)

    assert batch_lengths == [SQL_IN_BATCH_SIZE, 5]
    assert [chunk.file_id for chunk in chunks] == sorted(file_ids)
