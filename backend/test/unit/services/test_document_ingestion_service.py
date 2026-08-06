from __future__ import annotations
import asyncio
import hashlib
from datetime import timedelta
from types import SimpleNamespace
import pytest
from yuxi.knowledge.utils.kb_utils import sanitize_processing_error, sanitize_processing_params
from yuxi.repositories.knowledge_file_repository import DocumentCreateOutcome
from yuxi.services import document_ingestion_service as ingestion_module
from yuxi.services.document_ingestion_service import (
    DocumentIngestionService,
    DuplicateConflictError,
    DuplicateStrategyError,
    InvalidReplacementTargetError,
    ReplacementCleanupInvariantError,
    ReplacementInProgressError,
)
from yuxi.utils.datetime_utils import utc_now_naive
pytestmark = pytest.mark.asyncio
async def test_transport_duplicate_fields_are_not_persisted_as_processing_params():
    sanitized = sanitize_processing_params(
        {
            "content_hash": "forged",
            "file_size": 999,
            "duplicate_strategy": "replace",
            "replace_file_id": "other-file",
            "source_path": "demo.txt",
            "chunk_preset_id": "default",
        }
    )
    assert sanitized == {"chunk_preset_id": "default"}
    safe_error = sanitize_processing_error("token=secret-value at C:\\private\\failure.log")
    assert "secret-value" not in safe_error
    assert "private" not in safe_error
def make_record(
    file_id: str,
    *,
    kb_id: str = "kb_1",
    filename: str = "demo.txt",
    content_hash: str = "hash_1",
    is_active: bool = True,
    status: str = "indexed",
    parent_id: str | None = None,
):
    return SimpleNamespace(
        file_id=file_id,
        kb_id=kb_id,
        filename=filename,
        content_hash=content_hash,
        file_size=4,
        is_active=is_active,
        status=status,
        created_at=None,
        parent_id=parent_id,
    )
class ConflictRepository:
    def __init__(self, records=()):
        self.records = list(records)
    async def list_by_content_hash(self, *, kb_id: str, content_hash: str):
        return [record for record in self.records if record.kb_id == kb_id and record.content_hash == content_hash]
    async def validate_parent_folder(self, *, kb_id: str, parent_id: str | None):
        return None
    async def build_document_display_paths(self, records):
        return {record.file_id: record.filename for record in records}
    async def list_same_name_files(self, *, kb_id: str, parent_id: str | None, filename: str):
        return [
            record
            for record in self.records
            if record.kb_id == kb_id
            and record.parent_id == parent_id
            and record.is_active
            and record.filename.casefold() == filename.casefold()
        ]
    async def list_pending_replacement_candidates(self, *, kb_id: str, replacement_target_file_id: str):
        return []
async def test_exact_content_conflict_only_allows_skip_and_does_not_leak_other_kb():
    same_kb = make_record("file_same")
    other_kb = make_record("file_other", kb_id="kb_other")
    service = DocumentIngestionService(file_repository=ConflictRepository([same_kb, other_kb]))
    with pytest.raises(DuplicateConflictError) as exc_info:
        await service.check_upload_conflict(
            kb_id="kb_1",
            filename="new-name.txt",
            content_hash="hash_1",
            file_size=4,
            duplicate_strategy="prompt",
        )
    detail = exc_info.value.detail
    assert detail["conflict_type"] == "exact_content"
    assert detail["allowed_strategies"] == ["skip"]
    assert detail["incoming"]["content_hash"] == "hash_1"
    assert [item["file_id"] for item in detail["conflicts"]] == ["file_same"]
    assert detail["conflicts"][0]["content_hash"] == "hash_1"
    assert all("path" not in item and "minio_url" not in item for item in detail["conflicts"])
    skipped = await service.check_upload_conflict(
        kb_id="kb_1",
        filename="new-name.txt",
        content_hash="hash_1",
        file_size=4,
        duplicate_strategy="skip",
    )
    assert skipped.action == "skipped"
    assert skipped.existing_file_id == "file_same"
    with pytest.raises(DuplicateStrategyError, match="only supports the skip"):
        await service.check_upload_conflict(
            kb_id="kb_1",
            filename="new-name.txt",
            content_hash="hash_1",
            file_size=4,
            duplicate_strategy="keep_both",
        )
