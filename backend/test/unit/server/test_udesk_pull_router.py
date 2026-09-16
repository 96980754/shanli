"""Udesk 状态/触发接口单测。

- 触发：「配置不齐必须当场拒绝」，而不是排一个必然空跑的任务。
  `pull_service._build_service()` 在凭证不齐时返回 None，任务入队后静默空跑、页面上
  只有一句「已排队」——审核页点了看不出任何结果即由此而来。
- 状态：租约列是 TIMESTAMPTZ（读回 aware），与它比较的时间也必须是 aware；且拉取
  与总结两段进度都要如实回传。
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi import HTTPException

from server.routers.curated_qa_router import get_udesk_status, trigger_udesk_pull
from yuxi.storage.postgres.models_udesk import UdeskSyncState
from yuxi.utils.datetime_utils import utc_now

pytestmark = [pytest.mark.asyncio, pytest.mark.unit]


@pytest.fixture()
def udesk_credentials(monkeypatch):
    """显式给出凭证四项（清空运行时快照 + 设环境变量），不受本机已保存设置影响。

    open_api_token 由各用例单独决定——它正是本文件要验证的那一项。
    """
    from yuxi.config.app import config

    for key in ("udesk_enabled", "udesk_subdomain", "udesk_email"):
        monkeypatch.setattr(config, key, None if key == "udesk_enabled" else "", raising=False)
    for name in ("UDESK_ENABLED", "UDESK_SUBDOMAIN", "UDESK_EMAIL", "UDESK_OPEN_API_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("UDESK_ENABLED", "true")
    monkeypatch.setenv("UDESK_SUBDOMAIN", "acme")
    monkeypatch.setenv("UDESK_EMAIL", "admin@example.com")


async def test_rejects_missing_credentials(udesk_credentials):
    with pytest.raises(HTTPException) as exc:
        await trigger_udesk_pull(current_user=None)

    assert exc.value.status_code == 422
    assert exc.value.detail["missing_fields"] == ["open_api_token"]


async def test_queues_pull_when_configured(udesk_credentials, monkeypatch):
    monkeypatch.setenv("UDESK_OPEN_API_TOKEN", "tok-test")
    enqueued: list[tuple[str, str]] = []

    class _Queue:
        async def enqueue_job(self, function: str, *, _job_id: str):
            enqueued.append((function, _job_id))
            return type("Job", (), {"job_id": _job_id})()

    async def _fake_pool():
        return _Queue()

    monkeypatch.setattr("yuxi.services.run_queue_service.get_arq_pool", _fake_pool)

    result = await trigger_udesk_pull(current_user=None)

    assert result["queued"] is True
    assert enqueued and enqueued[0][0] == "run_scheduled_pull"
    # 固定 job_id，连点才能被判重。此前用 `udesk-pull:{随机}`，arq 从不去重，
    # 连点会排出一串无用任务：真正挡重复的是服务里的 DB 租约，那些任务只会空跑一遍
    # 并各自去动状态行，409 分支也就永远走不到。
    assert enqueued[0][1] == "udesk-pull:manual"


async def test_rejects_duplicate_pull_when_job_already_queued(udesk_credentials, monkeypatch):
    """连点「立即拉取」：固定 job_id 已被占用时 arq 返回 None，接口回 409 而不是再排一个。"""
    monkeypatch.setenv("UDESK_OPEN_API_TOKEN", "tok-test")

    class _Queue:
        async def enqueue_job(self, function: str, *, _job_id: str):
            return None  # arq 对已存在的 job_id 就是返回 None

    async def _fake_pool():
        return _Queue()

    monkeypatch.setattr("yuxi.services.run_queue_service.get_arq_pool", _fake_pool)

    with pytest.raises(HTTPException) as exc:
        await trigger_udesk_pull(current_user=None)

    assert exc.value.status_code == 409


# ----------------------------------------------------------------- 状态接口
class _FakeResult:
    def __init__(self, value):
        self._value = value

    def one(self):
        return self._value

    def scalar_one_or_none(self):
        return self._value


class _FakeDb:
    """按调用顺序吐出结果：累计量五连 COUNT → 拉取状态行 → 总结状态行。"""

    def __init__(self, *results):
        self._results = list(results)

    async def execute(self, statement):
        return self._results.pop(0)


async def test_status_reports_running_pull_while_lease_is_held(udesk_credentials):
    """A 的回归：租约是 TIMESTAMPTZ，读回来是 aware，曾与 naive 的 utc_now_naive()
    比较抛 TypeError——而租约只在拉取进行中非空，于是状态接口恰好在「最需要看进度」
    的那几分钟里 500，前端 catch 后状态栏整个消失。

    同时钉住返回体形状：拉取与总结各自的进度、累计量三段都必须在。
    """
    state = UdeskSyncState(
        id=1,
        lease_expires_at=utc_now() + timedelta(minutes=5),
        progress_done=3,
        progress_total=7,
        last_run_status="running",
    )
    db = _FakeDb(_FakeResult((39, 522, 4, 1, 12)), _FakeResult(state), _FakeResult(state))

    payload = await get_udesk_status(db=db, current_user=None)

    assert payload["pull"]["running"] is True
    assert payload["pull"]["done"] == 3 and payload["pull"]["total"] == 7
    assert payload["pull"]["last_run_status"] == "running"
    # 候选两个口径：累计（采纳/拒绝不删行）与待审（列表默认过滤条件），
    # 只报累计会与下方列表条数对不上。
    assert payload["counts"] == {
        "conversations": 39,
        "messages": 522,
        "candidates": 4,
        "candidates_pending": 1,
    }
    # 总结进度由会话总数与待总结数现算，天然单调、不依赖批大小
    assert payload["summarize"]["done"] == 27 and payload["summarize"]["total"] == 39
    assert payload["summarize"]["pending"] == 12
    assert payload["summarize"]["running"] is False


async def test_status_marks_summarize_running_and_surfaces_last_error(udesk_credentials):
    state = UdeskSyncState(
        id=1,
        lease_expires_at=utc_now() - timedelta(minutes=1),  # 上一轮已结束
        summarize_lease_expires_at=utc_now() + timedelta(minutes=10),
        summarize_status="running",
        summarize_last_error=None,
        last_run_status="failed",
        last_error="UdeskPullError: 接口不可用",
        last_run_conversations=8,
        last_run_messages=0,
    )
    db = _FakeDb(_FakeResult((39, 522, 4, 0, 0)), _FakeResult(state), _FakeResult(state))

    payload = await get_udesk_status(db=db, current_user=None)

    # 两个租约互相独立：总结在跑不代表拉取在跑
    assert payload["pull"]["running"] is False and payload["summarize"]["running"] is True
    assert payload["pull"]["last_error"] == "UdeskPullError: 接口不可用"
    assert payload["pull"]["last_run_conversations"] == 8
    assert payload["pull"]["last_run_messages"] == 0
    assert payload["summarize"]["pending"] == 0 and payload["summarize"]["done"] == 39
