from datetime import datetime
from types import SimpleNamespace

import pytest

from yuxi.knowledge.base import FileStatus
from yuxi.services.document_qa_service import (
    DocumentQAError,
    DocumentQAService,
    QANotFound,
    QAVersionConflict,
)
from yuxi.knowledge.document_qa import QAProviderUnavailable


def _file(**overrides):
    values = {
        "file_id": "file-1",
        "kb_id": "kb-1",
        "status": FileStatus.INDEXED,
        "is_active": True,
        "is_folder": False,
        "markdown_file": "minio://parsed/kb-1/doc.md",
        "confirmed_at": datetime(2026, 7, 26),
        "cleaning_version": 3,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _chunk(chunk_id="chunk-1", file_id="file-1"):
    return SimpleNamespace(
        chunk_id=chunk_id,
        file_id=file_id,
        kb_id="kb-1",
        content="Shanli 2.1 支持向量检索，默认批次大小为 40。",
        chunk_index=0,
        source_metadata={"page_number": 1},
    )


class _FileRepo:
    def __init__(self, record):
        self.record = record

    async def get_by_file_id(self, file_id):
        return self.record if file_id == self.record.file_id else None


class _ChunkRepo:
    def __init__(self, chunks=None):
        self.chunks = chunks or [_chunk()]

    async def list_by_file_id(self, file_id):
        return [chunk for chunk in self.chunks if chunk.file_id == file_id]

    async def list_by_chunk_ids(self, chunk_ids):
        return [chunk for chunk in self.chunks if chunk.chunk_id in chunk_ids]


class _QARepo:
    def __init__(self):
        self.records = []

    async def list_by_file_id(self, *, kb_id, file_id, include_rejected=False):
        return [
            record
            for record in self.records
            if record.kb_id == kb_id and record.file_id == file_id and (include_rejected or record.status != "rejected")
        ]

    async def get_by_qa_id(self, qa_id):
        return next((record for record in self.records if record.qa_id == qa_id), None)

    async def create(self, data):
        record = SimpleNamespace(id=len(self.records) + 1, version=1, **data)
        self.records.append(record)
        return record

    async def create_or_get(self, data):
        existing = await self.find_by_identity(
            file_id=data["file_id"],
            content_hash=data["content_hash"],
            question_hash=data["question_hash"],
        )
        if existing is not None:
            return existing, False
        return await self.create(data), True

    async def update_with_version(self, *, kb_id, file_id, qa_id, expected_version, data):
        record = await self.get_by_qa_id(qa_id)
        if record is None or record.kb_id != kb_id or record.file_id != file_id or record.version != expected_version:
            return None
        for key, value in data.items():
            setattr(record, key, value)
        record.version += 1
        return record

    async def find_by_identity(self, *, file_id, content_hash, question_hash):
        return next(
            (
                record
                for record in self.records
                if record.file_id == file_id
                and record.content_hash == content_hash
                and record.question_hash == question_hash
            ),
            None,
        )

    async def mark_outdated_by_file_id(self, *, kb_id, file_id):
        affected = 0
        for record in self.records:
            if record.kb_id == kb_id and record.file_id == file_id and record.status != "rejected":
                record.possibly_outdated = True
                record.version += 1
                affected += 1
        return affected


class _Generator:
    async def generate(self, chunks, **_kwargs):
        return [
            {
                "question": "Shanli 2.1 支持什么检索？",
                "answer": "Shanli 2.1 支持向量检索，默认批次大小为 40。",
                "source_chunk_ids": ["chunk-1"],
                "evidence": [
                    {
                        "chunk_id": "chunk-1",
                        "text": "Shanli 2.1 支持向量检索，默认批次大小为 40。",
                    }
                ],
                "model_name": "configured-model",
                "model_version": "test",
            }
        ]


class _UnavailableGenerator:
    async def generate(self, _chunks, **_kwargs):
        raise QAProviderUnavailable("not configured")


class _Index:
    def __init__(self):
        self.upserts = []
        self.deletes = []

    async def upsert_confirmed_qa(self, **payload):
        self.upserts.append(payload)

    async def delete_confirmed_qa(self, kb_id, qa_id):
        self.deletes.append((kb_id, qa_id))


def _service(record=None):
    qa_repo = _QARepo()
    index = _Index()
    service = DocumentQAService(
        file_repository=_FileRepo(record or _file()),
        chunk_repository=_ChunkRepo(),
        qa_repository=qa_repo,
        generator=_Generator(),
        index_backend=index,
    )

    async def read_markdown(_path):
        return "# 正式正文\n\nShanli 2.1 支持向量检索，默认批次大小为 40。"

    service._read_markdown = read_markdown
    return service, qa_repo, index


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "record",
    [
        _file(status=FileStatus.WAITING_CONFIRMATION),
        _file(is_active=False),
        _file(confirmed_at=None),
        _file(status=FileStatus.ERROR_INDEXING),
    ],
)
async def test_generation_only_accepts_active_confirmed_indexed_document(record):
    service, _repo, _index = _service(record)

    with pytest.raises(QANotFound):
        await service.generate_drafts(
            kb_id="kb-1",
            file_id="file-1",
            operator_id="user-1",
        )