async def test_same_name_is_case_insensitive_and_supports_three_confirmed_strategies():
    existing = make_record("file_1", filename="Report.PDF", content_hash="old")
    service = DocumentIngestionService(file_repository=ConflictRepository([existing]))
    with pytest.raises(DuplicateConflictError) as exc_info:
        await service.check_upload_conflict(
            kb_id="kb_1",
            filename="report.pdf",
            content_hash="new",
            file_size=8,
            duplicate_strategy="prompt",
        )
    assert exc_info.value.detail["conflict_type"] == "same_name"
    assert exc_info.value.detail["allowed_strategies"] == ["skip", "replace", "keep_both"]
    assert exc_info.value.detail["incoming"]["content_hash"] == "new"
    assert exc_info.value.detail["conflicts"][0]["content_hash"] == "old"
    skipped = await service.check_upload_conflict(
        kb_id="kb_1",
        filename="report.pdf",
        content_hash="new",
        file_size=8,
        duplicate_strategy="skip",
    )
    assert skipped.existing_file_id == "file_1"
    kept = await service.check_upload_conflict(
        kb_id="kb_1",
        filename="report.pdf",
        content_hash="new",
        file_size=8,
        duplicate_strategy="keep_both",
    )
    assert kept.action == "upload"
    replaced = await service.check_upload_conflict(
        kb_id="kb_1",
        filename="report.pdf",
        content_hash="new",
        file_size=8,
        duplicate_strategy="replace",
        replace_file_id="file_1",
    )
    assert replaced.action == "upload"
    with pytest.raises(InvalidReplacementTargetError) as exc_info:
        await service.check_upload_conflict(
            kb_id="kb_1",
            filename="report.pdf",
            content_hash="new",
            file_size=8,
            duplicate_strategy="replace",
            replace_file_id="file_from_other_kb",
        )
    assert exc_info.value.detail["code"] == "invalid_replacement_target"
async def test_same_name_conflict_is_scoped_to_parent_folder_but_exact_content_is_global():
    first_folder = make_record("file_1", filename="产品说明.txt", content_hash="old", parent_id="folder_1")
    service = DocumentIngestionService(file_repository=ConflictRepository([first_folder]))
    different_folder = await service.check_upload_conflict(
        kb_id="kb_1",
        parent_id="folder_2",
        filename="产品说明.txt",
        content_hash="new",
        file_size=8,
        duplicate_strategy="prompt",
    )
    assert different_folder.action == "upload"
    with pytest.raises(DuplicateConflictError) as same_folder:
        await service.check_upload_conflict(
            kb_id="kb_1",
            parent_id="folder_1",
            filename="产品说明.txt",
            content_hash="new",
            file_size=8,
            duplicate_strategy="prompt",
        )
    assert same_folder.value.detail["conflict_type"] == "same_name"
    with pytest.raises(DuplicateConflictError) as exact_content:
        await service.check_upload_conflict(
            kb_id="kb_1",
            parent_id="folder_2",
            filename="另一名称.txt",
            content_hash="old",
            file_size=8,
            duplicate_strategy="prompt",
        )
    assert exact_content.value.detail["conflict_type"] == "exact_content"
async def test_replace_reports_existing_in_progress_candidate_without_storage_details():
    target = make_record("old", filename="report.pdf", content_hash="old")
    candidate = make_record(
        "candidate",
        filename="report.pdf",
        content_hash="candidate",
        is_active=False,
        status="indexing",
    )
    class PendingRepository(ConflictRepository):
        async def list_pending_replacement_candidates(self, *, kb_id, replacement_target_file_id):
            assert (kb_id, replacement_target_file_id) == ("kb_1", "old")
            return [candidate]
    service = DocumentIngestionService(file_repository=PendingRepository([target, candidate]))
    with pytest.raises(ReplacementInProgressError) as exc_info:
        await service.check_upload_conflict(
            kb_id="kb_1",
            filename="REPORT.PDF",
            content_hash="new",
            file_size=8,
            duplicate_strategy="replace",
            replace_file_id="old",
        )
    assert exc_info.value.detail == {
        "code": "replacement_in_progress",
        "message": "该文档已有正在处理的替换版本",
        "target_file_id": "old",
        "candidate_file_id": "candidate",
    }
    assert "path" not in str(exc_info.value.detail)
