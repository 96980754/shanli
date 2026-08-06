from __future__ import annotations
from datetime import datetime
from types import SimpleNamespace
import pytest
from yuxi.knowledge.base import FileStatus
from yuxi.knowledge.enrichment import EnrichmentProviderUnavailable
from yuxi.services.document_enrichment_service import (
    EnrichmentNotFound,
    EnrichmentVersionConflict,
    DocumentEnrichmentService,
)
def _record(**overrides):
    values = {
        "file_id": "file-1",
        "kb_id": "kb-1",
        "filename": "doc.md",
        "markdown_file": "minio://parsed/kb-1/doc.md",
        "cleaning_draft_file": "minio://parsed/kb-1/draft.md",
        "cleaning_version": 3,
        "confirmed_at": datetime(2026, 7, 26),
        "status": FileStatus.INDEXED,
        "is_active": True,
        "is_folder": False,
        "enrichment_data": {},
        "enrichment_status": None,
        "enrichment_version": 0,
        "enrichment_content_hash": None,
        "enrichment_generated_at": None,
        "enrichment_error": None,
        "enrichment_possibly_outdated": False,
        "updated_by": "user-1",
    }
    values.update(overrides)
    return SimpleNamespace(**values)
class _FakeRepository:
    def __init__(self, record):
        self.record = record
    async def get_by_file_id(self, file_id):
        return self.record if file_id == self.record.file_id else None
    async def update_enrichment_fields_with_version(
        self,
        *,
        kb_id,
        file_id,
        expected_version,
        expected_cleaning_version,
        data,
        increment_version,
        require_active=True,
    ):
        record = await self.get_by_file_id(file_id)
        if (
            record is None
            or record.kb_id != kb_id
            or record.enrichment_version != expected_version
            or record.cleaning_version != expected_cleaning_version
            or (require_active and not record.is_active)
        ):
            return None
        for key, value in data.items():
            setattr(record, key, value)
        if increment_version:
            record.enrichment_version += 1
        return record
class _FakeGenerator:
    async def generate(self, markdown, *, components, model_spec, **_kwargs):
        assert markdown == "# 正式正文\n\nShanli 2.1 支持知识库。"
        assert model_spec == "configured:model"
        return {
            "summary": "Shanli 2.1 支持知识库。",
            "keywords": ["Shanli", "知识库"],
            "tags": ["RAG", "rag"],
            "model_name": "configured-model",
            "model_version": "test",
        }
class _FailingGenerator:
    async def generate(self, *_args, **_kwargs):
        raise RuntimeError("provider failed at https://private.example/v1")
class _UnavailableGenerator:
    async def generate(self, *_args, **_kwargs):
        raise EnrichmentProviderUnavailable("文档信息增强模型未配置")
class _BodyChangingGenerator(_FakeGenerator):
    def __init__(self, record):
        self.record = record
    async def generate(self, markdown, **kwargs):
        result = await super().generate(markdown, **kwargs)
        self.record.cleaning_version += 1
        return result
def _service(monkeypatch, record):
    repository = _FakeRepository(record)
    service = DocumentEnrichmentService(file_repository=repository, generator=_FakeGenerator())
    async def read_markdown(path):
        assert path == record.markdown_file
        return "# 正式正文\n\nShanli 2.1 支持知识库。"
    monkeypatch.setattr(service, "_read_markdown", read_markdown)
    return service, repository
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"status": FileStatus.WAITING_CONFIRMATION}, "尚未确认并完成入库"),
        ({"is_active": False}, "当前生效版本"),
        ({"confirmed_at": None}, "尚未确认并完成入库"),
    ],
)
async def test_generation_only_accepts_active_confirmed_formal_markdown(monkeypatch, overrides, message):
    service, _repository = _service(monkeypatch, _record(**overrides))
    with pytest.raises(EnrichmentNotFound, match=message):
        await service.generate(
            kb_id="kb-1",
            file_id="file-1",
            operator_id="user-1",
            components={"summary", "keywords", "tags"},
            model_spec="configured:model",
        )
@pytest.mark.asyncio
async def test_generation_persists_version_bound_results(monkeypatch):
    record = _record()
    service, _repository = _service(monkeypatch, record)
    result = await service.generate(
        kb_id="kb-1",
        file_id="file-1",
        operator_id="user-1",
        components={"summary", "keywords", "tags"},
        model_spec="configured:model",
    )
    assert result["status"] == "ready"
    assert result["content_version"] == 3
    assert result["summary"]["source"] == "generated"
    assert [item["normalized_value"] for item in result["keywords"]] == ["shanli", "知识库"]
    assert [item["normalized_name"] for item in result["tags"]] == ["rag"]
    assert record.status == FileStatus.INDEXED
