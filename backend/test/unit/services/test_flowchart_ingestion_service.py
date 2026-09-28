from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import fitz
import pytest

from yuxi.knowledge.flowchart_analysis import FlowchartAnalysisError
from yuxi.knowledge.base import FileStatus
from yuxi.knowledge.flowchart import FLOWCHART_INGESTION_TYPE, flowchart_revision, is_flowchart
from yuxi.knowledge.flowchart_analysis import FlowchartAnalysisResult, PageFlowchartAnalysis
from yuxi.repositories.knowledge_file_repository import (
    FlowchartRevisionConflict,
    FlowchartRevisionUpdate,
    FlowchartStateConflict,
)
from yuxi.services.document_ingestion_service import DocumentCreationResult
from yuxi.services.flowchart_ingestion_service import (
    FlowchartImmutable,
    FlowchartIngestionError,
    FlowchartIngestionService,
)
pytestmark = pytest.mark.asyncio

PDF_PATH = "http://minio/knowledgebases/kb-1/upload/process_1234567890123.pdf"
VALID_MARKDOWN = "# 采购审批\n\n" + "\n\n".join(
    f"## {section}\n\n未识别到明确内容"
    for section in (
        "流程用途", "参与角色", "主流程", "条件分支", "退回与异常路径",
        "关键上下游关系", "开始与结束", "补充说明",
    )
)