async def test_second_stage_hashes_server_object_and_ignores_client_hash(monkeypatch):
    captured = {}
    stored_bytes = b"trusted server object"
    existing = make_record("file_existing", content_hash=hashlib.sha256(stored_bytes).hexdigest())
    class CaptureRepository:
        async def create_document_with_duplicate_guard(self, **kwargs):
            captured.update(kwargs)
            return DocumentCreateOutcome(
                action="conflict",
                conflicts=(existing,),
                conflict_type="exact_content",
            )
        async def build_document_display_paths(self, records):
            return {record.file_id: record.filename for record in records}
    class FakeMinio:
        public_endpoint = "localhost:9000"
        async def adownload_file(self, bucket_name, object_name):
            assert (bucket_name, object_name) == ("knowledgebases", "kb_1/upload/demo_1234567890123.txt")
            return stored_bytes
        async def adelete_file(self, bucket_name, object_name):
            return None
    class FakeKnowledgeBaseRepository:
        async def get_by_kb_id(self, kb_id):
            return SimpleNamespace(additional_params={})
    async def fake_prepare_item_metadata(item, content_type, kb_id, params):
        return {
            "file_id": "file_new",
            "filename": params["source_path"],
            "parent_id": None,
            "file_type": "txt",
            "processing_params": {},
        }
    fake_minio = FakeMinio()
    monkeypatch.setattr(ingestion_module, "get_minio_client", lambda: fake_minio)
    monkeypatch.setattr(ingestion_module, "KnowledgeBaseRepository", FakeKnowledgeBaseRepository)
    monkeypatch.setattr(ingestion_module, "prepare_item_metadata", fake_prepare_item_metadata)
    service = DocumentIngestionService(file_repository=CaptureRepository())
    with pytest.raises(DuplicateConflictError):
        await service.create_uploaded_document(
            kb_id="kb_1",
            item="http://localhost:9000/knowledgebases/kb_1/upload/demo_1234567890123.txt",
            params={
                "duplicate_strategy": "prompt",
                "content_hash": "forged",
                "content_hashes": {"ignored": "forged"},
                "file_sizes": {"ignored": 999999},
                "source_path": "folder/Demo.txt",
            },
            operator_id="user_1",
        )
    data = captured["data"]
    assert data["content_hash"] == hashlib.sha256(stored_bytes).hexdigest()
    assert data["content_hash"] != "forged"
    assert data["file_size"] == len(stored_bytes)
    assert data["filename"] == "folder/Demo.txt"
async def test_second_stage_revalidates_server_object_signature_and_removes_invalid_stage(monkeypatch):
    deleted = []
    class NeverCalledRepository:
        async def exists_by_storage_path(self, **_kwargs):
            return False
        async def create_document_with_duplicate_guard(self, **_kwargs):
            raise AssertionError("invalid staged object must not create a document")
    class FakeMinio:
        public_endpoint = "localhost:9000"
        async def adownload_file(self, _bucket_name, _object_name):
            return b"not a real PDF"
        async def adelete_file(self, bucket_name, object_name):
            deleted.append((bucket_name, object_name))
    fake_minio = FakeMinio()
    monkeypatch.setattr(ingestion_module, "get_minio_client", lambda: fake_minio)
    service = DocumentIngestionService(file_repository=NeverCalledRepository())
    with pytest.raises(ValueError, match="PDF 文件签名"):
        await service.create_uploaded_document(
            kb_id="kb_1",
            item="http://localhost:9000/knowledgebases/kb_1/upload/demo_1234567890123.pdf",
            params={},
            operator_id="user_1",
        )
    assert deleted == [
        ("knowledgebases", "kb_1/upload/demo_1234567890123.pdf"),
    ]
async def test_preprocessed_metadata_cannot_bypass_server_object_hash(monkeypatch):
    class FakeMinio:
        public_endpoint = "localhost:9000"
        async def adownload_file(self, _bucket_name, _object_name):
            return b"server content"
        async def adelete_file(self, _bucket_name, _object_name):
            return None
    monkeypatch.setattr(ingestion_module, "get_minio_client", lambda: FakeMinio())
    service = DocumentIngestionService(file_repository=ConflictRepository())
    item = "http://localhost:9000/knowledgebases/kb_1/upload/client-forged.html"
    with pytest.raises(ValueError, match="server content hash"):
        await service.create_uploaded_document(
            kb_id="kb_1",
            item=item,
            params={
                "_preprocessed_map": {
                    item: {
                        "path": item,
                        "content_hash": "client-forged",
                        "filename": "https://example.com",
                        "file_size": 1,
                    }
                }
            },
            operator_id="user_1",
        )
