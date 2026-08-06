from __future__ import annotations
import asyncio
from datetime import datetime
from types import SimpleNamespace
import pytest
from yuxi.knowledge.base import FileStatus
from yuxi.knowledge.cleaning import OptionalAIDocumentCleaner
from yuxi.services import document_cleaning_service as cleaning_service_module
from yuxi.services.document_cleaning_service import (
    CleaningVersionConflict,
    DocumentCleaningError,
    DocumentCleaningService,
)
def _record(**overrides):
    values = {
        "file_id": "file-1",
        "kb_id": "kb-1",
        "parent_id": None,
        "filename": "doc.md",
        "original_filename": "doc.md",
        "file_type": "md",
        "path": "minio://knowledgebases/kb-1/upload/doc.md",
        "minio_url": None,
        "markdown_file": "minio://parsed/kb-1/parsed/file-1.md",
        "original_markdown_file": "minio://parsed/kb-1/parsed/file-1.md",
        "cleaning_draft_file": None,
        "cleaning_metadata": None,
        "cleaning_version": 0,
        "confirmed_at": None,
        "confirmed_by": None,
        "status": FileStatus.PARSED,
        "content_hash": "hash",
        "file_size": 10,
        "chunk_count": 0,
        "token_count": 0,
        "content_type": "file",
        "processing_params": {},
        "parse_metadata": {"blocks": []},
        "processing_stage": None,
        "processing_progress": 55,
        "replacement_target_file_id": None,
        "previous_version_id": None,
        "is_active": True,
        "superseded_at": None,
        "is_folder": False,
        "error_message": None,
        "created_by": "user-1",
        "updated_by": "user-1",
    }
    values.update(overrides)
    return SimpleNamespace(**values)
class _FakeRepository:
    def __init__(self, record):
        self.record = record
        self.reject_next_version_update = False
        self.candidate = None
    async def get_by_file_id(self, file_id):
        if self.candidate and file_id == self.candidate.file_id:
            return self.candidate
        return self.record if file_id == self.record.file_id else None
    async def update_fields(self, *, file_id, kb_id, data):
        record = await self.get_by_file_id(file_id)
        if record is None or record.kb_id != kb_id:
            return None
        for key, value in data.items():
            setattr(record, key, value)
        return record
    async def update_cleaning_fields_with_version(
        self,
        *,
        kb_id,
        file_id,
        expected_version,
        data,
        increment_version,
        allowed_statuses=None,
    ):
        record = await self.get_by_file_id(file_id)
        if (
            self.reject_next_version_update
            or record is None
            or record.kb_id != kb_id
            or record.cleaning_version != expected_version
            or (allowed_statuses and record.status not in allowed_statuses)
        ):
            self.reject_next_version_update = False
            return None
        for key, value in data.items():
            setattr(record, key, value)
        if increment_version:
            record.cleaning_version += 1
        return record
    async def create_cleaning_replacement_candidate(
        self,
        *,
        file_id,
        kb_id,
        target_file_id,
        data,
        target_restore_data=None,
    ):
        if self.candidate:
            return self.candidate, False
        self.candidate = _record(file_id=file_id, kb_id=kb_id, **data)
        self.candidate.replacement_target_file_id = target_file_id
        self.candidate.is_active = False
        for key, value in (target_restore_data or {}).items():
            setattr(self.record, key, value)
        return self.candidate, True
class _FakeQAService:
    def __init__(self):
        self.rebase_calls = []
    async def mark_file_qas_outdated(self, *, kb_id, file_id):
        del kb_id, file_id
        return 0
    async def rebase_draft_qas(self, *, kb_id, file_id, operator_id=None):
        self.rebase_calls.append((kb_id, file_id, operator_id))
        return 0
def _service(monkeypatch, record):
    repository = _FakeRepository(record)
    qa_service = _FakeQAService()
    service = DocumentCleaningService(
        file_repository=repository,
        cleaner=OptionalAIDocumentCleaner(),
        qa_service=qa_service,
    )
    saved = {}
    async def read_markdown(_path):
        return "# 标题\n\n正文  内容"
    async def save_draft(_kb_id, _file_id, version, content):
        path = f"minio://parsed/{_file_id}/draft-{version}.md"
        saved[path] = content
        return path
    async def delete_draft(path):
        saved.pop(path, None)
    monkeypatch.setattr(service, "_read_markdown", read_markdown)
    monkeypatch.setattr(service, "_save_draft", save_draft)
    monkeypatch.setattr(service, "_delete_draft", delete_draft)
    return service, repository, saved, qa_service