def make_record(**overrides):
    values = {
        "file_id": "file-flow",
        "kb_id": "kb-1",
        "filename": "process.pdf",
        "file_type": "pdf",
        "content_type": "file",
        "processing_params": {"ingestion_type": FLOWCHART_INGESTION_TYPE},
        "parse_metadata": {"flowchart": {"revision": 0}},
        "status": FileStatus.ERROR_FLOWCHART_PARSING,
        "markdown_file": None,
        "original_markdown_file": None,
        "path": PDF_PATH,
        "processing_stage": "flowchart_parsing",
        "processing_progress": 10,
        "error_message": None,
        "confirmed_at": None,
        "confirmed_by": None,
        "is_current": True,
        "is_active": True,
        "is_folder": False,
        "updated_by": "user-1",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class MemoryFlowchartRepository:
    def __init__(self, record=None):
        self.record = record or make_record()
        self.status_history = []

    async def get_by_file_id(self, file_id):
        return self.record if self.record.file_id == file_id else None

    async def update_flowchart_with_revision(
        self,
        *,
        expected_revision,
        data,
        metadata_updates=None,
        increment_revision=True,
        allowed_statuses=None,
        idempotent_if_confirmed=False,
        **_kwargs,
    ):
        if self.record.confirmed_at is not None:
            if idempotent_if_confirmed:
                return FlowchartRevisionUpdate(self.record, idempotent=True)
            raise FlowchartStateConflict("流程图当前版本已确认，不能继续修改")
        if flowchart_revision(self.record) != expected_revision:
            raise FlowchartRevisionConflict("流程图草稿已被其他编辑更新，请刷新后重试")
        if allowed_statuses and self.record.status not in allowed_statuses:
            raise FlowchartStateConflict("invalid status")
        metadata = deepcopy(self.record.parse_metadata or {})
        flowchart = deepcopy(metadata.get("flowchart") or {})
        flowchart.update(metadata_updates or {})
        flowchart["revision"] = expected_revision + 1 if increment_revision else expected_revision
        metadata["flowchart"] = flowchart
        self.record.parse_metadata = metadata
        for key, value in data.items():
            setattr(self.record, key, value)
        if "status" in data:
            self.status_history.append(data["status"])
        return FlowchartRevisionUpdate(self.record)

    async def update_flowchart_status(self, *, allowed_statuses, data, require_confirmed=None, **_kwargs):
        if self.record.status not in allowed_statuses:
            raise FlowchartStateConflict("invalid status")
        if require_confirmed is True and self.record.confirmed_at is None:
            raise FlowchartStateConflict("not confirmed")
        if require_confirmed is False and self.record.confirmed_at is not None:
            raise FlowchartStateConflict("confirmed")
        for key, value in data.items():
            setattr(self.record, key, value)
        return self.record


class FakeDocumentIngestion:
    def __init__(self, repository):
        self.repository = repository
        self.calls = []

    async def create_uploaded_document(self, **kwargs):
        self.calls.append(kwargs)
        self.repository.record.status = FileStatus.UPLOADED
        return DocumentCreationResult(
            action="created",
            file_meta={"file_id": self.repository.record.file_id, "content_type": "file"},
        )


class MemoryMinio:
    KB_BUCKETS = {"parsed": "knowledgebases"}

    def __init__(self):
        self.objects = {}

    def ensure_bucket_exists(self, _bucket):
        return None

    async def aupload_file(self, *, bucket_name, object_name, data, **_kwargs):
        self.objects[(bucket_name, object_name)] = data
        return SimpleNamespace(url=f"http://minio/{bucket_name}/{object_name}")

    async def adownload_file(self, bucket_name, object_name):
        return self.objects[(bucket_name, object_name)]

    async def adelete_file(self, bucket_name, object_name):
        self.objects.pop((bucket_name, object_name), None)


class FakeVisionAnalyzer:
    async def analyze_flowchart(self, images, _prompt, model_spec):
        return FlowchartAnalysisResult(
            pages=(PageFlowchartAnalysis(1, VALID_MARKDOWN, ()),),
            semantic_markdown=VALID_MARKDOWN,
            warnings=(),
            model_spec=model_spec,
        )


@pytest.fixture
def flowchart_service(monkeypatch):
    from yuxi.services import flowchart_ingestion_service as module

    repository = MemoryFlowchartRepository()
    ingestion = FakeDocumentIngestion(repository)
    minio = MemoryMinio()
    pdf = fitz.open()
    pdf.new_page()
    minio.objects[("knowledgebases", "kb-1/upload/process_1234567890123.pdf")] = pdf.tobytes()
    pdf.close()
    monkeypatch.setattr(module, "get_minio_client", lambda: minio)
    monkeypatch.setattr(module.config, "flowchart_vision_model_spec", "domestic:vision")

    async def start_index(_kb_id, _file_id, _operator_id):
        repository.record.status = FileStatus.INDEXED

    return (
        FlowchartIngestionService(
            file_repository=repository, document_ingestion=ingestion,
            vision_analyzer=FakeVisionAnalyzer(), index_starter=start_index,
        ),
        repository,
        ingestion,
    )


async def test_non_pdf_flowchart_creation_is_rejected_before_ingestion(flowchart_service):
    service, _repository, ingestion = flowchart_service

    with pytest.raises(FlowchartIngestionError, match="只支持 PDF"):
        await service.create(
            kb_id="kb-1",
            file_path="http://minio/knowledgebases/kb-1/upload/process_1234567890123.docx",
            params={},
            operator_id="user-1",
        )

    assert ingestion.calls == []


async def test_create_uses_server_owned_discriminator_and_preserves_content_type(flowchart_service):
    service, repository, ingestion = flowchart_service

    result = await service.create(kb_id="kb-1", file_path=PDF_PATH, params={}, operator_id="user-1")

    assert result["status"] == FileStatus.FLOWCHART_WAITING_CONFIRMATION
    assert result["revision"] == 1
    assert result["content_type"] == "file"
    assert is_flowchart(repository.record)
    assert ingestion.calls[0]["ingestion_type"] == FLOWCHART_INGESTION_TYPE
    assert repository.record.parse_metadata["flowchart"]["page_count"] == 1
    assert repository.status_history == [FileStatus.FLOWCHART_PARSING, FileStatus.FLOWCHART_WAITING_CONFIRMATION]
    preview = await service.get_preview(kb_id="kb-1", file_id="file-flow")
    assert "# 采购审批" in preview["semantic_markdown"]
    assert preview["flowchart_metadata"]["pages"][0]["page_number"] == 1
    assert "secret" not in repr(repository.record.parse_metadata)
    assert "base64" not in repr(repository.record.parse_metadata)


async def test_create_without_model_enters_parse_error_without_fake_draft(flowchart_service, monkeypatch):
    from yuxi.services import flowchart_ingestion_service as module

    service, repository, _ingestion = flowchart_service
    monkeypatch.setattr(module.config, "flowchart_vision_model_spec", None)

    result = await service.create(kb_id="kb-1", file_path=PDF_PATH, params={}, operator_id="admin")

    assert result["status"] == FileStatus.ERROR_FLOWCHART_PARSING
    assert result["error_code"] == "FLOWCHART_VISION_MODEL_NOT_CONFIGURED"
    assert repository.record.markdown_file is None
    assert repository.status_history == [FileStatus.FLOWCHART_PARSING, FileStatus.ERROR_FLOWCHART_PARSING]


async def test_reparse_failure_preserves_old_draft_and_reports_error(flowchart_service):
    service, repository, _ingestion = flowchart_service
    first = await service.create(kb_id="kb-1", file_path=PDF_PATH, params={}, operator_id="admin")
    old_path = repository.record.markdown_file

    class BrokenVision:
        async def analyze_flowchart(self, *_args):
            raise FlowchartAnalysisError("FLOWCHART_VISION_TIMEOUT", "视觉解析超时")

    service.vision_analyzer = BrokenVision()
    result = await service.reparse(
        kb_id="kb-1", file_id="file-flow", expected_revision=first["revision"], operator_id="admin"
    )

    assert result["status"] == FileStatus.ERROR_FLOWCHART_PARSING
    assert result["error_code"] == "FLOWCHART_VISION_TIMEOUT"
    assert repository.record.markdown_file == old_path
    assert result["revision"] == first["revision"]
    preview = await service.get_preview(kb_id="kb-1", file_id="file-flow")
    assert "# 采购审批" in preview["semantic_markdown"]
    assert preview["revision"] == first["revision"]
    assert preview["error_message"] == "视觉解析超时"
    assert preview["flowchart_metadata"]["error_code"] == "FLOWCHART_VISION_TIMEOUT"
    assert (await service.reparse(
        kb_id="kb-1", file_id="file-flow", expected_revision=first["revision"], operator_id="admin"
    ))["status"] == FileStatus.ERROR_FLOWCHART_PARSING


async def test_malformed_analyzer_output_is_not_persisted_as_draft(flowchart_service):
    service, repository, _ingestion = flowchart_service

    class MalformedVision:
        async def analyze_flowchart(self, _images, _prompt, model_spec):
            return FlowchartAnalysisResult(
                pages=(PageFlowchartAnalysis(1, "# 不完整", ()),),
                semantic_markdown="# 不完整\n\n## 主流程\n\n1. 步骤",
                warnings=(), model_spec=model_spec,
            )

    service.vision_analyzer = MalformedVision()
    result = await service.create(kb_id="kb-1", file_path=PDF_PATH, params={}, operator_id="admin")

    assert result["status"] == FileStatus.ERROR_FLOWCHART_PARSING
    assert result["error_code"] == "FLOWCHART_INVALID_OUTPUT"
    assert repository.record.markdown_file is None


async def test_successful_reparse_switches_draft_and_increments_revision(flowchart_service):
    service, repository, _ingestion = flowchart_service
    first = await service.create(kb_id="kb-1", file_path=PDF_PATH, params={}, operator_id="admin")
    old_path = repository.record.markdown_file

    result = await service.reparse(
        kb_id="kb-1", file_id="file-flow", expected_revision=first["revision"], operator_id="admin"
    )

    assert result["status"] == FileStatus.FLOWCHART_WAITING_CONFIRMATION
    assert result["revision"] == 2
    assert repository.record.markdown_file != old_path
    assert repository.record.original_markdown_file == old_path
    with pytest.raises(FlowchartRevisionConflict):
        await service.reparse(kb_id="kb-1", file_id="file-flow", expected_revision=1, operator_id="admin")


async def test_manual_edit_keeps_first_ai_output(flowchart_service):
    service, repository, _ingestion = flowchart_service
    first = await service.create(kb_id="kb-1", file_path=PDF_PATH, params={}, operator_id="admin")
    original_path = repository.record.original_markdown_file

    await service.update_draft(
        kb_id="kb-1", file_id="file-flow", expected_revision=first["revision"],
        semantic_markdown="# 人工修订\n\n已核对", operator_id="admin",
    )

    assert repository.record.original_markdown_file == original_path
    assert "# 采购审批" in await service._read_markdown(original_path)


async def test_draft_revision_increments_and_stale_revision_is_rejected(flowchart_service):
    service, repository, _ingestion = flowchart_service

    result = await service.update_draft(
        kb_id="kb-1",
        file_id="file-flow",
        expected_revision=0,
        semantic_markdown="# 流程名称\n\n采购审批",
        operator_id="admin",
    )

    assert result["revision"] == 1
    assert result["status"] == FileStatus.FLOWCHART_WAITING_CONFIRMATION
    with pytest.raises(FlowchartRevisionConflict):
        await service.update_draft(
            kb_id="kb-1",
            file_id="file-flow",
            expected_revision=0,
            semantic_markdown="# stale",
            operator_id="admin",
        )
    assert flowchart_revision(repository.record) == 1


async def test_confirm_freezes_draft_and_reparse_and_is_idempotent(flowchart_service):
    service, repository, _ingestion = flowchart_service
    await service.update_draft(
        kb_id="kb-1",
        file_id="file-flow",
        expected_revision=0,
        semantic_markdown=VALID_MARKDOWN,
        operator_id="admin",
    )

    first = await service.confirm(
        kb_id="kb-1", file_id="file-flow", expected_revision=1, operator_id="admin"
    )
    confirmed_at = repository.record.confirmed_at
    second = await service.confirm(
        kb_id="kb-1", file_id="file-flow", expected_revision=1, operator_id="admin"
    )

    assert first["status"] == FileStatus.INDEXED
    assert first["idempotent"] is False
    assert second["idempotent"] is True
    assert repository.record.confirmed_at == confirmed_at
    assert flowchart_revision(repository.record) == 2
    with pytest.raises(FlowchartImmutable):
        await service.update_draft(
            kb_id="kb-1",
            file_id="file-flow",
            expected_revision=2,
            semantic_markdown="# frozen",
            operator_id="admin",
        )
    with pytest.raises(FlowchartImmutable):
        await service.reparse(
            kb_id="kb-1", file_id="file-flow", expected_revision=2, operator_id="admin"
        )


async def test_confirmed_flowchart_can_start_only_one_index_side_effect(monkeypatch):
    from yuxi.services import flowchart_ingestion_service as module

    minio = MemoryMinio()
    monkeypatch.setattr(module, "get_minio_client", lambda: minio)
    repository = MemoryFlowchartRepository()
    ingestion = FakeDocumentIngestion(repository)
    calls = []

    async def start_index(kb_id, file_id, operator_id):
        calls.append((kb_id, file_id, operator_id))
        repository.record.status = FileStatus.INDEXED

    service = FlowchartIngestionService(
        file_repository=repository,
        document_ingestion=ingestion,
        index_starter=start_index,
    )
    await service.update_draft(
        kb_id="kb-1",
        file_id="file-flow",
        expected_revision=0,
        semantic_markdown=VALID_MARKDOWN,
        operator_id="admin",
    )
    await service.confirm(kb_id="kb-1", file_id="file-flow", expected_revision=1, operator_id="admin")
    await service.confirm(kb_id="kb-1", file_id="file-flow", expected_revision=1, operator_id="admin")

    assert calls == [("kb-1", "file-flow", "admin")]
    assert repository.record.status == FileStatus.INDEXED


async def test_confirm_rejects_stale_revision_and_malformed_markdown(flowchart_service):
    service, repository, _ingestion = flowchart_service
    await service.update_draft(
        kb_id="kb-1", file_id="file-flow", expected_revision=0,
        semantic_markdown="# Broken\n\n## 主流程\n提交申请", operator_id="admin",
    )

    with pytest.raises(FlowchartRevisionConflict):
        await service.confirm(kb_id="kb-1", file_id="file-flow", expected_revision=0, operator_id="admin")
    with pytest.raises(FlowchartIngestionError) as error:
        await service.confirm(kb_id="kb-1", file_id="file-flow", expected_revision=1, operator_id="admin")

    assert error.value.code == "FLOWCHART_INVALID_OUTPUT"
    assert repository.record.confirmed_at is None
    assert repository.record.status == FileStatus.FLOWCHART_WAITING_CONFIRMATION


async def test_failed_index_keeps_frozen_draft_and_retry_does_not_reparse(monkeypatch):
    from yuxi.services import flowchart_ingestion_service as module

    minio = MemoryMinio()
    monkeypatch.setattr(module, "get_minio_client", lambda: minio)
    repository = MemoryFlowchartRepository()
    calls = []

    async def start_index(_kb_id, _file_id, _operator_id):
        calls.append(repository.record.markdown_file)
        if len(calls) == 1:
            raise RuntimeError("temporary index failure")
        repository.record.status = FileStatus.INDEXED

    service = FlowchartIngestionService(
        file_repository=repository, document_ingestion=FakeDocumentIngestion(repository),
        index_starter=start_index,
    )
    await service.update_draft(
        kb_id="kb-1", file_id="file-flow", expected_revision=0,
        semantic_markdown=VALID_MARKDOWN, operator_id="admin",
    )
    frozen_path = repository.record.markdown_file
    failed = await service.confirm(kb_id="kb-1", file_id="file-flow", expected_revision=1, operator_id="admin")
    assert failed["status"] == FileStatus.ERROR_INDEXING
    assert repository.record.confirmed_at is not None
    with pytest.raises(FlowchartImmutable):
        await service.reparse(kb_id="kb-1", file_id="file-flow", expected_revision=2, operator_id="admin")

    retried = await service.retry_index(kb_id="kb-1", file_id="file-flow", operator_id="admin")
    assert retried["status"] == FileStatus.INDEXED
    assert calls == [frozen_path, frozen_path]
    assert repository.record.markdown_file == frozen_path
    assert flowchart_revision(repository.record) == 2