async def test_second_stage_rejects_upload_url_from_unconfigured_host(monkeypatch):
    class FakeMinio:
        public_endpoint = "localhost:9000"
        async def adownload_file(self, _bucket_name, _object_name):
            raise AssertionError("untrusted URL must be rejected before reading MinIO")
    monkeypatch.setattr(ingestion_module, "get_minio_client", lambda: FakeMinio())
    with pytest.raises(ValueError, match="upload URL host") as exc_info:
        await DocumentIngestionService(file_repository=ConflictRepository()).create_uploaded_document(
            kb_id="kb_1",
            item="https://attacker.example/knowledgebases/kb_1/upload/demo_1234567890123.txt",
            params={},
            operator_id="user_1",
        )
    assert "attacker.example" not in str(exc_info.value)
async def test_staged_cleanup_never_deletes_an_object_claimed_by_a_document(monkeypatch):
    class ClaimedRepository:
        async def exists_by_storage_path(self, *, kb_id, storage_path):
            assert kb_id == "kb_1"
            assert storage_path.endswith("/demo.txt")
            return True
    class FailIfDeletedMinio:
        async def adelete_file(self, _bucket_name, _object_name):
            raise AssertionError("claimed object must not be deleted")
    monkeypatch.setattr(ingestion_module, "get_minio_client", lambda: FailIfDeletedMinio())
    cleaned = await DocumentIngestionService(file_repository=ClaimedRepository())._delete_staged_object_if_unclaimed(
        "kb_1",
        "minio://knowledgebases/kb_1/upload/demo.txt",
        "knowledgebases",
        "kb_1/upload/demo.txt",
    )
    assert cleaned is True
async def test_staged_cleanup_retries_three_times_without_masking_failure(monkeypatch):
    class UnclaimedRepository:
        async def exists_by_storage_path(self, **_kwargs):
            return False
    class FailingMinio:
        def __init__(self):
            self.calls = 0
        async def adelete_file(self, _bucket_name, _object_name):
            self.calls += 1
            raise RuntimeError("temporary storage failure")
    async def no_sleep(_seconds):
        return None
    minio = FailingMinio()
    monkeypatch.setattr(ingestion_module, "get_minio_client", lambda: minio)
    monkeypatch.setattr(ingestion_module.asyncio, "sleep", no_sleep)
    cleaned = await DocumentIngestionService(file_repository=UnclaimedRepository())._delete_staged_object_if_unclaimed(
        "kb_1",
        "minio://knowledgebases/kb_1/upload/demo.txt",
        "knowledgebases",
        "kb_1/upload/demo.txt",
    )
    assert cleaned is False
    assert minio.calls == 3
async def test_cleanup_failure_is_retryable_and_success_is_idempotent(monkeypatch):
    new_record = SimpleNamespace(
        file_id="new",
        kb_id="kb_1",
        status="indexed",
        is_active=True,
        previous_version_id="old",
        replacement_target_file_id="old",
        processing_task_id="task_1",
    )
    old_record = SimpleNamespace(file_id="old", kb_id="kb_1", is_active=False)
    class CleanupRepository:
        def __init__(self):
            self.records = {"new": new_record, "old": old_record}
        async def get_by_file_id(self, file_id):
            return self.records.get(file_id)
        async def update_fields(self, *, file_id, kb_id, data):
            record = self.records[file_id]
            for key, value in data.items():
                setattr(record, key, value)
            return record
    class FakeKb:
        databases_meta = {"kb_1": {}}
        def __init__(self):
            self.calls = 0
        async def delete_file_vectors_strict(self, kb_id, file_id):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("temporary milvus failure at C:\\private\\vector.db")
    fake_kb = FakeKb()
    async def fake_get_kb(kb_id):
        return fake_kb
    monkeypatch.setattr(ingestion_module.knowledge_base, "aget_kb", fake_get_kb)
    service = DocumentIngestionService(file_repository=CleanupRepository())
    with pytest.raises(RuntimeError, match="temporary milvus"):
        await service.cleanup_replaced_version(
            kb_id="kb_1",
            new_file_id="new",
            old_file_id="old",
            task_id="task_1",
        )
    await service.mark_cleanup_failure(
        kb_id="kb_1",
        new_file_id="new",
        error=RuntimeError("cleanup failed at kb_1/upload/private.txt"),
    )
    assert new_record.is_active is True
    assert new_record.status == "error_replacement_cleanup"
    assert "upload/private.txt" not in new_record.error_message
    await service.cleanup_replaced_version(
        kb_id="kb_1",
        new_file_id="new",
        old_file_id="old",
        task_id="task_1",
    )
    assert new_record.status == "indexed"
    assert new_record.processing_task_id is None
    assert fake_kb.calls == 2
    await service.cleanup_replaced_version(
        kb_id="kb_1",
        new_file_id="new",
        old_file_id="old",
        task_id="task_1",
    )
    assert fake_kb.calls == 2
