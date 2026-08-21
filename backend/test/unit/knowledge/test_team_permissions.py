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


async def test_team_grant_matches_user_in_team():
    service = service_for(
        SimpleNamespace(kb_id="kb-1", created_by="owner", share_config=None),
        [permission("team", "55", can_view=True, can_search=True)],
    )
    user = {"role": "user", "uid": "lisi", "department_id": 10, "team_id": 55}

    assert await service.has_permission(user, "kb-1", "can_view") is True
    assert await service.has_permission(user, "kb-1", "can_search") is True
    assert await service.has_permission(user, "kb-1", "can_upload") is False


async def test_team_grant_does_not_match_other_team_or_teamless_user():
    service = service_for(
        SimpleNamespace(kb_id="kb-1", created_by="owner", share_config=None),
        [permission("team", "55", can_view=True)],
    )
    other_team_user = {"role": "user", "uid": "lisi", "department_id": 10, "team_id": 56}
    no_team_user = {"role": "user", "uid": "wangwu", "department_id": 10, "team_id": None}

    assert await service.has_permission(other_team_user, "kb-1", "can_view") is False
    assert await service.has_permission(no_team_user, "kb-1", "can_view") is False


async def test_team_grant_string_subject_id_matches_int_team_id():
    service = service_for(
        SimpleNamespace(kb_id="kb-1", created_by="owner", share_config=None),
        [permission("team", "55", can_download=True)],
    )

    assert (
        await service.has_permission(
            {"role": "user", "uid": "lisi", "department_id": 10, "team_id": 55},
            "kb-1",
            "can_download",
        )
        is True
    )


async def test_department_grant_still_covers_all_team_members():
    """引入团队后，部门兜底仍覆盖部门下所有人（含各团队成员）"""
    service = service_for(
        SimpleNamespace(kb_id="kb-1", created_by="owner", share_config=None),
        [permission("department", "10", can_upload=True)],
    )

    for team_id in (None, 55, 56):
        user = {"role": "user", "uid": "u", "department_id": 10, "team_id": team_id}
        assert await service.has_permission(user, "kb-1", "can_upload") is True


async def test_team_grant_does_not_affect_departmentless_share_config():
    """share_config 部门兜底不受团队授权影响"""
    service = service_for(
        SimpleNamespace(
            kb_id="kb-1",
            created_by="owner",
            share_config={"access_level": "department", "department_ids": [10], "user_uids": []},
        ),
        [permission("team", "55", can_view=True)],
    )
    user = {"role": "user", "uid": "lisi", "department_id": 10, "team_id": 99}

    assert await service.has_permission(user, "kb-1", "can_view") is True
