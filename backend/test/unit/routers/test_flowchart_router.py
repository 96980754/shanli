from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from server.routers import flowchart_router
from yuxi.repositories.knowledge_file_repository import FlowchartRevisionConflict

pytestmark = pytest.mark.asyncio


async def _allow_documents(*_args, **_kwargs):
    return None


async def test_flowchart_api_skeleton_routes_are_registered():
    routes = {(route.path, tuple(sorted(route.methods or set()))) for route in flowchart_router.flowcharts.routes}
    assert ("/knowledge/databases/{kb_id}/flowcharts", ("POST",)) in routes
    assert ("/knowledge/databases/{kb_id}/flowcharts/{file_id}/preview", ("GET",)) in routes
    assert ("/knowledge/databases/{kb_id}/flowcharts/{file_id}/draft", ("PUT",)) in routes
    assert ("/knowledge/databases/{kb_id}/flowcharts/{file_id}/reparse", ("POST",)) in routes
    assert ("/knowledge/databases/{kb_id}/flowcharts/{file_id}/confirm", ("POST",)) in routes
    assert ("/knowledge/databases/{kb_id}/flowcharts/{file_id}/retry-index", ("POST",)) in routes


async def test_create_requires_existing_upload_permission(monkeypatch):
    service_created = False

    async def deny_permission(_user, _kb_id, action):
        assert action == "can_upload"
        raise HTTPException(status_code=403, detail="知识库权限不足")

    class UnexpectedService:
        def __init__(self):
            nonlocal service_created
            service_created = True

    monkeypatch.setattr(flowchart_router, "_require_kb_permission", deny_permission)
    monkeypatch.setattr(flowchart_router, "FlowchartIngestionService", UnexpectedService)

    with pytest.raises(HTTPException) as exc_info:
        await flowchart_router.create_flowchart(
            "kb-1",
            flowchart_router.FlowchartCreateRequest(file_path="http://minio/file.pdf"),
            current_user=SimpleNamespace(uid="viewer"),
        )

    assert exc_info.value.status_code == 403
    assert service_created is False


async def test_preview_requires_view_and_uses_manage_for_readonly(monkeypatch):
    checks = []

    async def require_permission(_user, kb_id, action):
        checks.append(("required", kb_id, action))

    async def has_permission(_user, kb_id, action):
        checks.append(("optional", kb_id, action))
        return False

    class FakeService:
        async def get_preview(self, **_kwargs):
            return {"confirmed_at": None, "semantic_markdown": "# Flow"}

    monkeypatch.setattr(flowchart_router, "_require_kb_permission", require_permission)
    monkeypatch.setattr(flowchart_router, "_has_kb_permission", has_permission)
    monkeypatch.setattr(flowchart_router, "_ensure_database_supports_documents", _allow_documents)
    monkeypatch.setattr(flowchart_router, "FlowchartIngestionService", FakeService)

    result = await flowchart_router.get_flowchart_preview(
        "kb-1", "file-1", current_user=SimpleNamespace(uid="viewer")
    )

    assert result["readonly"] is True
    assert checks == [
        ("required", "kb-1", "can_view"),
        ("optional", "kb-1", "can_manage"),
    ]


async def test_stale_draft_revision_returns_409(monkeypatch):
    async def require_permission(_user, _kb_id, action):
        assert action == "can_manage"

    class FakeService:
        async def update_draft(self, **_kwargs):
            raise FlowchartRevisionConflict("stale")

    monkeypatch.setattr(flowchart_router, "_require_kb_permission", require_permission)
    monkeypatch.setattr(flowchart_router, "_ensure_database_supports_documents", _allow_documents)
    monkeypatch.setattr(flowchart_router, "FlowchartIngestionService", FakeService)

    with pytest.raises(HTTPException) as exc_info:
        await flowchart_router.update_flowchart_draft(
            "kb-1",
            "file-1",
            flowchart_router.FlowchartDraftRequest(expected_revision=0, semantic_markdown="# Flow"),
            current_user=SimpleNamespace(uid="admin"),
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == {"code": "FLOWCHART_REVISION_CONFLICT", "message": "stale"}


async def test_mutating_routes_reuse_manage_permission(monkeypatch):
    checks = []

    async def require_permission(_user, kb_id, action):
        checks.append((kb_id, action))

    class FakeService:
        async def reparse(self, **_kwargs):
            return {"status": "error_flowchart_parsing"}

        async def confirm(self, **_kwargs):
            return {"status": "error_indexing"}

        async def retry_index(self, **_kwargs):
            return {"status": "indexed"}

    monkeypatch.setattr(flowchart_router, "_require_kb_permission", require_permission)
    monkeypatch.setattr(flowchart_router, "_ensure_database_supports_documents", _allow_documents)
    monkeypatch.setattr(flowchart_router, "FlowchartIngestionService", FakeService)
    request = flowchart_router.FlowchartRevisionRequest(expected_revision=1)

    await flowchart_router.reparse_flowchart(
        "kb-1", "file-1", request, current_user=SimpleNamespace(uid="admin")
    )
    await flowchart_router.confirm_flowchart(
        "kb-1", "file-1", request, current_user=SimpleNamespace(uid="admin")
    )
    await flowchart_router.retry_flowchart_index(
        "kb-1", "file-1", current_user=SimpleNamespace(uid="admin")
    )

    assert checks == [("kb-1", "can_manage")] * 3
