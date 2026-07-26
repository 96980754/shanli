from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from server.routers import knowledge_router
from yuxi.services.document_cleaning_service import DocumentCleaningNotFound

pytestmark = pytest.mark.asyncio


async def _allow_documents(*_args, **_kwargs):
    return None


async def test_cleaning_preview_requires_view_and_uses_manage_for_readonly(monkeypatch):
    permission_checks = []

    async def require_permission(_user, kb_id, action):
        permission_checks.append(("required", kb_id, action))

    async def has_permission(_user, kb_id, action):
        permission_checks.append(("optional", kb_id, action))
        return False

    class FakeCleaningService:
        async def get_preview(self, *, kb_id, file_id):
            return {"kb_id": kb_id, "file_id": file_id, "cleaned_markdown": "draft"}

    monkeypatch.setattr(knowledge_router, "_require_kb_permission", require_permission)
    monkeypatch.setattr(knowledge_router, "_has_kb_permission", has_permission)
    monkeypatch.setattr(knowledge_router, "_ensure_database_supports_documents", _allow_documents)
    monkeypatch.setattr(knowledge_router, "DocumentCleaningService", FakeCleaningService)

    result = await knowledge_router.get_document_cleaning_preview(
        "kb-1",
        "file-1",
        current_user=SimpleNamespace(uid="viewer"),
    )

    assert result["readonly"] is True
    assert permission_checks == [
        ("required", "kb-1", "can_view"),
        ("optional", "kb-1", "can_manage"),
    ]


async def test_cleaning_write_endpoints_require_manage(monkeypatch):
    permission_checks = []

    async def require_permission(_user, kb_id, action):
        permission_checks.append((kb_id, action))

    class FakeCleaningService:
        async def save_draft(self, **kwargs):
            return {"file_id": kwargs["file_id"], "cleaning_version": 2}

    monkeypatch.setattr(knowledge_router, "_require_kb_permission", require_permission)
    monkeypatch.setattr(knowledge_router, "_ensure_database_supports_documents", _allow_documents)
    monkeypatch.setattr(knowledge_router, "DocumentCleaningService", FakeCleaningService)

    request = knowledge_router.CleaningDraftUpdateRequest(content="edited", version=1)
    result = await knowledge_router.update_document_cleaning_draft(
        "kb-1",
        "file-1",
        request,
        current_user=SimpleNamespace(uid="editor"),
    )

    assert result["readonly"] is False
    assert permission_checks == [("kb-1", "can_manage")]


async def test_cleaning_write_permission_denial_stops_before_service(monkeypatch):
    service_created = False

    async def deny_permission(_user, _kb_id, _action):
        raise HTTPException(status_code=403, detail="知识库权限不足")

    class UnexpectedCleaningService:
        def __init__(self):
            nonlocal service_created
            service_created = True

    monkeypatch.setattr(knowledge_router, "_require_kb_permission", deny_permission)
    monkeypatch.setattr(knowledge_router, "DocumentCleaningService", UnexpectedCleaningService)

    with pytest.raises(HTTPException) as exc_info:
        await knowledge_router.confirm_document_cleaning(
            "kb-1",
            "file-1",
            knowledge_router.CleaningVersionRequest(version=1),
            current_user=SimpleNamespace(uid="viewer"),
        )

    assert exc_info.value.status_code == 403
    assert service_created is False


async def test_cross_knowledge_base_file_id_returns_not_found(monkeypatch):
    class FakeCleaningService:
        async def get_preview(self, *, kb_id, file_id):
            raise DocumentCleaningNotFound("文档不存在")

    async def has_permission(*_args, **_kwargs):
        return True

    monkeypatch.setattr(knowledge_router, "_require_kb_permission", _allow_documents)
    monkeypatch.setattr(knowledge_router, "_has_kb_permission", has_permission)
    monkeypatch.setattr(knowledge_router, "_ensure_database_supports_documents", _allow_documents)
    monkeypatch.setattr(knowledge_router, "DocumentCleaningService", FakeCleaningService)

    with pytest.raises(HTTPException) as exc_info:
        await knowledge_router.get_document_cleaning_preview(
            "kb-other",
            "file-1",
            current_user=SimpleNamespace(uid="viewer"),
        )

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "文档不存在"