@pytest.mark.asyncio
async def test_automatic_generation_does_not_overwrite_manual_components(monkeypatch):
    record = _record(
        enrichment_data={
            "summary": {
                "text": "人工摘要",
                "source": "manual",
                "status": "ready",
                "content_version": 3,
            },
            "keywords": [],
            "tags": [],
        },
        enrichment_version=2,
    )
    service, _repository = _service(monkeypatch, record)
    result = await service.generate(
        kb_id="kb-1",
        file_id="file-1",
        operator_id="user-1",
        components={"summary", "keywords"},
        model_spec="configured:model",
    )
    assert result["summary"]["text"] == "人工摘要"
    assert result["summary"]["source"] == "manual"
    assert result["keywords"][0]["source"] == "generated"
@pytest.mark.asyncio
async def test_automatic_generation_preserves_manual_keywords_and_tags(monkeypatch):
    record = _record(
        enrichment_data={
            "summary": {},
            "keywords": [{"value": "人工关键词", "source": "manual", "status": "ready"}],
            "tags": [{"name": "人工标签", "source": "manual", "status": "ready"}],
        },
        enrichment_version=2,
    )
    service, _repository = _service(monkeypatch, record)
    result = await service.generate(
        kb_id="kb-1",
        file_id="file-1",
        operator_id="user-1",
        components={"summary", "keywords", "tags"},
        model_spec="configured:model",
    )
    assert result["keywords"][0]["value"] == "人工关键词"
    assert result["keywords"][0]["source"] == "manual"
    assert result["tags"][0]["name"] == "人工标签"
    assert result["tags"][0]["source"] == "manual"
@pytest.mark.asyncio
async def test_explicit_generation_can_replace_selected_manual_component(monkeypatch):
    record = _record(
        enrichment_data={
            "summary": {
                "text": "人工摘要",
                "source": "manual",
                "status": "ready",
                "content_version": 3,
            }
        },
        enrichment_version=2,
    )
    service, _repository = _service(monkeypatch, record)
    result = await service.generate(
        kb_id="kb-1",
        file_id="file-1",
        operator_id="user-1",
        components={"summary"},
        model_spec="configured:model",
        overwrite_manual=True,
    )
    assert result["summary"]["text"] == "Shanli 2.1 支持知识库。"
    assert result["summary"]["source"] == "generated"
@pytest.mark.asyncio
async def test_deleting_all_manual_keywords_still_protects_the_empty_manual_result(monkeypatch):
    record = _record(enrichment_version=1)
    service, _repository = _service(monkeypatch, record)
    manual = await service.update_keywords(
        kb_id="kb-1",
        file_id="file-1",
        operator_id="user-1",
        expected_version=1,
        values=[],
    )
    regenerated = await service.generate(
        kb_id="kb-1",
        file_id="file-1",
        operator_id="user-1",
        components={"keywords"},
        model_spec="configured:model",
    )
    assert manual["keyword_source"] == "manual"
    assert regenerated["keywords"] == []
    assert regenerated["keyword_source"] == "manual"
    assert regenerated["idempotent"] is True
@pytest.mark.asyncio
async def test_provider_failure_is_visible_without_changing_indexed_status(monkeypatch):
    record = _record()
    service, repository = _service(monkeypatch, record)
    service.generator = _FailingGenerator()
    with pytest.raises(ValueError, match="provider failed"):
        await service.generate(
            kb_id="kb-1",
            file_id="file-1",
            operator_id="user-1",
            components={"summary"},
            model_spec="configured:model",
        )
    assert repository.record.status == FileStatus.INDEXED
    assert repository.record.enrichment_status == "failed"
    assert "private.example" not in repository.record.enrichment_error
@pytest.mark.asyncio
async def test_missing_provider_is_skipped_without_changing_indexed_status(monkeypatch):
    record = _record()
    service, repository = _service(monkeypatch, record)
    service.generator = _UnavailableGenerator()
    result = await service.generate(
        kb_id="kb-1",
        file_id="file-1",
        operator_id="user-1",
        components={"summary"},
        model_spec=None,
    )
    assert result["status"] == "skipped"
    assert repository.record.status == FileStatus.INDEXED
@pytest.mark.asyncio
async def test_old_generation_cannot_overwrite_a_new_body_version(monkeypatch):
    record = _record()
    service, _repository = _service(monkeypatch, record)
    service.generator = _BodyChangingGenerator(record)
    with pytest.raises(EnrichmentVersionConflict, match="正文版本"):
        await service.generate(
            kb_id="kb-1",
            file_id="file-1",
            operator_id="user-1",
            components={"summary", "keywords", "tags"},
            model_spec="configured:model",
        )
    assert record.enrichment_data == {}
@pytest.mark.asyncio
async def test_manual_update_uses_optimistic_version(monkeypatch):
    record = _record(enrichment_version=4)
    service, _repository = _service(monkeypatch, record)
    result = await service.update_summary(
        kb_id="kb-1",
        file_id="file-1",
        operator_id="user-2",
        expected_version=4,
        text="人工摘要",
    )
    assert result["version"] == 5
    assert result["summary"]["source"] == "manual"
    with pytest.raises(EnrichmentVersionConflict):
        await service.update_summary(
            kb_id="kb-1",
            file_id="file-1",
            operator_id="user-3",
            expected_version=4,
            text="过期编辑",
        )
