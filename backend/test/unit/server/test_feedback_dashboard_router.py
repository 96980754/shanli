import pytest

from server.routers.feedback_dashboard_router import get_feedback_summary


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _FakeDb:
    def __init__(self, rows):
        self.rows = rows

    async def execute(self, _query):
        return _FakeResult(self.rows)


class _FakeUser:
    uid = "admin"


@pytest.mark.asyncio
async def test_feedback_summary_counts_structured_and_legacy_reasons():
    db = _FakeDb(
        [
            ("like", None),
            ("dislike", "答案有误\n测试反馈原因统计"),
            ("dislike", "信息过时"),
            ("dislike", "旧版自由文本原因"),
        ]
    )

    result = await get_feedback_summary(agent_id=None, db=db, current_user=_FakeUser())

    assert result.total_feedbacks == 4
    assert result.like_count == 1
    assert result.dislike_count == 3
    assert result.satisfaction_rate == 25.0
    assert result.legacy_unclassified_count == 1
    assert {item.code: item.count for item in result.reason_stats} == {
        "answer_incorrect": 1,
        "outdated": 1,
        "irrelevant": 0,
        "other": 0,
    }


@pytest.mark.asyncio
async def test_feedback_summary_empty_data_keeps_existing_satisfaction_semantics():
    result = await get_feedback_summary(agent_id=None, db=_FakeDb([]), current_user=_FakeUser())

    assert result.total_feedbacks == 0
    assert result.like_count == 0
    assert result.dislike_count == 0
    assert result.satisfaction_rate == 100.0
