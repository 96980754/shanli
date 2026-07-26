from types import SimpleNamespace

import pytest

import yuxi.services.uncovered_question_service as service


def _conversation():
    return SimpleNamespace(
        uid="user-1",
        thread_id="thread-1",
        agent_id="agent-1",
    )


def _message(*, status="refused", reason="low_relevance", question="TEST-C100 的电池容量是多少？"):
    return SimpleNamespace(
        id=42,
        extra_metadata={
            "answer_status": status,
            "refusal_reason": reason,
            "knowledge_question": question,
            "knowledge_evidence": {
                "kb_ids": ["kb-b", "kb-a", "kb-a"],
                "top_score": 0.5577,
                "score_type": "score",
            },
        },
    )


def test_build_uncovered_question_data_normalizes_and_deduplicates():
    data = service.build_uncovered_question_data(
        conversation=_conversation(),
        assistant_message=_message(question="  TEST-C100   的电池容量是多少？  "),
    )

    assert data is not None
    assert data["question"] == "TEST-C100   的电池容量是多少？"
    assert data["normalized_question"] == "test-c100 的电池容量是多少？"
    assert data["kb_ids"] == ["kb-a", "kb-b"]
    assert data["reason"] == "low_relevance"
    assert data["top_score"] == pytest.approx(0.5577)
    assert len(data["question_hash"]) == 64
    assert len(data["kb_scope_hash"]) == 64


def test_build_uncovered_question_data_ignores_answered_message():
    assert (
        service.build_uncovered_question_data(
            conversation=_conversation(),
            assistant_message=_message(status="answered", reason=None),
        )
        is None
    )


def test_build_uncovered_question_data_ignores_system_error():
    assert (
        service.build_uncovered_question_data(
            conversation=_conversation(),
            assistant_message=_message(status="refused", reason="retrieval_error"),
        )
        is None
    )


@pytest.mark.asyncio
async def test_record_uncovered_question_calls_repository(monkeypatch):
    captured = {}
    expected = SimpleNamespace(id=7, occurrence_count=1)

    class FakeRepository:
        def __init__(self, db):
            captured["db"] = db

        async def upsert_occurrence(self, data):
            captured["data"] = data
            return expected

    monkeypatch.setattr(service, "UncoveredQuestionRepository", FakeRepository)

    db = object()
    result = await service.record_uncovered_question(
        db=db,
        conversation=_conversation(),
        assistant_message=_message(),
    )

    assert result is expected
    assert captured["db"] is db
    assert captured["data"]["assistant_message_id"] == 42
    assert captured["data"]["agent_id"] == "agent-1"