async def test_worker_recovery_does_not_exceed_retry_budget_for_terminal_failure(monkeypatch):
    failed = pending_cleanup_record(
        file_id="new_failed",
        task_id="replacement-cleanup:new_failed:1",
        lease_expires_at=None,
        status="error_replacement_cleanup",
    )
    repository = LeaseAwareRecoveryRepository([failed])
    queue = StatefulQueue()
    monkeypatch.setattr(ingestion_module, "get_arq_job_status", lambda *_args: _async_value("not_found"))
    recovered = await DocumentIngestionService(file_repository=repository).recover_pending_replacement_cleanups(
        queue=queue
    )
    assert recovered == 0
    assert queue.enqueued == []
async def test_cleanup_worker_retries_transient_errors_but_not_invariant_errors(monkeypatch):
    class FailingService:
        def __init__(self, error):
            self.error = error
            self.marked = []
            self.retried = []
        async def cleanup_replaced_version(self, **_kwargs):
            raise self.error
        async def mark_cleanup_failure(self, **kwargs):
            self.marked.append(kwargs)
        async def mark_cleanup_retry(self, **kwargs):
            self.retried.append(kwargs)
    transient_service = FailingService(RuntimeError("temporary storage failure"))
    monkeypatch.setattr(ingestion_module, "DocumentIngestionService", lambda: transient_service)
    with pytest.raises(ingestion_module.Retry):
        await ingestion_module.process_document_replacement_cleanup(
            {"job_try": 1},
            "kb_1",
            "new",
            "old",
            "task_1",
        )
    assert len(transient_service.retried) == 1
    assert transient_service.marked == []
    with pytest.raises(RuntimeError, match="temporary storage failure"):
        await ingestion_module.process_document_replacement_cleanup(
            {"job_try": 4},
            "kb_1",
            "new",
            "old",
            "task_1",
        )
    assert len(transient_service.marked) == 1
    invariant_service = FailingService(ReplacementCleanupInvariantError("invalid version relationship"))
    monkeypatch.setattr(ingestion_module, "DocumentIngestionService", lambda: invariant_service)
    with pytest.raises(ReplacementCleanupInvariantError):
        await ingestion_module.process_document_replacement_cleanup(
            {"job_try": 1},
            "kb_1",
            "new",
            "old",
            "task_2",
        )
    assert len(invariant_service.marked) == 1
    assert invariant_service.retried == []
async def test_replacement_cleanup_preserves_historical_chunks_and_artifacts(monkeypatch):
    chunks = {"chunk-old": "historical content"}
    artifacts = {"original": True, "markdown": True, "preview": True}
    vectors = {"old"}
    new_record = SimpleNamespace(
        file_id="new",
        kb_id="kb_1",
        status="indexed",
        is_active=True,
        previous_version_id="old",
        replacement_target_file_id="old",
        processing_task_id="replacement-cleanup:new:1",
        processing_task_lease_expires_at=utc_now_naive() + timedelta(minutes=30),
    )
    old_record = SimpleNamespace(
        file_id="old",
        kb_id="kb_1",
        status="indexed",
        is_active=False,
        previous_version_id=None,
        replacement_target_file_id=None,
        processing_task_id=None,
        markdown_file="minio://knowledgebases/kb_1/parsed/old.md",
        chunk_count=1,
        token_count=3,
        superseded_at=utc_now_naive(),
    )
    class CleanupRepository:
        def __init__(self):
            self.records = {"new": new_record, "old": old_record}
        async def get_by_file_id(self, file_id):
            return self.records.get(file_id)
        async def update_fields(self, *, file_id, kb_id, data):
            record = self.records[file_id]
            assert record.kb_id == kb_id
            for key, value in data.items():
                setattr(record, key, value)
            return record
    class FakeKb:
        databases_meta = {"kb_1": {}}
        async def delete_file_vectors_strict(self, kb_id, file_id):
            assert kb_id == "kb_1"
            vectors.discard(file_id)
    async def fake_get_kb(kb_id):
        assert kb_id == "kb_1"
        return FakeKb()
    monkeypatch.setattr(ingestion_module.knowledge_base, "aget_kb", fake_get_kb)
    monkeypatch.setattr(
        ingestion_module,
        "get_minio_client",
        lambda: (_ for _ in ()).throw(AssertionError("replacement cleanup must preserve stored artifacts")),
    )
    service = DocumentIngestionService(file_repository=CleanupRepository())
    task_id = new_record.processing_task_id
    await service.cleanup_replaced_version(
        kb_id="kb_1",
        new_file_id="new",
        old_file_id="old",
        task_id=task_id,
    )
    await service.cleanup_replaced_version(
        kb_id="kb_1",
        new_file_id="new",
        old_file_id="old",
        task_id=task_id,
    )
    assert vectors == set()
    assert chunks["chunk-old"] == "historical content"
    assert artifacts == {"original": True, "markdown": True, "preview": True}
    assert old_record.markdown_file == "minio://knowledgebases/kb_1/parsed/old.md"
    assert old_record.chunk_count == 1
    assert old_record.token_count == 3
    assert old_record.is_active is False
    assert old_record.superseded_at is not None
