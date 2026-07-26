from types import SimpleNamespace

import pytest

import yuxi.services.uncovered_question_service as service


class _Record:
    def __init__(self, record_id: int = 1, status: str = "new"):
        self.record_id = record_id
        self.status = status

    def to_dict(self):
        return {
            "id": self.record_id,
            "question": "TEST-C100 的电池容量是多少？",
            "normalized_question": "test-c100 的电池容量是多少？",
            "question_hash": "hash",
            "uid": "user-1",
            "thread_id": "thread-1",
            "assistant_message_id": 42,
            "agent_id": "agent-1",
            "kb_ids": ["kb-1"],
            "reason": "low_relevance",
            "top_score": 0.5578,
            "score_type": "score",
            "status": self.status,
            "occurrence_count": 2,
            "first_seen_at": "2026-07-25T10:00:00",
            "last_seen_at": "2026-07-25T11:00:00",
            "resolved_at": None,
            "resolution_note": None,
        }


@pytest.mark.asyncio
async def test_list_uncovered_questions_view_passes_filters(monkeypatch):
    captured = {}

    class FakeRepository:
        def __init__(self, db):
            captured["db"] = db

        async def list_items(self, **kwargs):
            captured["kwargs"] = kwargs
            return [_Record()], 3

    monkeypatch.setattr(service, "UncoveredQuestionRepository", FakeRepository)

    result = await service.list_uncovered_questions_view(
        db=object(),
        status="new",
        agent_id=" agent-1 ",
        reason=" low_relevance ",
        query_text=" 电池容量 ",
        limit=20,
        offset=10,
    )

    assert result["total"] == 3
    assert result["limit"] == 20
    assert result["offset"] == 10
    assert result["items"][0]["id"] == 1
    assert captured["kwargs"] == {
        "status": "new",
        "agent_id": "agent-1",
        "reason": "low_relevance",
        "query_text": "电池容量",
        "limit": 20,
        "offset": 10,
    }


def test_list_uncovered_questions_view_rejects_invalid_status():
    with pytest.raises(ValueError, match="invalid status"):
        service._validate_status("closed")


@pytest.mark.asyncio
async def test_get_uncovered_question_view_raises_when_missing(monkeypatch):
    class FakeRepository:
        def __init__(self, db):
            pass

        async def get_by_id(self, question_id):
            return None

    monkeypatch.setattr(service, "UncoveredQuestionRepository", FakeRepository)

    with pytest.raises(LookupError, match="not found"):
        await service.get_uncovered_question_view(db=object(), question_id=999)


@pytest.mark.asyncio
async def test_update_uncovered_question_status_view(monkeypatch):
    captured = {}

    class FakeRepository:
        def __init__(self, db):
            captured["db"] = db

        async def update_status(self, **kwargs):
            captured["kwargs"] = kwargs
            return _Record(status=kwargs["status"])

    monkeypatch.setattr(service, "UncoveredQuestionRepository", FakeRepository)

    result = await service.update_uncovered_question_status_view(
        db=object(),
        question_id=1,
        status="resolved",
        resolution_note=" 已补充产品规格文档 ",
    )

    assert result["status"] == "resolved"
    assert captured["kwargs"] == {
        "question_id": 1,
        "status": "resolved",
        "resolution_note": "已补充产品规格文档",
    }


def test_resolution_note_length_is_limited():
    with pytest.raises(ValueError, match="must not exceed"):
        service._normalize_resolution_note("x" * 2001)
