from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from server.routers import knowledge_router


pytestmark = pytest.mark.asyncio


class FakePermissionService:
    def __init__(self, allowed: bool):
        self.allowed = allowed
        self.calls = []

    async def has_permission(self, user, kb_id, action):
        self.calls.append((user, kb_id, action))
        return self.allowed

    async def effective_permissions(self, user, kb_id):
        self.calls.append((user, kb_id, "effective"))
        return SimpleNamespace(
            can_view=self.allowed,
            can_search=self.allowed,
            can_upload=self.allowed,
            can_download=self.allowed,
            can_delete=self.allowed,
            can_manage=self.allowed,
            can_grant=self.allowed,
            can_export=self.allowed,
        )


class FakePermissionRepository:
    def __init__(self):
        self.permissions = [
            SimpleNamespace(
                id=1,
                kb_id="kb-1",
                subject_type="user",
                subject_id="zhangsan",
                can_view=True,
                can_search=True,
                can_upload=False,
                can_download=False,
                can_delete=False,
                can_manage=False,
                can_grant=False,
                can_export=False,
            )
        ]
        self.upsert_payload = None
        self.deleted_id = None

    async def list_by_kb_id(self, kb_id):
        return [permission for permission in self.permissions if permission.kb_id == kb_id]

    async def upsert(self, payload):
        self.upsert_payload = payload
        return SimpleNamespace(id=2, **payload)

    async def delete(self, permission_id):
        self.deleted_id = permission_id
        return True


def install_fakes(monkeypatch, *, allowed=True):
    service = FakePermissionService(allowed)
    repository = FakePermissionRepository()
    monkeypatch.setattr(knowledge_router, "KnowledgePermissionService", lambda: service)
    monkeypatch.setattr(knowledge_router, "KnowledgePermissionRepository", lambda: repository)
    return service, repository


def user(uid="admin", role="admin", department_id=1, team_id=None):
    return SimpleNamespace(uid=uid, role=role, department_id=department_id, team_id=team_id)


async def test_list_database_permissions_requires_grant_permission(monkeypatch):
    install_fakes(monkeypatch, allowed=False)

    with pytest.raises(HTTPException) as exc_info:
        await knowledge_router.list_database_permissions("kb-1", current_user=user())

    assert exc_info.value.status_code == 403


async def test_list_database_permissions_returns_permission_matrix(monkeypatch):
    service, _repository = install_fakes(monkeypatch, allowed=True)

    result = await knowledge_router.list_database_permissions("kb-1", current_user=user(uid="owner"))

    assert service.calls == [
        ({"uid": "owner", "role": "admin", "department_id": 1, "team_id": None}, "kb-1", "can_grant")
    ]
    assert result == {
        "permissions": [
            {
                "id": 1,
                "kb_id": "kb-1",
                "subject_type": "user",
                "subject_id": "zhangsan",
                "can_view": True,
                "can_search": True,
                "can_upload": False,
                "can_download": False,
                "can_delete": False,
                "can_manage": False,
                "can_grant": False,
                "can_export": False,
            }
        ]
    }


async def test_upsert_database_permission_persists_requested_flags(monkeypatch):
    _service, repository = install_fakes(monkeypatch, allowed=True)
    request = knowledge_router.KnowledgePermissionUpsertRequest(
        subject_type="department",
        subject_id="10",
        can_view=True,
        can_search=True,
        can_upload=True,
    )

    result = await knowledge_router.upsert_database_permission("kb-1", request, current_user=user())

    assert repository.upsert_payload == {
        "kb_id": "kb-1",
        "subject_type": "department",
        "subject_id": "10",
        "can_view": True,
        "can_search": True,
        "can_upload": True,
        "can_download": False,
        "can_delete": False,
        "can_manage": False,
        "can_grant": False,
        "can_export": False,
    }
    assert result["permission"]["id"] == 2


async def test_delete_database_permission_requires_grant_and_deletes(monkeypatch):
    _service, repository = install_fakes(monkeypatch, allowed=True)

    result = await knowledge_router.delete_database_permission("kb-1", 3, current_user=user())

    assert repository.deleted_id == 3
    assert result == {"message": "permission deleted"}