class LeaseAwareRecoveryRepository:
    def __init__(self, records):
        self.records = {record.file_id: record for record in records}
        self.claims = []
        self._lock = asyncio.Lock()
    async def list_pending_replacement_cleanup(self):
        return list(self.records.values())
    async def get_by_file_id(self, file_id):
        return self.records.get(file_id)
    async def claim_replacement_cleanup(
        self,
        *,
        kb_id,
        file_id,
        expected_task_id,
        expected_lease_expires_at,
        task_id,
        task_updated_at,
        lease_expires_at,
        reset_attempt,
    ):
        async with self._lock:
            record = self.records[file_id]
            if (
                record.kb_id != kb_id
                or record.processing_task_id != expected_task_id
                or record.processing_task_lease_expires_at != expected_lease_expires_at
            ):
                return None
            record.processing_task_id = task_id
            record.processing_task_updated_at = task_updated_at
            record.processing_task_lease_expires_at = lease_expires_at
            if reset_attempt:
                record.processing_task_attempt = 0
            record.processing_stage = "replacement_cleanup"
            record.status = "indexed"
            record.error_message = None
            self.claims.append(task_id)
            return record
    async def update_fields(self, *, file_id, kb_id, data):
        record = self.records[file_id]
        assert record.kb_id == kb_id
        for key, value in data.items():
            setattr(record, key, value)
        return record
class StatefulQueue:
    def __init__(self):
        self.enqueued = []
        self.fail = False
    async def enqueue_job(self, *args, **kwargs):
        if self.fail:
            raise RuntimeError("redis unavailable at redis://private-host")
        self.enqueued.append((args, kwargs))
        return SimpleNamespace(job_id=kwargs["_job_id"])
def pending_cleanup_record(
    *,
    file_id="new",
    task_id="auto",
    lease_expires_at=None,
    task_updated_at=None,
    task_attempt=0,
    status="indexed",
):
    if task_id == "auto":
        task_id = DocumentIngestionService._next_cleanup_task_id(file_id, None)
    return SimpleNamespace(
        file_id=file_id,
        kb_id="kb_1",
        status=status,
        is_active=True,
        previous_version_id=f"old-{file_id}",
        replacement_target_file_id=f"old-{file_id}",
        processing_stage="replacement_cleanup",
        processing_progress=95,
        processing_task_id=task_id,
        processing_task_attempt=task_attempt,
        processing_task_updated_at=(
            task_updated_at
            if task_updated_at is not None
            else (utc_now_naive() - timedelta(minutes=10) if task_id else None)
        ),
        processing_task_lease_expires_at=lease_expires_at,
        error_message=None,
    )
async def _async_value(value):
    return value
async def test_replacement_cleanup_job_id_is_deterministic_and_fits_schema():
    first = DocumentIngestionService._next_cleanup_task_id("file-" + "x" * 80, None)
    repeated = DocumentIngestionService._next_cleanup_task_id("file-" + "x" * 80, None)
    reclaimed = DocumentIngestionService._next_cleanup_task_id("file-" + "x" * 80, first)
    assert first == repeated
    assert reclaimed != first
    assert len(first) <= 64
    assert len(reclaimed) <= 64
