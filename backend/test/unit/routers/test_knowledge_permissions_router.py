from __future__ import annotations

from types import SimpleNamespace

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


def user(uid="admin", role="admin", department_id=1):
    return SimpleNamespace(uid=uid, role=role, department_id=department_id)


async def test_list_database_permissions_requires_grant_permission(monkeypatch):
    install_fakes(monkeypatch, allowed=False)

    with pytest.raises(HTTPException) as exc_info:
        await knowledge_router.list_database_permissions("kb-1", current_user=user())

    assert exc_info.value.status_code == 403


async def test_list_database_permissions_returns_permission_matrix(monkeypatch):
    service, _repository = install_fakes(monkeypatch, allowed=True)

    result = await knowledge_router.list_database_permissions("kb-1", current_user=user(uid="owner"))

    assert service.calls == [({"uid": "owner", "role": "admin", "department_id": 1}, "kb-1", "can_grant")]
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


async def test_get_database_info_requires_view_permission(monkeypatch):
    service, _repository = install_fakes(monkeypatch, allowed=False)

    with pytest.raises(HTTPException) as exc_info:
        await knowledge_router.get_database_info("kb-1", current_user=user(uid="viewer"))

    assert exc_info.value.status_code == 403
    assert service.calls == [({"uid": "viewer", "role": "admin", "department_id": 1}, "kb-1", "can_view")]


async def test_update_database_info_requires_manage_permission(monkeypatch):
    service, _repository = install_fakes(monkeypatch, allowed=False)
    request = knowledge_router.UpdateDatabaseRequest(name="name", description="desc")

    with pytest.raises(HTTPException) as exc_info:
        await knowledge_router.update_database_info("kb-1", request, current_user=user(uid="manager"))

    assert exc_info.value.status_code == 403
    assert service.calls == [({"uid": "manager", "role": "admin", "department_id": 1}, "kb-1", "can_manage")]


async def test_list_documents_requires_view_permission(monkeypatch):
    service, _repository = install_fakes(monkeypatch, allowed=False)

    with pytest.raises(HTTPException) as exc_info:
        await knowledge_router.list_documents("kb-1", current_user=user(uid="viewer"))

    assert exc_info.value.status_code == 403
    assert service.calls == [({"uid": "viewer", "role": "admin", "department_id": 1}, "kb-1", "can_view")]


async def test_add_uploaded_documents_requires_upload_permission(monkeypatch):
    service, _repository = install_fakes(monkeypatch, allowed=False)
    payload = knowledge_router.AddUploadedDocumentsRequest(items=["minio://knowledgebases/kb-1/upload/a.md"], params={})

    with pytest.raises(HTTPException) as exc_info:
        await knowledge_router.add_uploaded_documents("kb-1", payload, current_user=user(uid="uploader"))

    assert exc_info.value.status_code == 403
    assert service.calls == [({"uid": "uploader", "role": "admin", "department_id": 1}, "kb-1", "can_upload")]
