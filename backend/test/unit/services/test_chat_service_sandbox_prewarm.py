from __future__ import annotations

import asyncio

from yuxi.services import chat_service as svc


def _run_schedule(monkeypatch, *, thread_id, uid, meta) -> dict:
    captured: dict = {}

    async def fake_prewarm(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(svc, "_prewarm_sandbox", fake_prewarm)

    async def run():
        svc._schedule_sandbox_prewarm(thread_id=thread_id, uid=uid, meta=meta)
        # 让后台任务跑完
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    asyncio.run(run())
    return captured


def test_prewarm_scope_defaults_to_thread(monkeypatch):
    """普通对话未提供 file_thread/skills_thread 时，沙箱作用域默认等于 thread_id。"""
    captured = _run_schedule(monkeypatch, thread_id="t1", uid="u1", meta={"run_type": "chat"})
    assert captured == {
        "thread_id": "t1",
        "uid": "u1",
        "file_thread_id": "t1",
        "skills_thread_id": "t1",
    }


def test_prewarm_scope_uses_subagent_threads(monkeypatch):
    """子智能体运行使用 meta 中独立的 file_thread/skills_thread 作用域。"""
    captured = _run_schedule(
        monkeypatch,
        thread_id="parent-1",
        uid="u1",
        meta={"run_type": "subagent", "file_thread_id": "file-1", "skills_thread_id": "skill-1"},
    )
    assert captured == {
        "thread_id": "parent-1",
        "uid": "u1",
        "file_thread_id": "file-1",
        "skills_thread_id": "skill-1",
    }


def test_prewarm_sandbox_swallows_provider_errors(monkeypatch):
    """预冷失败只记 warning，不应影响本次运行。"""

    class _Boom:
        def get(self, *args, **kwargs):
            raise RuntimeError("provisioner unreachable")

    monkeypatch.setattr(
        "yuxi.agents.backends.sandbox.provider.get_sandbox_provider",
        lambda: _Boom(),
    )

    async def run():
        await svc._prewarm_sandbox(thread_id="t", uid="u", file_thread_id="t", skills_thread_id="t")

    # 不应抛异常
    asyncio.run(run())
