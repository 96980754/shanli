from types import SimpleNamespace
import pytest
from yuxi.services.knowledge_conflict_service import (
    KnowledgeConflictError,
    KnowledgeConflictNotFound,
    KnowledgeConflictService,
)
class _FileRepository:
    def __init__(self, record):
        self.record = record
    async def get_by_file_id(self, file_id):
        return self.record if self.record and self.record.file_id == file_id else None
class _ChunkRepository:
    def __init__(self, record):
        self.record = record
    async def get_by_chunk_id(self, chunk_id):
        return self.record if self.record and self.record.chunk_id == chunk_id else None
def _file(**overrides):
    values = {
        "file_id": "file-1",
        "kb_id": "kb-1",
        "status": "indexed",
        "is_active": True,
        "is_folder": False,
        "cleaning_version": 2,
        "content_hash": "content-hash",
    }
    values.update(overrides)
    return SimpleNamespace(**values)
def _chunk(**overrides):
    values = {
        "chunk_id": "chunk-1",
        "file_id": "file-1",
        "kb_id": "kb-1",
        "content": "最大并发用户数为 100。",
    }
    values.update(overrides)
    return SimpleNamespace(**values)
def _payload(**overrides):
    values = {
        "file_id": "file-1",
        "chunk_id": "chunk-1",
        "evidence": "最大并发用户数为 100",
    }
    values.update(overrides)
    return values
@pytest.mark.asyncio
async def test_evidence_must_be_present_in_bound_chunk() -> None:
    service = KnowledgeConflictService(
        file_repository=_FileRepository(_file()),
        chunk_repository=_ChunkRepository(_chunk()),
    )
    with pytest.raises(KnowledgeConflictError, match="exact excerpt"):
        await service._validate_evidence(
            kb_id="kb-1",
            payload=_payload(evidence="最大并发用户数为 200"),
        )
@pytest.mark.asyncio
async def test_chunk_must_belong_to_source_file() -> None:
    service = KnowledgeConflictService(
        file_repository=_FileRepository(_file()),
        chunk_repository=_ChunkRepository(_chunk(file_id="file-2")),
    )
    with pytest.raises(KnowledgeConflictError, match="does not belong"):
        await service._validate_evidence(kb_id="kb-1", payload=_payload())
@pytest.mark.asyncio
async def test_cross_knowledge_base_file_is_hidden() -> None:
    service = KnowledgeConflictService(
        file_repository=_FileRepository(_file(kb_id="kb-2")),
        chunk_repository=_ChunkRepository(_chunk()),
    )
    with pytest.raises(KnowledgeConflictNotFound, match="not found"):
        await service._validate_evidence(kb_id="kb-1", payload=_payload())
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"is_active": False}, "not found"),
        ({"status": "waiting_confirmation"}, "active and indexed"),
    ],
)
async def test_only_active_indexed_documents_are_accepted(overrides, message) -> None:
    service = KnowledgeConflictService(
        file_repository=_FileRepository(_file(**overrides)),
        chunk_repository=_ChunkRepository(_chunk()),
    )
    with pytest.raises(KnowledgeConflictError, match=message):
        await service._validate_evidence(kb_id="kb-1", payload=_payload())