async def test_get_database_access_returns_effective_permissions(monkeypatch):
    service, _repository = install_fakes(monkeypatch, allowed=True)

    result = await knowledge_router.get_database_access("kb-1", current_user=user(uid="viewer", role="user"))

    assert service.calls == [
        ({"uid": "viewer", "role": "user", "department_id": 1, "team_id": None}, "kb-1", "effective")
    ]
    assert result == {
        "can_view": True,
        "can_search": True,
        "can_upload": True,
        "can_download": True,
        "can_delete": True,
        "can_manage": True,
        "can_grant": True,
        "can_export": True,
    }


async def test_get_database_access_rejects_user_without_view_permission(monkeypatch):
    install_fakes(monkeypatch, allowed=False)

    with pytest.raises(HTTPException) as exc_info:
        await knowledge_router.get_database_access("kb-1", current_user=user(uid="viewer", role="user"))

    assert exc_info.value.status_code == 403


async def test_get_database_info_requires_view_permission(monkeypatch):
    service, _repository = install_fakes(monkeypatch, allowed=False)

    with pytest.raises(HTTPException) as exc_info:
        await knowledge_router.get_database_info("kb-1", current_user=user(uid="viewer"))

    assert exc_info.value.status_code == 403
    assert service.calls == [
        ({"uid": "viewer", "role": "admin", "department_id": 1, "team_id": None}, "kb-1", "can_view")
    ]


async def test_update_database_info_requires_manage_permission(monkeypatch):
    service, _repository = install_fakes(monkeypatch, allowed=False)
    request = knowledge_router.UpdateDatabaseRequest(name="name", description="desc")

    with pytest.raises(HTTPException) as exc_info:
        await knowledge_router.update_database_info("kb-1", request, current_user=user(uid="manager"))

    assert exc_info.value.status_code == 403
    assert service.calls == [
        ({"uid": "manager", "role": "admin", "department_id": 1, "team_id": None}, "kb-1", "can_manage")
    ]


async def test_list_documents_requires_view_permission(monkeypatch):
    service, _repository = install_fakes(monkeypatch, allowed=False)

    with pytest.raises(HTTPException) as exc_info:
        await knowledge_router.list_documents("kb-1", current_user=user(uid="viewer"))

    assert exc_info.value.status_code == 403
    assert service.calls == [
        ({"uid": "viewer", "role": "admin", "department_id": 1, "team_id": None}, "kb-1", "can_view")
    ]


async def test_download_document_requires_view_and_download_permissions(monkeypatch):
    service, _repository = install_fakes(monkeypatch, allowed=True)
    monkeypatch.setattr(knowledge_router, "_ensure_database_supports_documents", AsyncMock())
    get_file_download = AsyncMock(
        return_value={"filename": "demo.txt", "content": b"demo", "media_type": "text/plain"}
    )
    monkeypatch.setattr(knowledge_router.knowledge_base, "get_file_download", get_file_download)

    response = await knowledge_router.download_document(
        "kb-1",
        "file-1",
        current_user=user(uid="viewer", role="user"),
    )

    assert service.calls == [
        ({"uid": "viewer", "role": "user", "department_id": 1, "team_id": None}, "kb-1", "can_view"),
        ({"uid": "viewer", "role": "user", "department_id": 1, "team_id": None}, "kb-1", "can_download"),
    ]
    get_file_download.assert_awaited_once_with(kb_id="kb-1", file_id="file-1", variant="original")
    assert response.media_type == "text/plain"


async def test_download_document_rejects_download_only_permission(monkeypatch):
    class DownloadOnlyPermissionService:
        async def has_permission(self, user, kb_id, action):
            return action == "can_download"

    get_file_download = AsyncMock()
    monkeypatch.setattr(knowledge_router, "KnowledgePermissionService", DownloadOnlyPermissionService)
    monkeypatch.setattr(knowledge_router.knowledge_base, "get_file_download", get_file_download)

    with pytest.raises(HTTPException) as exc_info:
        await knowledge_router.download_document(
            "kb-1",
            "file-1",
            current_user=user(uid="viewer", role="user"),
        )

    assert exc_info.value.status_code == 403
    get_file_download.assert_not_awaited()


async def test_preview_requires_search_permission(monkeypatch):
    service, _repository = install_fakes(monkeypatch, allowed=False)
    preview = AsyncMock()
    monkeypatch.setattr(
        knowledge_router,
        "KnowledgePreviewService",
        lambda: SimpleNamespace(preview=preview),
    )

    with pytest.raises(HTTPException) as exc_info:
        await knowledge_router.preview_knowledge_base(
            "kb-1",
            knowledge_router.KnowledgePreviewRequest(query="问题"),
            current_user=user(uid="viewer", role="user"),
        )

    assert exc_info.value.status_code == 403
    assert service.calls == [
        ({"uid": "viewer", "role": "user", "department_id": 1}, "kb-1", "can_search")
    ]
    preview.assert_not_awaited()


