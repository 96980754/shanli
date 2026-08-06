from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from server.routers import knowledge_router
from yuxi.services.document_enrichment_service import EnrichmentNotFound
pytestmark = pytest.mark.asyncio
async def _allow_documents(*_args, **_kwargs):
    return None
async def test_enrichment_view_requires_view_and_reports_readonly(monkeypatch):
    checks = []
    async def require_permission(_user, kb_id, action):
        checks.append(("required", kb_id, action))
    async def has_permission(_user, kb_id, action):
        checks.append(("optional", kb_id, action))
        return False
    class FakeService:
        async def get(self, *, kb_id, file_id):
            return {"kb_id": kb_id, "file_id": file_id, "status": "ready"}
    monkeypatch.setattr(knowledge_router, "_require_kb_permission", require_permission)
    monkeypatch.setattr(knowledge_router, "_has_kb_permission", has_permission)
    monkeypatch.setattr(knowledge_router, "_ensure_database_supports_documents", _allow_documents)
    monkeypatch.setattr(knowledge_router, "DocumentEnrichmentService", FakeService)
    result = await knowledge_router.get_document_enrichment(
        "kb-1",
        "file-1",
        current_user=SimpleNamespace(uid="viewer"),
    )
    assert result["readonly"] is True
    assert checks == [
        ("required", "kb-1", "can_view"),
        ("optional", "kb-1", "can_manage"),
    ]
async def test_enrichment_generate_and_edit_require_manage(monkeypatch):
    checks = []
    async def require_permission(_user, kb_id, action):
        checks.append((kb_id, action))
    async def enqueue(**kwargs):
        assert kwargs["components"] == {"summary"}
        return "task-1", True
    class FakeService:
        async def update_summary(self, **kwargs):
            return {"file_id": kwargs["file_id"], "version": kwargs["expected_version"] + 1}
    monkeypatch.setattr(knowledge_router, "_require_kb_permission", require_permission)
    monkeypatch.setattr(knowledge_router, "_ensure_database_supports_documents", _allow_documents)
    monkeypatch.setattr(knowledge_router, "enqueue_document_enrichment", enqueue)
    monkeypatch.setattr(knowledge_router, "DocumentEnrichmentService", FakeService)
    generated = await knowledge_router.generate_document_enrichment(
        "kb-1",
        "file-1",
        knowledge_router.EnrichmentGenerateRequest(components=["summary"]),
        current_user=SimpleNamespace(uid="editor"),
    )
    updated = await knowledge_router.update_document_summary(
        "kb-1",
        "file-1",
        knowledge_router.EnrichmentSummaryUpdateRequest(version=1, text="人工摘要"),
        current_user=SimpleNamespace(uid="editor"),
    )
    assert generated == {"status": "queued", "task_id": "task-1", "created": True}
    assert updated["version"] == 2
    assert checks == [("kb-1", "can_manage"), ("kb-1", "can_manage")]
async def test_cross_knowledge_base_enrichment_returns_not_found(monkeypatch):
    class FakeService:
        async def get(self, **_kwargs):
            raise EnrichmentNotFound("文档不存在")
    async def has_permission(*_args, **_kwargs):
        return True
    monkeypatch.setattr(knowledge_router, "_require_kb_permission", _allow_documents)
    monkeypatch.setattr(knowledge_router, "_has_kb_permission", has_permission)
    monkeypatch.setattr(knowledge_router, "_ensure_database_supports_documents", _allow_documents)
    monkeypatch.setattr(knowledge_router, "DocumentEnrichmentService", FakeService)
    with pytest.raises(HTTPException) as exc_info:
        await knowledge_router.get_document_enrichment(
            "kb-other",
            "file-1",
            current_user=SimpleNamespace(uid="viewer"),
        )
    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "文档不存在"
