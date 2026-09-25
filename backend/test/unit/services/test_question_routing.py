"""question_routing 单测：规则分档、judge 兜底、路由优先级与 thread 惯性。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import yuxi.services.question_routing as question_routing
from yuxi.services.question_routing import (
    classify_complexity,
    route_question_model_spec,
)


def _medium_question() -> str:
    # 31~120 字之间且不含复杂意图词，用于把判定推进到 judge 层
    return "请问调度台的账户体系里，一个部门下面最多可以挂多少个子账号，超出了应该找谁扩容" * 1


class _NoHistoryRepo:
    def __init__(self, db):
        del db

    async def get_latest_run_by_thread_for_user(self, thread_id: str, uid: str):
        del thread_id, uid
        return None


class _LastRunRepo:
    def __init__(self, db, last_run=None):
        del db
        self.last_run = last_run

    async def get_latest_run_by_thread_for_user(self, thread_id: str, uid: str):
        del thread_id, uid
        return self.last_run


# ---------- classify_complexity：规则层 ----------


@pytest.mark.asyncio
async def test_classify_attachment_or_image_is_complex():
    assert (await classify_complexity("这是什么", has_attachment=True))["complexity"] == "complex"
    assert (await classify_complexity("这是什么", has_image=True))["complexity"] == "complex"
    assert (await classify_complexity("这是什么", has_attachment=True))["tier"] == "rule"


@pytest.mark.asyncio
async def test_classify_empty_question_is_complex():
    verdict = await classify_complexity("   ")
    assert verdict == {"complexity": "complex", "tier": "rule", "reason": "空问题"}


@pytest.mark.asyncio
async def test_classify_long_question_is_complex():
    verdict = await classify_complexity("字" * (question_routing.COMPLEX_MIN_CHARS + 1))
    assert verdict["complexity"] == "complex"
    assert verdict["tier"] == "rule"


@pytest.mark.asyncio
async def test_classify_intent_term_is_complex():
    verdict = await classify_complexity("对比一下 F10 和 P10")
    assert verdict["complexity"] == "complex"
    assert verdict["tier"] == "rule"
    assert "对比" in verdict["reason"]


@pytest.mark.asyncio
async def test_classify_short_plain_question_is_simple():
    verdict = await classify_complexity("f10 的价格是多少")
    assert verdict["complexity"] == "simple"
    assert verdict["tier"] == "rule"


# ---------- classify_complexity：judge 层 ----------


@pytest.mark.asyncio
async def test_classify_judge_disabled_defaults_complex(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(question_routing, "QUESTION_ROUTE_JUDGE_MODEL", "")
    verdict = await classify_complexity(_medium_question())
    assert verdict == {"complexity": "complex", "tier": "fallback", "reason": "分档判定不可用，按复杂处理"}


@pytest.mark.asyncio
async def test_classify_judge_returns_simple():
    async def caller(messages):
        del messages
        return '{"complexity": "simple"}'

    verdict = await classify_complexity(_medium_question(), caller=caller)
    assert verdict["complexity"] == "simple"
    assert verdict["tier"] == "llm"


@pytest.mark.asyncio
async def test_classify_judge_tolerates_wrapped_json():
    async def caller(messages):
        del messages
        return '好的，结果如下：\n{"complexity": "complex"}\n以上。'

    verdict = await classify_complexity(_medium_question(), caller=caller)
    assert verdict["complexity"] == "complex"
    assert verdict["tier"] == "llm"


@pytest.mark.asyncio
async def test_classify_judge_garbage_or_invalid_defaults_complex():
    async def garbage(messages):
        del messages
        return "模型输出不是 JSON"

    async def invalid(messages):
        del messages
        return '{"complexity": "huh"}'

    for caller in (garbage, invalid):
        verdict = await classify_complexity(_medium_question(), caller=caller)
        assert verdict["complexity"] == "complex"
        assert verdict["tier"] == "fallback"


@pytest.mark.asyncio
async def test_classify_judge_exception_defaults_complex():
    async def broken(messages):
        del messages
        raise RuntimeError("judge down")

    verdict = await classify_complexity(_medium_question(), caller=broken)
    assert verdict["complexity"] == "complex"
    assert verdict["tier"] == "fallback"


# ---------- route_question_model_spec：优先级与惯性 ----------


def _agent_item(config: dict | None = None):
    return SimpleNamespace(config_json={"context": config or {}})


class _Backend:
    class context_schema:  # noqa: N801 — 仿 agent_run_service 测试的 _FakeBackend 形状
        def __init__(self):
            self.model = ""
            self.model_simple = ""

        def update_from_dict(self, data: dict):
            for key, value in data.items():
                if hasattr(self, key):
                    setattr(self, key, value)


def _patch_model_cache(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        question_routing.model_cache,
        "get_model_info",
        lambda spec: SimpleNamespace(model_type="chat") if spec == "fast-1" else None,
    )


@pytest.mark.asyncio
async def test_route_explicit_model_spec_bypasses_routing(monkeypatch: pytest.MonkeyPatch):
    _patch_model_cache(monkeypatch)
    spec, route = await route_question_model_spec(
        explicit_model_spec="claude-x",
        base_spec="base",
        agent_item=_agent_item({"model_simple": "fast-1"}),
        agent_backend=_Backend(),
        question="hello",
        has_image=False,
        has_attachment=False,
        thread_id="t",
        uid="u",
        db=None,
    )
    assert spec == "base"
    assert route is None


@pytest.mark.asyncio
async def test_route_disabled_when_model_simple_empty(monkeypatch: pytest.MonkeyPatch):
    _patch_model_cache(monkeypatch)
    spec, route = await route_question_model_spec(
        explicit_model_spec=None,
        base_spec="base",
        agent_item=_agent_item({}),
        agent_backend=_Backend(),
        question="hello",
        has_image=False,
        has_attachment=False,
        thread_id="t",
        uid="u",
        db=None,
    )
    assert spec == "base"
    assert route is None


@pytest.mark.asyncio
async def test_route_invalid_model_simple_raises(monkeypatch: pytest.MonkeyPatch):
    _patch_model_cache(monkeypatch)
    with pytest.raises(HTTPException) as exc_info:
        await route_question_model_spec(
            explicit_model_spec=None,
            base_spec="base",
            agent_item=_agent_item({"model_simple": "missing-model"}),
            agent_backend=_Backend(),
            question="hello",
            has_image=False,
            has_attachment=False,
            thread_id="t",
            uid="u",
            db=None,
        )
    assert exc_info.value.status_code == 422


@pytest.mark.asyncio
async def test_route_simple_question_uses_model_simple(monkeypatch: pytest.MonkeyPatch):
    _patch_model_cache(monkeypatch)
    monkeypatch.setattr(question_routing, "AgentRunRepository", _NoHistoryRepo)
    spec, route = await route_question_model_spec(
        explicit_model_spec=None,
        base_spec="base",
        agent_item=_agent_item({"model_simple": "fast-1"}),
        agent_backend=_Backend(),
        question="f10 的价格是多少",
        has_image=False,
        has_attachment=False,
        thread_id="t",
        uid="u",
        db=None,
    )
    assert spec == "fast-1"
    assert route == {"complexity": "simple", "tier": "rule", "reason": "短问题且无复杂信号"}


@pytest.mark.asyncio
async def test_route_complex_question_keeps_base_spec(monkeypatch: pytest.MonkeyPatch):
    _patch_model_cache(monkeypatch)
    monkeypatch.setattr(question_routing, "AgentRunRepository", _NoHistoryRepo)
    spec, route = await route_question_model_spec(
        explicit_model_spec=None,
        base_spec="base",
        agent_item=_agent_item({"model_simple": "fast-1"}),
        agent_backend=_Backend(),
        question="hello",
        has_image=False,
        has_attachment=True,
        thread_id="t",
        uid="u",
        db=None,
    )
    assert spec == "base"
    assert route["complexity"] == "complex"
    assert route["tier"] == "rule"


@pytest.mark.asyncio
async def test_route_thread_inertia_overrides_simple_rules(monkeypatch: pytest.MonkeyPatch):
    """短问题本应判 simple，但 thread 上一 run 是 complex 时必须粘滞到 complex。"""
    _patch_model_cache(monkeypatch)
    monkeypatch.setattr(
        question_routing,
        "AgentRunRepository",
        lambda db: _LastRunRepo(db, last_run=SimpleNamespace(input_payload={"route": {"complexity": "complex"}})),
    )
    spec, route = await route_question_model_spec(
        explicit_model_spec=None,
        base_spec="base",
        agent_item=_agent_item({"model_simple": "fast-1"}),
        agent_backend=_Backend(),
        question="f10 的价格是多少",
        has_image=False,
        has_attachment=False,
        thread_id="t",
        uid="u",
        db=None,
    )
    assert spec == "base"
    assert route == {"complexity": "complex", "tier": "thread-inertia", "reason": "thread 上一 run 为 complex"}


@pytest.mark.asyncio
async def test_route_thread_inertia_not_triggered_by_simple_history(monkeypatch: pytest.MonkeyPatch):
    _patch_model_cache(monkeypatch)
    monkeypatch.setattr(
        question_routing,
        "AgentRunRepository",
        lambda db: _LastRunRepo(db, last_run=SimpleNamespace(input_payload={"route": {"complexity": "simple"}})),
    )
    spec, route = await route_question_model_spec(
        explicit_model_spec=None,
        base_spec="base",
        agent_item=_agent_item({"model_simple": "fast-1"}),
        agent_backend=_Backend(),
        question="f10 的价格是多少",
        has_image=False,
        has_attachment=False,
        thread_id="t",
        uid="u",
        db=None,
    )
    assert spec == "fast-1"
    assert route["complexity"] == "simple"


@pytest.mark.asyncio
async def test_route_thread_inertia_ignores_runs_without_route(monkeypatch: pytest.MonkeyPatch):
    """subagent 等未路由 run 没有 route 记录，不构成惯性。"""
    _patch_model_cache(monkeypatch)
    monkeypatch.setattr(
        question_routing,
        "AgentRunRepository",
        lambda db: _LastRunRepo(db, last_run=SimpleNamespace(input_payload={"model_spec": "base"})),
    )
    spec, route = await route_question_model_spec(
        explicit_model_spec=None,
        base_spec="base",
        agent_item=_agent_item({"model_simple": "fast-1"}),
        agent_backend=_Backend(),
        question="f10 的价格是多少",
        has_image=False,
        has_attachment=False,
        thread_id="t",
        uid="u",
        db=None,
    )
    assert spec == "fast-1"
    assert route["complexity"] == "simple"
