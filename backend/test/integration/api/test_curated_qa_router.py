"""客服记录候选知识接口集成测试（审核页的删除语义）。

审核页上的「删除」是真删除：候选是待审草稿，不采纳就不留痕，因此这里断言的是
「行没了」，而不只是接口返回 200。
"""

from __future__ import annotations

import uuid

import pytest
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_udesk import CuratedQACandidate

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

CANDIDATES_PATH = "/api/dashboard/qa-candidates"


async def _require_superadmin(test_client, headers):
    response = await test_client.get("/api/auth/me", headers=headers)
    assert response.status_code == 200, response.text
    if response.json()["role"] != "superadmin":
        pytest.fail("This test requires TEST_USERNAME to be a superadmin account.")


async def _insert_candidate() -> int:
    """直接落一行待审候选，返回主键。审核页没有「新建候选」的入口，只能这样造数据。"""
    if not pg_manager._initialized:
        pg_manager.initialize()
    async with pg_manager.get_async_session_context() as db:
        candidate = CuratedQACandidate(
            source_conversation_id=f"pytest_{uuid.uuid4().hex[:12]}",
            question="pytest 候选问题？",
            normalized_question="pytest 候选问题?",
            question_hash=uuid.uuid4().hex,
            answer="pytest 候选答案。",
        )
        db.add(candidate)
        await db.commit()
        return candidate.id


async def _candidate_exists(candidate_id: int) -> bool:
    async with pg_manager.get_async_session_context() as db:
        return await db.get(CuratedQACandidate, candidate_id) is not None


async def test_delete_candidate_requires_authentication(test_client):
    response = await test_client.delete(f"{CANDIDATES_PATH}/1")
    assert response.status_code == 401


async def test_delete_candidate_forbids_standard_user(test_client, standard_user):
    response = await test_client.delete(f"{CANDIDATES_PATH}/1", headers=standard_user["headers"])
    assert response.status_code == 403


async def test_delete_candidate_returns_404_for_unknown_id(test_client, admin_headers):
    await _require_superadmin(test_client, admin_headers)

    response = await test_client.delete(f"{CANDIDATES_PATH}/2147483647", headers=admin_headers)

    assert response.status_code == 404, response.text


async def test_delete_candidate_removes_the_row(test_client, admin_headers):
    await _require_superadmin(test_client, admin_headers)
    candidate_id = await _insert_candidate()

    response = await test_client.delete(f"{CANDIDATES_PATH}/{candidate_id}", headers=admin_headers)

    assert response.status_code == 200, response.text
    assert response.json() == {"deleted": 1}
    # 不可恢复：再删一次是 404，说明行确实没了而不是被打了标记
    assert not await _candidate_exists(candidate_id)
    assert (await test_client.delete(f"{CANDIDATES_PATH}/{candidate_id}", headers=admin_headers)).status_code == 404