@pytest.mark.asyncio
async def test_generated_draft_is_version_bound_and_not_indexed():
    service, repo, index = _service()

    result = await service.generate_drafts(kb_id="kb-1", file_id="file-1", operator_id="user-1")

    assert len(result["items"]) == 1
    assert repo.records[0].status == "draft"
    assert repo.records[0].cleaning_version == 3
    assert repo.records[0].source == "generated"
    assert index.upserts == []


@pytest.mark.asyncio
async def test_confirm_is_idempotent_and_syncs_existing_collection():
    service, _repo, index = _service()
    created = await service.create_manual(
        kb_id="kb-1",
        file_id="file-1",
        operator_id="user-1",
        question="Shanli 2.1 支持什么检索？",
        answer="Shanli 2.1 支持向量检索，默认批次大小为 40。",
        source_chunk_ids=["chunk-1"],
        evidence=[{"chunk_id": "chunk-1", "text": "Shanli 2.1 支持向量检索，默认批次大小为 40。"}],
    )

    confirmed = await service.confirm(
        kb_id="kb-1",
        file_id="file-1",
        qa_id=created["qa_id"],
        operator_id="user-1",
        expected_version=created["version"],
    )
    repeated = await service.confirm(
        kb_id="kb-1",
        file_id="file-1",
        qa_id=created["qa_id"],
        operator_id="user-1",
        expected_version=created["version"],
    )

    assert confirmed["status"] == "confirmed"
    assert confirmed["sync_status"] == "synced"
    assert repeated["idempotent"] is True
    assert len(index.upserts) == 1


@pytest.mark.asyncio
async def test_editing_confirmed_qa_returns_to_draft_and_removes_online_projection():
    service, _repo, index = _service()
    created = await service.create_manual(
        kb_id="kb-1",
        file_id="file-1",
        operator_id="user-1",
        question="Shanli 2.1 支持什么检索？",
        answer="Shanli 2.1 支持向量检索，默认批次大小为 40。",
        source_chunk_ids=["chunk-1"],
        evidence=[{"chunk_id": "chunk-1", "text": "Shanli 2.1 支持向量检索，默认批次大小为 40。"}],
    )
    confirmed = await service.confirm(
        kb_id="kb-1",
        file_id="file-1",
        qa_id=created["qa_id"],
        operator_id="user-1",
        expected_version=created["version"],
    )

    updated = await service.update(
        kb_id="kb-1",
        file_id="file-1",
        qa_id=created["qa_id"],
        operator_id="user-2",
        expected_version=confirmed["version"],
        question="Shanli 2.1 的默认检索能力是什么？",
        answer="Shanli 2.1 支持向量检索，默认批次大小为 40。",
        source_chunk_ids=["chunk-1"],
        evidence=[{"chunk_id": "chunk-1", "text": "Shanli 2.1 支持向量检索，默认批次大小为 40。"}],
    )

    assert updated["source"] == "manual"
    assert updated["status"] == "draft"
    assert index.deletes == [("kb-1", created["qa_id"])]


@pytest.mark.asyncio
async def test_stale_editor_cannot_overwrite_qa():
    service, _repo, _index = _service()
    created = await service.create_manual(
        kb_id="kb-1",
        file_id="file-1",
        operator_id="user-1",
        question="Shanli 2.1 支持什么检索？",
        answer="Shanli 2.1 支持向量检索，默认批次大小为 40。",
        source_chunk_ids=["chunk-1"],
        evidence=[{"chunk_id": "chunk-1", "text": "Shanli 2.1 支持向量检索，默认批次大小为 40。"}],
    )

    with pytest.raises(QAVersionConflict):
        await service.update(
            kb_id="kb-1",
            file_id="file-1",
            qa_id=created["qa_id"],
            operator_id="user-2",
            expected_version=0,
            question=created["question"],
            answer=created["answer"],
            source_chunk_ids=["chunk-1"],
            evidence=created["evidence"],
        )


