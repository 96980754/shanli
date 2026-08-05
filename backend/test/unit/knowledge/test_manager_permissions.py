from __future__ import annotations

from types import SimpleNamespace

import pytest

from yuxi.knowledge.manager import KnowledgeBaseManager
from yuxi.permissions import knowledge as permission_module


pytestmark = pytest.mark.asyncio


async def test_get_databases_by_user_includes_explicit_view_grant(monkeypatch, tmp_path):
    manager = KnowledgeBaseManager(str(tmp_path))
    databases = [
        {
            "kb_id": "kb-granted",
            "created_by": "owner",
            "share_config": {"access_level": "user", "department_ids": [], "user_uids": []},
        },
        {
            "kb_id": "kb-private",
            "created_by": "owner",
            "share_config": {"access_level": "user", "department_ids": [], "user_uids": []},
        },
    ]

    async def get_databases(category_id=None):
        assert category_id is None
        return {"databases": databases}

    class FakePermissionService:
        def __init__(self):
            self.calls = []

        async def has_permission(self, user, kb_id, action):
            self.calls.append((user, kb_id, action))
            return kb_id == "kb-granted" and action == "can_view"

    permission_service = FakePermissionService()
    monkeypatch.setattr(manager, "get_databases", get_databases)
    monkeypatch.setattr(permission_module, "KnowledgePermissionService", lambda: permission_service)

    result = await manager.get_databases_by_user(
        SimpleNamespace(uid="viewer", role="user", department_id=10)
    )

    assert result == {"databases": [databases[0]]}
    assert permission_service.calls == [
        ({"uid": "viewer", "role": "user", "department_id": 10}, "kb-granted", "can_view"),
        ({"uid": "viewer", "role": "user", "department_id": 10}, "kb-private", "can_view"),
    ]