async def test_preview_uses_scoped_service_and_hides_provider_error(monkeypatch):
    service, _repository = install_fakes(monkeypatch, allowed=True)
    preview = AsyncMock(side_effect=knowledge_router.KnowledgePreviewModelError("secret"))
    monkeypatch.setattr(
        knowledge_router,
        "KnowledgePreviewService",
        lambda: SimpleNamespace(preview=preview),
    )

    with pytest.raises(HTTPException) as exc_info:
        await knowledge_router.preview_knowledge_base(
            "kb-1",
            knowledge_router.KnowledgePreviewRequest(query=" 问题 ", meta={"search_mode": "hybrid"}),
            current_user=user(uid="viewer", role="user"),
        )

    assert exc_info.value.status_code == 503
    assert exc_info.value.detail == "回答模型暂时不可用，请检查知识库模型配置"
    assert "secret" not in exc_info.value.detail
    assert service.calls == [
        ({"uid": "viewer", "role": "user", "department_id": 1}, "kb-1", "can_search")
    ]
    preview.assert_awaited_once_with(
        kb_id="kb-1",
        query="问题",
        meta={"search_mode": "hybrid"},
        generate_answer=True,
    )


async def test_source_versions_requires_view_and_download_permissions(monkeypatch):
    service, _repository = install_fakes(monkeypatch, allowed=True)
    monkeypatch.setattr(knowledge_router, "_ensure_database_supports_documents", AsyncMock())
    list_for_current_files = AsyncMock(return_value=[])
    monkeypatch.setattr(
        knowledge_router,
        "KnowledgeSourceVersionService",
        lambda: SimpleNamespace(list_for_current_files=list_for_current_files),
    )

    result = await knowledge_router.list_source_versions(
        "kb-1",
        knowledge_router.SourceVersionBatchRequest(file_ids=["file-1", "file-1"]),
        current_user=user(uid="viewer", role="user"),
    )

    assert result == {"items": []}
    assert service.calls == [
        ({"uid": "viewer", "role": "user", "department_id": 1}, "kb-1", "can_view"),
        ({"uid": "viewer", "role": "user", "department_id": 1}, "kb-1", "can_download"),
    ]
    list_for_current_files.assert_awaited_once_with(
        kb_id="kb-1",
        file_ids=["file-1", "file-1"],
    )


async def test_scoped_document_search_requires_manage_permission(monkeypatch):
    service, _repository = install_fakes(monkeypatch, allowed=False)

    with pytest.raises(HTTPException) as exc_info:
        await knowledge_router.search_documents_across_knowledge_bases(
            kb_id="kb-1",
            keyword="spec",
            updated_from=None,
            updated_to=None,
            publisher=None,
            page=1,
            page_size=30,
            current_user=user(uid="uploader", role="user"),
        )

    assert exc_info.value.status_code == 403
    assert service.calls == [
        ({"uid": "uploader", "role": "user", "department_id": 1, "team_id": None}, "kb-1", "can_manage")
    ]


async def test_scoped_document_search_only_queries_requested_database(monkeypatch):
    service, _repository = install_fakes(monkeypatch, allowed=True)
    monkeypatch.setattr(knowledge_router, "_ensure_database_supports_documents", AsyncMock())
    search = AsyncMock(return_value=([{"file_id": "file-1", "kb_id": "kb-1"}], 1))
    monkeypatch.setattr(knowledge_router, "KnowledgeFileRepository", lambda: SimpleNamespace(search_documents=search))

    result = await knowledge_router.search_documents_across_knowledge_bases(
        kb_id="kb-1",
        keyword="spec",
        updated_from=None,
        updated_to=None,
        publisher="owner",
        page=2,
        page_size=20,
        current_user=user(uid="manager", role="user"),
    )

    search.assert_awaited_once_with(
        kb_ids=["kb-1"],
        keyword="spec",
        updated_from=None,
        updated_to=None,
        created_by="owner",
        page=2,
        page_size=20,
    )
    assert service.calls == [
        ({"uid": "manager", "role": "user", "department_id": 1, "team_id": None}, "kb-1", "can_manage")
    ]
    assert result == {
        "items": [{"file_id": "file-1", "kb_id": "kb-1", "kb_name": "kb-1"}],
        "total": 1,
        "page": 2,
        "page_size": 20,
    }


