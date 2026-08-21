from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from yuxi.repositories import team_repository as repo_module
from yuxi.repositories.team_repository import TeamRepository


pytestmark = pytest.mark.asyncio


class FakeResult:
    def __init__(self, rows=None):
        self._rows = rows if rows is not None else []

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None

    def scalar(self):
        return self._rows[0] if self._rows else None

    def scalars(self):
        return self

    def all(self):
        return self._rows


def team(id: int, department_id: int, name: str = "团队", is_default: bool = False):
    return SimpleNamespace(id=id, department_id=department_id, name=name, is_default=is_default)


class FakeSession:
    def __init__(self, responders):
        self.responders = list(responders)
        self.executed: list = []
        self.added: list = []
        self.deleted: list = []

    async def execute(self, statement):
        self.executed.append(statement)
        responder = self.responders.pop(0) if self.responders else FakeResult()
        return responder() if callable(responder) else responder

    def add(self, obj):
        self.added.append(obj)

    async def delete(self, obj):
        self.deleted.append(obj)


def install(session: FakeSession, monkeypatch: pytest.MonkeyPatch) -> None:
    @asynccontextmanager
    async def fake_session_context():
        yield session

    monkeypatch.setattr(repo_module.pg_manager, "get_async_session_context", fake_session_context)


async def test_create_first_team_auto_becomes_default(monkeypatch):
    """部门还没有默认团队时，新建团队自动成为默认团队"""
    session = FakeSession([FakeResult()])
    install(session, monkeypatch)

    created = await TeamRepository().create(1, "销售一组", None, is_default=False)

    assert created.is_default is True
    assert session.added == [created]


async def test_create_second_team_keeps_non_default(monkeypatch):
    """部门已有默认团队时，新建团队保持非默认"""
    session = FakeSession([FakeResult([1])])  # 默认团队存在 → 命中
    install(session, monkeypatch)

    created = await TeamRepository().create(1, "销售二组", None, is_default=False)

    assert created.is_default is False


async def test_delete_default_team_raises(monkeypatch):
    """默认团队不允许删除"""
    default = team(10, 1, "默认团队", is_default=True)
    session = FakeSession([FakeResult([default])])
    install(session, monkeypatch)

    with pytest.raises(ValueError):
        await TeamRepository().delete(10)

    assert session.deleted == []


async def test_delete_team_rehomes_users_cleans_permissions_then_deletes(monkeypatch):
    """删除非默认团队：先查团队、再查默认团队、迁移成员、清理遗留授权，最后删除"""
    default = team(10, 1, "默认团队", is_default=True)
    target = team(11, 1, "销售一组", is_default=False)
    session = FakeSession([FakeResult([target]), FakeResult([default]), FakeResult(), FakeResult()])
    install(session, monkeypatch)

    assert await TeamRepository().delete(11) is True
    assert session.deleted == [target]
    assert len(session.executed) == 4  # 两次 SELECT + 成员迁移 UPDATE + 授权清理 DELETE
    assert "DELETE FROM knowledge_base_permissions" in str(session.executed[3])


async def test_delete_missing_team_returns_false(monkeypatch):
    session = FakeSession([FakeResult()])
    install(session, monkeypatch)

    assert await TeamRepository().delete(99) is False


async def test_update_only_allows_name_and_description(monkeypatch):
    """update 只接受 name/description，忽略其余字段"""
    existing = team(11, 1, "旧名", is_default=False)
    session = FakeSession([FakeResult([existing])])
    install(session, monkeypatch)

    updated = await TeamRepository().update(11, {"name": "新名", "is_default": True})

    assert updated is existing
    assert existing.name == "新名"
    assert existing.is_default is False


async def test_set_members_adds_selected_and_reverts_unchecked(monkeypatch):
    """勾选的用户加入团队，取消勾选的原成员迁回默认团队"""
    core = team(11, 1, "核心组", is_default=False)
    default = team(10, 1, "默认团队", is_default=True)
    session = FakeSession(
        [FakeResult([core]), FakeResult([2]), FakeResult(), FakeResult([default]), FakeResult()]
    )
    install(session, monkeypatch)

    await TeamRepository().set_members(11, 1, [1, 2])

    assert len(session.executed) == 5  # 查团队 + 校验部门用户数 + 勾选加入 + 查默认团队 + 迁回
    assert "UPDATE users" in str(session.executed[2])  # 勾选用户加入核心组
    assert "UPDATE users" in str(session.executed[4])  # 其余原成员迁回默认团队


async def test_set_members_rejects_cross_department_users(monkeypatch):
    """user_ids 含跨部门用户时拒绝，且不执行任何更新"""
    core = team(11, 1, "核心组", is_default=False)
    session = FakeSession([FakeResult([core]), FakeResult([1])])  # 3 个目标只有 1 个属本部门
    install(session, monkeypatch)

    with pytest.raises(ValueError, match="存在不属于该部门的用户"):
        await TeamRepository().set_members(11, 1, [1, 2, 3])

    assert len(session.executed) == 2


async def test_set_members_empty_clears_team(monkeypatch):
    """全部取消勾选 → 团队清空，所有原成员迁回默认团队"""
    core = team(11, 1, "核心组", is_default=False)
    default = team(10, 1, "默认团队", is_default=True)
    session = FakeSession([FakeResult([core]), FakeResult([default]), FakeResult()])
    install(session, monkeypatch)

    await TeamRepository().set_members(11, 1, [])

    assert len(session.executed) == 3  # 查团队 + 查默认团队 + 全员迁回
    assert "UPDATE users" in str(session.executed[2])


async def test_set_members_default_team_keeps_members(monkeypatch):
    """对默认团队操作只把勾选用户纳入，不迁回任何人（兜底桶取消勾选不改归属）"""
    default = team(10, 1, "默认团队", is_default=True)
    session = FakeSession([FakeResult([default]), FakeResult([2]), FakeResult()])
    install(session, monkeypatch)

    await TeamRepository().set_members(10, 1, [1, 2])

    assert len(session.executed) == 3  # 查团队 + 校验部门用户数 + 勾选加入，无迁回
    assert "UPDATE users" in str(session.executed[2])


async def test_set_members_missing_or_foreign_team_raises(monkeypatch):
    """团队不存在或不属于该部门时抛错"""
    session = FakeSession([FakeResult()])  # 团队不存在
    install(session, monkeypatch)
    with pytest.raises(ValueError, match="团队不存在或不属于该部门"):
        await TeamRepository().set_members(99, 1, [1])

    foreign = team(11, 99, "他部门团队", is_default=False)  # 团队属于其他部门
    session2 = FakeSession([FakeResult([foreign])])
    install(session2, monkeypatch)
    with pytest.raises(ValueError, match="团队不存在或不属于该部门"):
        await TeamRepository().set_members(11, 1, [1])