def test_auto_confirm_uses_explicit_request_then_backend_default(monkeypatch):
    monkeypatch.setattr(cleaning_service_module.config, "document_cleaning_auto_confirm", False)
    assert DocumentCleaningService.resolve_auto_confirm({"auto_confirm": True}) is True
    assert DocumentCleaningService.resolve_auto_confirm({"auto_confirm": False}) is False
    assert DocumentCleaningService.resolve_auto_confirm({"auto_index": False}) is False
    assert DocumentCleaningService.resolve_auto_confirm({}) is False
@pytest.mark.asyncio
async def test_manual_cleaning_waits_without_creating_chunks_or_indexing(monkeypatch):
    record = _record()
    service, _repository, _saved, _qa_service = _service(monkeypatch, record)
    index_calls = []
    async def index_file(*args, **kwargs):
        index_calls.append((args, kwargs))
    monkeypatch.setattr(cleaning_service_module.knowledge_base, "index_file", index_file)
    result = await service.generate_draft(
        kb_id="kb-1",
        file_id="file-1",
        operator_id="user-1",
        auto_confirm=False,
    )
    assert result["status"] == FileStatus.WAITING_CONFIRMATION
    assert record.chunk_count == 0
    assert index_calls == []
    assert record.cleaning_draft_file
@pytest.mark.asyncio
async def test_save_draft_uses_optimistic_version_and_does_not_index(monkeypatch):
    record = _record(
        status=FileStatus.WAITING_CONFIRMATION,
        cleaning_version=2,
        cleaning_draft_file="minio://parsed/old-draft.md",
    )
    service, _repository, saved, _qa_service = _service(monkeypatch, record)
    result = await service.save_draft(
        kb_id="kb-1",
        file_id="file-1",
        operator_id="user-2",
        expected_version=2,
        content="人工编辑后的唯一标记",
    )
    assert result["cleaning_version"] == 3
    assert record.status == FileStatus.WAITING_CONFIRMATION
    assert any("人工编辑后的唯一标记" in value for value in saved.values())
@pytest.mark.asyncio
async def test_stale_editor_is_rejected_without_overwriting_current_draft(monkeypatch):
    record = _record(status=FileStatus.WAITING_CONFIRMATION, cleaning_version=3)
    service, repository, _saved, _qa_service = _service(monkeypatch, record)
    repository.reject_next_version_update = True
    with pytest.raises(CleaningVersionConflict):
        await service.save_draft(
            kb_id="kb-1",
            file_id="file-1",
            operator_id="user-2",
            expected_version=3,
            content="过期编辑",
        )
    assert record.cleaning_version == 3
@pytest.mark.asyncio
async def test_confirm_initial_document_indexes_the_saved_draft(monkeypatch):
    record = _record(
        status=FileStatus.WAITING_CONFIRMATION,
        cleaning_version=1,
        cleaning_draft_file="minio://parsed/draft.md",
    )
    service, _repository, _saved, _qa_service = _service(monkeypatch, record)
    calls = []
    async def index_file(kb_id, file_id, operator_id=None, params=None):
        calls.append((kb_id, file_id, operator_id, params))
        record.status = FileStatus.INDEXED
        return {"status": FileStatus.INDEXED}
    monkeypatch.setattr(cleaning_service_module.knowledge_base, "index_file", index_file)
    result = await service.confirm(
        kb_id="kb-1",
        file_id="file-1",
        operator_id="user-1",
        expected_version=1,
    )
    assert result["status"] == FileStatus.INDEXED
    assert record.markdown_file == "minio://parsed/draft.md"
    assert calls == [("kb-1", "file-1", "user-1", {})]
@pytest.mark.asyncio
async def test_confirm_rebinds_draft_qa_after_indexing(monkeypatch):
    record = _record(
        status=FileStatus.WAITING_CONFIRMATION,
        cleaning_version=1,
        cleaning_draft_file="minio://parsed/draft.md",
    )
    service, _repository, _saved, qa_service = _service(monkeypatch, record)
    async def index_file(_kb_id, file_id, operator_id=None, params=None):
        del file_id, operator_id, params
        record.status = FileStatus.INDEXED
        return {"status": FileStatus.INDEXED}
    monkeypatch.setattr(cleaning_service_module.knowledge_base, "index_file", index_file)
    result = await service.confirm(
        kb_id="kb-1",
        file_id="file-1",
        operator_id="user-1",
        expected_version=1,
    )
    assert result["status"] == FileStatus.INDEXED
    assert qa_service.rebase_calls == [("kb-1", "file-1", "user-1")]