async def test_unscoped_document_search_keeps_browsable_database_scope(monkeypatch):
    monkeypatch.setattr(
        knowledge_router,
        "_document_browse_kb_ids",
        AsyncMock(return_value=(["kb-1", "kb-2"], {"kb-1": "One", "kb-2": "Two"})),
    )
    search = AsyncMock(return_value=([{"file_id": "file-2", "kb_id": "kb-2"}], 1))
    monkeypatch.setattr(knowledge_router, "KnowledgeFileRepository", lambda: SimpleNamespace(search_documents=search))

    result = await knowledge_router.search_documents_across_knowledge_bases(
        kb_id=None,
        keyword="guide",
        updated_from=None,
        updated_to=None,
        publisher=None,
        page=1,
        page_size=30,
        current_user=user(uid="viewer", role="user"),
    )

    search.assert_awaited_once_with(
        kb_ids=["kb-1", "kb-2"],
        keyword="guide",
        updated_from=None,
        updated_to=None,
        created_by=None,
        page=1,
        page_size=30,
    )
    assert result["items"][0]["kb_name"] == "Two"


async def test_add_uploaded_documents_requires_upload_permission(monkeypatch):
    service, _repository = install_fakes(monkeypatch, allowed=False)
    payload = knowledge_router.AddUploadedDocumentsRequest(items=["minio://knowledgebases/kb-1/upload/a.md"], params={})

    with pytest.raises(HTTPException) as exc_info:
        await knowledge_router.add_uploaded_documents("kb-1", payload, current_user=user(uid="uploader"))

    assert exc_info.value.status_code == 403
    assert service.calls == [
        ({"uid": "uploader", "role": "admin", "department_id": 1, "team_id": None}, "kb-1", "can_upload")
    ]


async def test_get_folder_chain_requires_view_permission(monkeypatch):
    service, _repository = install_fakes(monkeypatch, allowed=False)

    with pytest.raises(HTTPException) as exc_info:
        await knowledge_router.get_folder_chain("kb-1", "folder-1", current_user=user(uid="viewer", role="user"))

    assert exc_info.value.status_code == 403
    assert service.calls == [
        ({"uid": "viewer", "role": "user", "department_id": 1, "team_id": None}, "kb-1", "can_view")
    ]


async def test_get_folder_chain_returns_top_down_chain(monkeypatch):
    service, _repository = install_fakes(monkeypatch, allowed=True)
    monkeypatch.setattr(knowledge_router, "_ensure_database_supports_documents", AsyncMock())
    get_folder_chain = AsyncMock(
        return_value=[
            {"file_id": "folder-a", "filename": "A"},
            {"file_id": "folder-b", "filename": "B"},
        ]
    )
    monkeypatch.setattr(
        knowledge_router, "KnowledgeFileRepository", lambda: SimpleNamespace(get_folder_chain=get_folder_chain)
    )

    result = await knowledge_router.get_folder_chain("kb-1", "folder-b", current_user=user(uid="viewer", role="user"))

    get_folder_chain.assert_awaited_once_with(kb_id="kb-1", folder_id="folder-b")
    assert service.calls == [
        ({"uid": "viewer", "role": "user", "department_id": 1, "team_id": None}, "kb-1", "can_view")
    ]
    assert result == {
        "folder_id": "folder-b",
        "chain": [{"file_id": "folder-a", "filename": "A"}, {"file_id": "folder-b", "filename": "B"}],
    }


async def test_get_folder_chain_returns_404_when_folder_missing(monkeypatch):
    install_fakes(monkeypatch, allowed=True)
    monkeypatch.setattr(knowledge_router, "_ensure_database_supports_documents", AsyncMock())
    get_folder_chain = AsyncMock(return_value=None)
    monkeypatch.setattr(
        knowledge_router, "KnowledgeFileRepository", lambda: SimpleNamespace(get_folder_chain=get_folder_chain)
    )

    with pytest.raises(HTTPException) as exc_info:
        await knowledge_router.get_folder_chain("kb-1", "missing", current_user=user(uid="viewer", role="user"))

    assert exc_info.value.status_code == 404
