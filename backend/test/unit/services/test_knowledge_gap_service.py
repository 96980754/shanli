from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from yuxi.services.knowledge_gap_service import (
    KnowledgeGapAdminService,
    annotate_gap_has_answer,
    build_gap_identity,
    load_answered_gap_ids,
    normalize_kb_scope,
    normalize_question,
)


def test_gap_identity_is_stable_for_question_and_kb_order():
    first = build_gap_identity("  TEST-C100   是否支持 SIP？ ", "assistant", ["kb-b", "kb-a", "kb-a"])
    second = build_gap_identity("test-c100 是否支持 sip？", "assistant", ["kb-a", "kb-b"])

    assert normalize_question("  A   B ") == "a b"
    assert normalize_kb_scope([" b ", "a", "b"]) == ["a", "b"]
    assert first["question_hash"] == second["question_hash"]
    assert first["kb_scope"] == ["kb-a", "kb-b"]
    assert first["kb_scope_hash"] == second["kb_scope_hash"]


def test_gap_admin_validation():
    service = KnowledgeGapAdminService()

    assert service.validate_status("processing") == "processing"
    assert service.normalize_note("  已补充文档  ") == "已补充文档"
    assert service.normalize_note("") is None


def test_annotate_gap_has_answer_by_assistant_message_id():
    gap = {"id": 1, "assistant_message_id": 99, "status": "new"}

    assert annotate_gap_has_answer(dict(gap), {99})["has_answer"] is True
    assert annotate_gap_has_answer(dict(gap), {98})["has_answer"] is False
    assert annotate_gap_has_answer({**gap, "assistant_message_id": None}, {99})["has_answer"] is False
    assert annotate_gap_has_answer({**gap, "assistant_message_id": 99}, set())["has_answer"] is False


async def test_load_answered_gap_ids_returns_qa_source_message_ids():
    session = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(all=lambda: [(5,), (8,)])))

    answered = await load_answered_gap_ids(session, [5, 8, None])

    assert answered == {5, 8}
    session.execute.assert_awaited_once()


async def test_load_answered_gap_ids_skips_query_when_no_ids():
    session = SimpleNamespace(execute=AsyncMock())

    assert await load_answered_gap_ids(session, []) == set()
    session.execute.assert_not_awaited()
