from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest

from yuxi.services import feedback_service as svc


class _FakeResult:
    def __init__(self, value):
        self.value = value

    def scalar_one_or_none(self):
        return self.value


class _FakeSession:
    def __init__(self, results):
        self.results = list(results)
        self.added = []
        self.committed = False
        self.rolled_back = False

    async def execute(self, _query):
        return _FakeResult(self.results.pop(0))

    def add(self, item):
        self.added.append(item)

    async def commit(self):
        self.committed = True

    async def refresh(self, item):
        item.id = 9
        item.created_at = datetime(2026, 1, 2, 3, 4, 5)

    async def rollback(self):
        self.rolled_back = True


@pytest.mark.asyncio
async def test_submit_message_feedback_syncs_langfuse_score(monkeypatch: pytest.MonkeyPatch):
    message = SimpleNamespace(
        id=3,
        conversation_id=7,
        extra_metadata={"langfuse_trace_id": "trace-1"},
    )
    conversation = SimpleNamespace(id=7, uid="user-1")
    db = _FakeSession([message, conversation, None])
    calls = []

    monkeypatch.setattr(svc, "submit_user_feedback_score", lambda **kwargs: calls.append(kwargs) or True)

    result = await svc.submit_message_feedback_view(
        message_id=3,
        rating="like",
        reason=None,
        db=db,
        current_uid="user-1",
    )

    assert result == {
        "id": 9,
        "message_id": 3,
        "rating": "like",
        "reason": None,
        "created_at": "2026-01-02T03:04:05",
    }
    assert db.committed is True
    assert db.rolled_back is False
    assert calls == [
        {
            "trace_id": "trace-1",
            "feedback_id": 9,
            "message_id": 3,
            "conversation_id": 7,
            "uid": "user-1",
            "rating": "like",
            "reason": None,
        }
    ]


@pytest.mark.asyncio
async def test_submit_message_feedback_skips_langfuse_without_trace_id(monkeypatch: pytest.MonkeyPatch):
    message = SimpleNamespace(id=3, conversation_id=7, extra_metadata={})
    conversation = SimpleNamespace(id=7, uid="user-1")
    db = _FakeSession([message, conversation, None])
    calls = []

    monkeypatch.setattr(svc, "submit_user_feedback_score", lambda **kwargs: calls.append(kwargs) or True)

    result = await svc.submit_message_feedback_view(
        message_id=3,
        rating="dislike",
        reason="不相关",
        db=db,
        current_uid="user-1",
    )

    assert result["rating"] == "dislike"
    assert result["reason"] == "不相关"
    assert calls == []


def test_parse_feedback_reason_with_detail():
    assert svc.parse_feedback_reason("答案有误\n测试反馈原因统计") == {
        "reason_code": "answer_incorrect",
        "reason_label": "答案有误",
        "reason_detail": "测试反馈原因统计",
    }


def test_parse_feedback_reason_without_detail():
    assert svc.parse_feedback_reason("信息过时") == {
        "reason_code": "outdated",
        "reason_label": "信息过时",
        "reason_detail": None,
    }


def test_parse_feedback_reason_keeps_legacy_free_text():
    assert svc.parse_feedback_reason("旧版自由文本原因") == {
        "reason_code": None,
        "reason_label": "历史反馈",
        "reason_detail": "旧版自由文本原因",
    }


def test_parse_feedback_reason_code_form():
    assert svc.parse_feedback_reason("answer_incorrect") == {
        "reason_code": "answer_incorrect",
        "reason_label": "答案有误",
        "reason_detail": None,
    }


def test_parse_feedback_reason_code_form_with_detail():
    assert svc.parse_feedback_reason("answer_incorrect\n我想要的答案和实际不符合") == {
        "reason_code": "answer_incorrect",
        "reason_label": "答案有误",
        "reason_detail": "我想要的答案和实际不符合",
    }


def test_parse_feedback_reason_code_form_with_blank_detail():
    assert svc.parse_feedback_reason("other\n   ")["reason_detail"] is None


def test_parse_feedback_reason_en_alias_buckets_into_code():
    # 历史英文界面存库标签 → 归入正确 code，保证统计跨语言一致
    assert svc.parse_feedback_reason("Answer is incorrect") == {
        "reason_code": "answer_incorrect",
        "reason_label": "答案有误",
        "reason_detail": None,
    }


def test_parse_feedback_reason_en_alias_with_detail():
    assert svc.parse_feedback_reason("Answer is irrelevant\ntoo vague") == {
        "reason_code": "irrelevant",
        "reason_label": "答非所问",
        "reason_detail": "too vague",
    }


def test_parse_feedback_reason_empty():
    assert svc.parse_feedback_reason(None) == {
        "reason_code": None,
        "reason_label": None,
        "reason_detail": None,
    }
    assert svc.parse_feedback_reason("  ") == {
        "reason_code": None,
        "reason_label": None,
        "reason_detail": None,
    }


def test_build_satisfaction_stats_unreplied_counts_as_satisfied():
    stats = svc.build_satisfaction_stats(evaluable_count=10, like_count=1, dislike_count=3)

    assert stats["evaluable_count"] == 10
    assert stats["like_count"] == 1
    assert stats["dislike_count"] == 3
    assert stats["silent_count"] == 6
    # (1 好评 + 6 未反馈) / 10
    assert stats["satisfaction_rate"] == 70.0
    assert stats["participation_rate"] == 40.0


def test_build_satisfaction_stats_no_evaluable_defaults_to_100():
    stats = svc.build_satisfaction_stats(evaluable_count=0, like_count=0, dislike_count=0)

    assert stats["silent_count"] == 0
    assert stats["satisfaction_rate"] == 100.0
    assert stats["participation_rate"] == 0.0


def test_build_satisfaction_stats_silent_never_negative():
    stats = svc.build_satisfaction_stats(evaluable_count=2, like_count=1, dislike_count=2)

    assert stats["silent_count"] == 0
    assert stats["satisfaction_rate"] == 50.0