@pytest.mark.asyncio
async def test_concurrent_initial_confirmation_only_indexes_once(monkeypatch):
    record = _record(
        status=FileStatus.WAITING_CONFIRMATION,
        cleaning_version=1,
        cleaning_draft_file="minio://parsed/draft.md",
    )
    service, _repository, _saved, _qa_service = _service(monkeypatch, record)
    index_calls = []
    release_index = asyncio.Event()
    async def index_file(kb_id, file_id, operator_id=None, params=None):
        index_calls.append((kb_id, file_id, operator_id, params))
        await release_index.wait()
        record.status = FileStatus.INDEXED
        return {"status": FileStatus.INDEXED}
    monkeypatch.setattr(cleaning_service_module.knowledge_base, "index_file", index_file)
    first = asyncio.create_task(
        service.confirm(
            kb_id="kb-1",
            file_id="file-1",
            operator_id="user-1",
            expected_version=1,
        )
    )
    await asyncio.sleep(0)
    second = asyncio.create_task(
        service.confirm(
            kb_id="kb-1",
            file_id="file-1",
            operator_id="user-2",
            expected_version=1,
        )
    )
    await asyncio.sleep(0)
    release_index.set()
    first_result, second_result = await asyncio.gather(first, second)
    assert index_calls == [("kb-1", "file-1", "user-1", {})]
    assert first_result["status"] == FileStatus.INDEXED
    assert second_result == {
        "file_id": "file-1",
        "status": FileStatus.CONFIRMED,
        "idempotent": True,
    }
@pytest.mark.asyncio
async def test_repeated_confirmation_is_idempotent(monkeypatch):
    record = _record(
        status=FileStatus.INDEXED,
        cleaning_version=1,
        cleaning_draft_file="minio://parsed/draft.md",
        confirmed_at=datetime(2026, 7, 25),
    )
    service, _repository, _saved, _qa_service = _service(monkeypatch, record)
    result = await service.confirm(
        kb_id="kb-1",
        file_id="file-1",
        operator_id="user-1",
        expected_version=1,
    )
    assert result == {"file_id": "file-1", "status": FileStatus.INDEXED, "idempotent": True}
@pytest.mark.asyncio
async def test_confirming_edit_of_indexed_document_creates_inactive_replacement(monkeypatch):
    record = _record(
        status=FileStatus.WAITING_CONFIRMATION,
        cleaning_version=2,
        cleaning_draft_file="minio://parsed/revised.md",
        chunk_count=2,
        token_count=20,
    )
    service, repository, _saved, _qa_service = _service(monkeypatch, record)
    indexed_file_ids = []
    async def index_file(_kb_id, file_id, operator_id=None, params=None):
        del operator_id, params
        indexed_file_ids.append(file_id)
        return {"status": FileStatus.INDEXED}
    monkeypatch.setattr(cleaning_service_module.knowledge_base, "index_file", index_file)
    result = await service.confirm(
        kb_id="kb-1",
        file_id="file-1",
        operator_id="user-1",
        expected_version=2,
    )
    assert repository.candidate is not None
    assert repository.candidate.is_active is False
    assert repository.candidate.replacement_target_file_id == "file-1"
    assert repository.candidate.markdown_file.startswith(f"minio://parsed/{repository.candidate.file_id}/")
    assert record.status == FileStatus.INDEXED
    assert record.cleaning_draft_file == record.markdown_file
    assert indexed_file_ids == [repository.candidate.file_id]
    assert result["previous_file_id"] == "file-1"
@pytest.mark.asyncio
async def test_cross_knowledge_base_file_id_is_not_readable(monkeypatch):
    service, _repository, _saved, _qa_service = _service(monkeypatch, _record(kb_id="kb-private"))
    with pytest.raises(DocumentCleaningError, match="文档不存在"):
        await service.get_preview(kb_id="kb-other", file_id="file-1")