async def test_recovery_does_not_duplicate_a_live_arq_job(monkeypatch):
    record = pending_cleanup_record(lease_expires_at=utc_now_naive() + timedelta(minutes=30))
    repository = LeaseAwareRecoveryRepository([record])
    queue = StatefulQueue()
    async def queued_status(_queue, task_id):
        assert task_id == record.processing_task_id
        return "queued"
    monkeypatch.setattr(ingestion_module, "get_arq_job_status", queued_status)
    recovered = await DocumentIngestionService(file_repository=repository).recover_pending_replacement_cleanups(
        queue=queue
    )
    assert recovered == 0
    assert queue.enqueued == []
    assert repository.claims == []
async def test_recovery_reclaims_a_missing_arq_job(monkeypatch):
    record = pending_cleanup_record(
        lease_expires_at=utc_now_naive() + timedelta(minutes=30),
        task_attempt=1,
    )
    repository = LeaseAwareRecoveryRepository([record])
    queue = StatefulQueue()
    monkeypatch.setattr(ingestion_module, "get_arq_job_status", lambda *_args: _async_value("not_found"))
    recovered = await DocumentIngestionService(file_repository=repository).recover_pending_replacement_cleanups(
        queue=queue
    )
    assert recovered == 1
    expected_task_id = DocumentIngestionService._next_cleanup_task_id("new", None)
    assert record.processing_task_id == expected_task_id
    assert queue.enqueued[0][1]["_job_id"] == expected_task_id
    assert queue.enqueued[0][1]["_job_try"] == 2
async def test_recovery_reclaims_an_expired_in_progress_job(monkeypatch):
    record = pending_cleanup_record(lease_expires_at=utc_now_naive() - timedelta(seconds=1))
    repository = LeaseAwareRecoveryRepository([record])
    queue = StatefulQueue()
    monkeypatch.setattr(ingestion_module, "get_arq_job_status", lambda *_args: _async_value("in_progress"))
    recovered = await DocumentIngestionService(file_repository=repository).recover_pending_replacement_cleanups(
        queue=queue
    )
    assert recovered == 1
    assert record.processing_task_id == DocumentIngestionService._next_cleanup_task_id(
        "new",
        DocumentIngestionService._next_cleanup_task_id("new", None),
    )
async def test_concurrent_recovery_claims_and_enqueues_once(monkeypatch):
    record = pending_cleanup_record(lease_expires_at=utc_now_naive() - timedelta(seconds=1))
    repository = LeaseAwareRecoveryRepository([record])
    queue = StatefulQueue()
    monkeypatch.setattr(ingestion_module, "get_arq_job_status", lambda *_args: _async_value("not_found"))
    services = [DocumentIngestionService(file_repository=repository) for _ in range(2)]
    results = await asyncio.gather(*(service.recover_pending_replacement_cleanups(queue=queue) for service in services))
    assert sum(results) == 1
    assert len(repository.claims) == 1
    assert len(queue.enqueued) == 1
async def test_enqueue_failure_is_persisted_and_sanitized(monkeypatch):
    record = pending_cleanup_record(task_id=None, lease_expires_at=None)
    repository = LeaseAwareRecoveryRepository([record])
    queue = StatefulQueue()
    queue.fail = True
    monkeypatch.setattr(ingestion_module, "get_arq_pool", lambda: _async_value(queue))
    with pytest.raises(RuntimeError, match="redis unavailable"):
        await DocumentIngestionService(file_repository=repository).enqueue_replacement_cleanup(
            kb_id="kb_1",
            new_file_id="new",
            old_file_id="old-new",
        )
    assert record.status == "error_replacement_cleanup"
    assert "private-host" not in record.error_message
    assert record.processing_task_lease_expires_at is None
async def test_completed_arq_job_is_not_submitted_again(monkeypatch):
    record = pending_cleanup_record(lease_expires_at=utc_now_naive() + timedelta(minutes=30))
    repository = LeaseAwareRecoveryRepository([record])
    queue = StatefulQueue()
    monkeypatch.setattr(ingestion_module, "get_arq_job_status", lambda *_args: _async_value("complete"))
    recovered = await DocumentIngestionService(file_repository=repository).recover_pending_replacement_cleanups(
        queue=queue
    )
    assert recovered == 0
    assert queue.enqueued == []
