import pytest
from sqlalchemy import TextClause

from server.routers.feedback_dashboard_router import get_feedback_summary


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def scalar(self):
        return self.value


class _FakeDb:
    """按语句类型分流：原始 SQL（可评价基数）返回 scalar，其余（反馈行）返回 rows。"""

    def __init__(self, rows, evaluable_count=0):
        self.rows = rows
        self.evaluable_count = evaluable_count

    async def execute(self, query, *args, **kwargs):
        if isinstance(query, TextClause):
            return _ScalarResult(self.evaluable_count)
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
        ],
        evaluable_count=10,
    )

    result = await get_feedback_summary(agent_id=None, db=db, current_user=_FakeUser())

    # 4 条显式反馈（1 好评 + 3 差评）落在 10 条可评价基数内 → 未反馈 6 条计满意
    assert result.total_feedbacks == 4
    assert result.like_count == 1
    assert result.dislike_count == 3
    assert result.evaluable_count == 10
    assert result.silent_count == 6
    assert result.satisfaction_rate == 70.0
    assert result.participation_rate == 40.0
    assert result.legacy_unclassified_count == 1
    assert {item.code: item.count for item in result.reason_stats} == {
        "answer_incorrect": 1,
        "outdated": 1,
        "irrelevant": 0,
        "other": 0,
    }


@pytest.mark.asyncio
async def test_feedback_summary_cross_language_reasons_bucket_into_same_codes():
    """code 存储 + 历史中文/英文标签混存时，同一原因归入同一 code（统计跨语言一致）。"""
    db = _FakeDb(
        [
            ("dislike", "answer_incorrect"),
            ("dislike", "Answer is incorrect"),
            ("dislike", "答案有误\n补充说明"),
            ("dislike", "Answer is irrelevant"),
            ("dislike", "Information is outdated"),
            ("dislike", "旧版自由文本原因"),
        ],
        evaluable_count=20,
    )

    result = await get_feedback_summary(agent_id=None, db=db, current_user=_FakeUser())

    assert result.dislike_count == 6
    assert result.legacy_unclassified_count == 1
    assert {item.code: item.count for item in result.reason_stats} == {
        "answer_incorrect": 3,
        "outdated": 1,
        "irrelevant": 1,
        "other": 0,
    }


@pytest.mark.asyncio
async def test_feedback_summary_no_evaluable_answers_defaults_to_satisfied():
    result = await get_feedback_summary(agent_id=None, db=_FakeDb([]), current_user=_FakeUser())

    assert result.total_feedbacks == 0
    assert result.like_count == 0
    assert result.dislike_count == 0
    assert result.evaluable_count == 0
    assert result.silent_count == 0
    assert result.satisfaction_rate == 100.0
    assert result.participation_rate == 0.0


@pytest.mark.asyncio
async def test_feedback_summary_all_feedback_dislike():
    """无好评、全差评时，未反馈仍默认满意，不满率即显式差评占比。"""
    db = _FakeDb([("dislike", "答案有误")], evaluable_count=4)

    result = await get_feedback_summary(agent_id=None, db=db, current_user=_FakeUser())

    assert result.like_count == 0
    assert result.dislike_count == 1
    assert result.evaluable_count == 4
    assert result.silent_count == 3
    assert result.satisfaction_rate == 75.0