@pytest.mark.asyncio
async def test_stale_confirmed_editor_does_not_remove_online_projection():
    service, _repo, index = _service()
    created = await service.create_manual(
        kb_id="kb-1",
        file_id="file-1",
        operator_id="user-1",
        question="Shanli 2.1 支持什么检索？",
        answer="Shanli 2.1 支持向量检索，默认批次大小为 40。",
        source_chunk_ids=["chunk-1"],
        evidence=[{"chunk_id": "chunk-1", "text": "Shanli 2.1 支持向量检索，默认批次大小为 40。"}],
    )
    confirmed = await service.confirm(
        kb_id="kb-1",
        file_id="file-1",
        qa_id=created["qa_id"],
        operator_id="user-1",
        expected_version=created["version"],
    )

    with pytest.raises(QAVersionConflict):
        await service.update(
            kb_id="kb-1",
            file_id="file-1",
            qa_id=created["qa_id"],
            operator_id="user-2",
            expected_version=confirmed["version"] - 1,
            question=created["question"],
            answer=created["answer"],
            source_chunk_ids=["chunk-1"],
            evidence=created["evidence"],
        )

    assert index.deletes == []


@pytest.mark.asyncio
async def test_confirmed_qa_cannot_be_deleted_as_draft():
    service, _repo, index = _service()
    created = await service.create_manual(
        kb_id="kb-1",
        file_id="file-1",
        operator_id="user-1",
        question="Shanli 2.1 支持什么检索？",
        answer="Shanli 2.1 支持向量检索，默认批次大小为 40。",
        source_chunk_ids=["chunk-1"],
        evidence=[{"chunk_id": "chunk-1", "text": "Shanli 2.1 支持向量检索，默认批次大小为 40。"}],
    )
    confirmed = await service.confirm(
        kb_id="kb-1",
        file_id="file-1",
        qa_id=created["qa_id"],
        operator_id="user-1",
        expected_version=created["version"],
    )

    with pytest.raises(DocumentQAError, match="已确认"):
        await service.delete_draft(
            kb_id="kb-1",
            file_id="file-1",
            qa_id=created["qa_id"],
            operator_id="user-1",
            expected_version=confirmed["version"],
        )

    assert index.deletes == []


@pytest.mark.asyncio
async def test_generation_preserves_manual_and_rejected_tombstones():
    service, repo, _index = _service()
    manual = await service.create_manual(
        kb_id="kb-1",
        file_id="file-1",
        operator_id="user-1",
        question="Shanli 2.1 支持什么检索？",
        answer="Shanli 2.1 支持向量检索，默认批次大小为 40。",
        source_chunk_ids=["chunk-1"],
        evidence=[{"chunk_id": "chunk-1", "text": "Shanli 2.1 支持向量检索，默认批次大小为 40。"}],
    )
    await service.reject_or_delete(
        kb_id="kb-1",
        file_id="file-1",
        qa_id=manual["qa_id"],
        operator_id="user-1",
        expected_version=manual["version"],
    )

    generated = await service.generate_drafts(
        kb_id="kb-1",
        file_id="file-1",
        operator_id="user-2",
        replace_generated=True,
    )

    assert generated["items"] == []
    assert len(repo.records) == 1
    assert repo.records[0].status == "rejected"
    assert repo.records[0].source == "manual"


@pytest.mark.asyncio
async def test_generation_does_not_overwrite_manual_draft():
    service, repo, _index = _service()
    manual = await service.create_manual(
        kb_id="kb-1",
        file_id="file-1",
        operator_id="user-1",
        question="Shanli 2.1 支持什么检索？",
        answer="Shanli 2.1 支持向量检索，默认批次大小为 40。",
        source_chunk_ids=["chunk-1"],
        evidence=[{"chunk_id": "chunk-1", "text": "Shanli 2.1 支持向量检索，默认批次大小为 40。"}],
    )

    generated = await service.generate_drafts(
        kb_id="kb-1",
        file_id="file-1",
        operator_id="user-2",
        replace_generated=True,
    )

    assert generated["items"] == []
    assert len(repo.records) == 1
    assert repo.records[0].qa_id == manual["qa_id"]
    assert repo.records[0].source == "manual"
    assert repo.records[0].status == "draft"


