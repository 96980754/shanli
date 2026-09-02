from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from server.routers import knowledge_router
from yuxi.services.document_diff_service import (
    DocumentDiffFamilyMismatchError,
    DocumentDiffNotFoundError,
)

pytestmark = pytest.mark.asyncio


async def _allow(*_args, **_kwargs):
    return None


def _request(a="file-v1", b="file-v2"):
    return knowledge_router.DocumentDiffRequest(version_a_file_id=a, version_b_file_id=b)


def _diff_result():
    return {
        "base": {"file_id": "file-v1", "document_version": 1, "is_current": False},
        "target": {"file_id": "file-v2", "document_version": 2, "is_current": True},
        "identical": False,
        "stats": {"added_lines": 1, "removed_lines": 1, "unchanged_lines": 1},
        "hunks": [],
    }


async def test_diff_document_versions_passes_ids_to_service_and_returns_result(monkeypatch):
    checks = []
    seen = {}

    async def require_permission(_user, kb_id, action):
        checks.append((kb_id, action))

    class FakeService:
        async def diff_versions(self, **kwargs):
            seen.update(kwargs)
            return _diff_result()

    monkeypatch.setattr(knowledge_router, "_require_kb_permission", require_permission)
    monkeypatch.setattr(knowledge_router, "_ensure_database_supports_documents", _allow)
    monkeypatch.setattr(knowledge_router, "DocumentDiffService", FakeService)

    result = await knowledge_router.diff_document_versions(
        "kb-1",
        _request(),
        current_user=SimpleNamespace(uid="viewer"),
    )

    assert seen["kb_id"] == "kb-1"
    assert seen["version_a_file_id"] == "file-v1"
    assert seen["version_b_file_id"] == "file-v2"
    assert result["identical"] is False
    assert checks == [("kb-1", "can_view")]


async def test_diff_document_versions_requires_document_support(monkeypatch):
    async def ensure(kb_id, operation):
        raise HTTPException(status_code=400, detail=f"只支持检索，不支持{operation}")

    async def require_permission(*_args, **_kwargs):
        return None

    class FakeService:
        async def diff_versions(self, **kwargs):
            return _diff_result()

    monkeypatch.setattr(knowledge_router, "_require_kb_permission", require_permission)
    monkeypatch.setattr(knowledge_router, "_ensure_database_supports_documents", ensure)
    monkeypatch.setattr(knowledge_router, "DocumentDiffService", FakeService)

    with pytest.raises(HTTPException) as exc_info:
        await knowledge_router.diff_document_versions(
            "kb-retrieval-only",
            _request(),
            current_user=SimpleNamespace(uid="viewer"),
        )
    assert exc_info.value.status_code == 400


async def test_diff_document_versions_missing_or_wrong_kb_maps_to_404(monkeypatch):
    class FakeService:
        async def diff_versions(self, **kwargs):
            raise DocumentDiffNotFoundError("文件不存在: file-v9")

    monkeypatch.setattr(knowledge_router, "_require_kb_permission", _allow)
    monkeypatch.setattr(knowledge_router, "_ensure_database_supports_documents", _allow)
    monkeypatch.setattr(knowledge_router, "DocumentDiffService", FakeService)

    with pytest.raises(HTTPException) as exc_info:
        await knowledge_router.diff_document_versions(
            "kb-1",
            _request(a="file-v9", b="file-v2"),
            current_user=SimpleNamespace(uid="viewer"),
        )
    assert exc_info.value.status_code == 404


async def test_diff_document_versions_cross_family_maps_to_400(monkeypatch):
    class FakeService:
        async def diff_versions(self, **kwargs):
            raise DocumentDiffFamilyMismatchError("两个版本不属于同一逻辑文档")

    monkeypatch.setattr(knowledge_router, "_require_kb_permission", _allow)
    monkeypatch.setattr(knowledge_router, "_ensure_database_supports_documents", _allow)
    monkeypatch.setattr(knowledge_router, "DocumentDiffService", FakeService)

    with pytest.raises(HTTPException) as exc_info:
        await knowledge_router.diff_document_versions(
            "kb-1",
            _request(a="file-other-family", b="file-v2"),
            current_user=SimpleNamespace(uid="viewer"),
        )
    assert exc_info.value.status_code == 400
