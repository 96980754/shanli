from __future__ import annotations

from types import SimpleNamespace

import pytest

from yuxi.permissions.knowledge import KnowledgePermissionService


pytestmark = pytest.mark.asyncio


class FakeKnowledgeBaseRepository:
    def __init__(self, kb=None):
        self.kb = kb

    async def get_by_kb_id(self, kb_id: str):
        if self.kb and self.kb.kb_id == kb_id:
            return self.kb
        return None


class FakeKnowledgePermissionRepository:
    def __init__(self, permissions=None):
        self.permissions = permissions or []

    async def list_by_kb_id(self, kb_id: str):
        return [permission for permission in self.permissions if permission.kb_id == kb_id]


def permission(subject_type: str, subject_id: str, **flags):
    defaults = {
        "can_view": False,
        "can_search": False,
        "can_upload": False,
        "can_download": False,
        "can_delete": False,
        "can_manage": False,
        "can_grant": False,
        "can_export": False,
    }
    defaults.update(flags)
    return SimpleNamespace(kb_id="kb-1", subject_type=subject_type, subject_id=subject_id, **defaults)


def service_for(kb, permissions=None):
    return KnowledgePermissionService(
        kb_repository=FakeKnowledgeBaseRepository(kb),
        permission_repository=FakeKnowledgePermissionRepository(permissions),
    )


async def test_admin_has_all_knowledge_permissions():
    service = service_for(SimpleNamespace(kb_id="kb-1", created_by="owner", share_config=None))

    permissions = await service.effective_permissions({"role": "admin", "uid": "department-admin"}, "kb-1")

    assert permissions.can_download is True
    assert permissions.can_manage is True
    assert permissions.can_grant is True


async def test_superadmin_has_all_knowledge_permissions():
    service = service_for(SimpleNamespace(kb_id="kb-1", created_by="owner", share_config=None))

    assert await service.has_permission({"role": "superadmin", "uid": "root"}, "kb-1", "can_export") is True


async def test_knowledge_base_creator_has_all_permissions():
    service = service_for(SimpleNamespace(kb_id="kb-1", created_by="owner", share_config=None))

    assert await service.has_permission({"role": "user", "uid": "owner"}, "kb-1", "can_grant") is True


async def test_user_department_and_role_grants_are_merged():
    service = service_for(
        SimpleNamespace(kb_id="kb-1", created_by="owner", share_config=None),
        [
            permission("user", "zhangsan", can_view=True),
            permission("department", "10", can_upload=True),
            permission("role", "knowledge_manager", can_manage=True),
        ],
    )
    user = {
        "role": "user",
        "roles": ["knowledge_manager"],
        "uid": "zhangsan",
        "department_id": 10,
    }

    assert await service.has_permission(user, "kb-1", "can_view") is True
    assert await service.has_permission(user, "kb-1", "can_upload") is True
    assert await service.has_permission(user, "kb-1", "can_manage") is True
    assert await service.has_permission(user, "kb-1", "can_delete") is False


async def test_default_is_deny_when_no_grant_matches():
    service = service_for(
        SimpleNamespace(kb_id="kb-1", created_by="owner", share_config=None),
        [permission("department", "20", can_view=True)],
    )

    assert await service.has_permission({"role": "user", "uid": "lisi", "department_id": 10}, "kb-1", "can_view") is False


async def test_share_config_remains_view_and_search_compatibility_fallback():
    service = service_for(
        SimpleNamespace(
            kb_id="kb-1",
            created_by="owner",
            share_config={"access_level": "department", "department_ids": [10], "user_uids": []},
        )
    )
    user = {"role": "user", "uid": "lisi", "department_id": 10}

    assert await service.has_permission(user, "kb-1", "can_view") is True
    assert await service.has_permission(user, "kb-1", "can_search") is True
    assert await service.has_permission(user, "kb-1", "can_upload") is False