@pytest.mark.asyncio
async def test_body_change_marks_existing_qa_outdated():
    service, repo, _index = _service()
    created = await service.create_manual(
        kb_id="kb-1",
        file_id="file-1",
        operator_id="user-1",
        question="Shanli 2.1 支持什么检索？",
        answer="Shanli 2.1 支持向量检索，默认批次大小为 40。",
        source_chunk_ids=["chunk-1"],
        evidence=[{"chunk_id": "chunk-1", "text": "Shanli 2.1 支持向量检索，默认批次大小为 40。"}],
    )

    affected = await service.mark_file_qas_outdated(kb_id="kb-1", file_id="file-1")

    assert affected == 1
    assert repo.records[0].qa_id == created["qa_id"]
    assert repo.records[0].possibly_outdated is True
    assert repo.records[0].version == created["version"] + 1


@pytest.mark.asyncio
async def test_provider_unavailable_skips_without_changing_indexed_document():
    record = _file()
    qa_repo = _QARepo()
    service = DocumentQAService(
        file_repository=_FileRepo(record),
        chunk_repository=_ChunkRepo(),
        qa_repository=qa_repo,
        generator=_UnavailableGenerator(),
        index_backend=_Index(),
    )

    async def read_markdown(_path):
        return "# 正式正文\n\nShanli 2.1 支持向量检索，默认批次大小为 40。"

    service._read_markdown = read_markdown

    result = await service.generate_drafts(kb_id="kb-1", file_id="file-1", operator_id="user-1")

    assert result == {"file_id": "file-1", "status": "skipped", "items": []}
    assert record.status == FileStatus.INDEXED
    assert qa_repo.records == []


@pytest.mark.asyncio
async def test_confirm_sync_failure_keeps_confirmed_record_and_document_indexed():
    class FailingIndex(_Index):
        async def upsert_confirmed_qa(self, **payload):
            self.upserts.append(payload)
            raise RuntimeError("provider endpoint and secret must not leak")

    record = _file()
    qa_repo = _QARepo()
    index = FailingIndex()
    service = DocumentQAService(
        file_repository=_FileRepo(record),
        chunk_repository=_ChunkRepo(),
        qa_repository=qa_repo,
        generator=_Generator(),
        index_backend=index,
    )

    async def read_markdown(_path):
        return "# 正式正文\n\nShanli 2.1 支持向量检索，默认批次大小为 40。"

    service._read_markdown = read_markdown
    created = await service.create_manual(
        kb_id="kb-1",
        file_id="file-1",
        operator_id="user-1",
        question="Shanli 2.1 支持什么检索？",
        answer="Shanli 2.1 支持向量检索，默认批次大小为 40。",
        source_chunk_ids=["chunk-1"],
        evidence=[{"chunk_id": "chunk-1", "text": "Shanli 2.1 支持向量检索，默认批次大小为 40。"}],
    )

    result = await service.confirm(
        kb_id="kb-1",
        file_id="file-1",
        qa_id=created["qa_id"],
        operator_id="user-1",
        expected_version=created["version"],
    )

    assert result["status"] == "confirmed"
    assert result["sync_status"] == "failed"
    assert result["sync_error"]
    assert record.status == FileStatus.INDEXED


@pytest.mark.asyncio
async def test_old_generation_task_cannot_write_after_body_version_changes():
    record = _file()

    class VersionChangingGenerator(_Generator):
        async def generate(self, chunks, **kwargs):
            result = await super().generate(chunks, **kwargs)
            record.cleaning_version += 1
            return result

    qa_repo = _QARepo()
    service = DocumentQAService(
        file_repository=_FileRepo(record),
        chunk_repository=_ChunkRepo(),
        qa_repository=qa_repo,
        generator=VersionChangingGenerator(),
        index_backend=_Index(),
    )

    async def read_markdown(_path):
        return "# 正式正文\n\nShanli 2.1 支持向量检索，默认批次大小为 40。"

    service._read_markdown = read_markdown

    with pytest.raises(QAVersionConflict):
        await service.generate_drafts(kb_id="kb-1", file_id="file-1", operator_id="user-1")

    assert qa_repo.records == []
