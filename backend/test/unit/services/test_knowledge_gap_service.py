from __future__ import annotations

from yuxi.services.knowledge_gap_service import (
    KnowledgeGapAdminService,
    build_gap_identity,
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
