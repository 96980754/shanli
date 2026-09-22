"""对话错误落库契约：error_message 是给用户看的说明，不能拿内部枚举或异常原文兜底。

对应 docs/vibe/2026-09-22-error-message-usability.md 的 A1。
"""

import os
import sys

import pytest

sys.path.insert(0, os.getcwd())

from yuxi.services.chat_service import save_partial_message

pytestmark = [pytest.mark.asyncio, pytest.mark.unit]


class _FakeConvRepo:
    """只记录落库参数，避免单测依赖数据库。"""

    def __init__(self):
        self.saved = None

    async def add_message_by_thread_id(self, **kwargs):
        self.saved = kwargs
        return {"id": 1, **kwargs}


async def test_error_message_stays_empty_when_not_provided():
    """没有给用户看的说明时留空，不能让前端拿 error_type（内部枚举）当文案。"""
    repo = _FakeConvRepo()

    await save_partial_message(repo, "thread-1", error_type="unexpected_error")

    assert repo.saved["extra_metadata"]["error_message"] is None
    assert repo.saved["extra_metadata"]["error_type"] == "unexpected_error"
    assert repo.saved["extra_metadata"]["is_error"] is True
    assert repo.saved["content"] == ""


async def test_error_message_is_passed_through_unchanged():
    repo = _FakeConvRepo()

    await save_partial_message(repo, "thread-1", error_message="输入内容包含敏感词", error_type="content_guard_blocked")

    assert repo.saved["extra_metadata"]["error_message"] == "输入内容包含敏感词"


async def test_trace_info_does_not_override_error_fields():
    repo = _FakeConvRepo()

    await save_partial_message(
        repo,
        "thread-1",
        error_type="resume_error",
        trace_info={"trace_id": "abc"},
    )

    metadata = repo.saved["extra_metadata"]
    assert metadata["trace_id"] == "abc"
    assert metadata["error_type"] == "resume_error"
    assert metadata["error_message"] is None
