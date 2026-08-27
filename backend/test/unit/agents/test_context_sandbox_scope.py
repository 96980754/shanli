"""沙箱作用域（file_thread_id/skills_thread_id）从 input_context 贯通到 Context 字段的回归测试。

背景：meta 显式指定沙箱作用域（如基准脚本固定 bench-sandbox）时，此前
build_agent_input_context 不接受这两个参数、BaseContext 也无对应字段，
工具执行路径拿不到作用域就回退到每轮真实 thread_id，导致每轮新建一个沙箱容器。
"""

from __future__ import annotations

import pytest

import yuxi.agents.context as ctx
from yuxi.agents.buildin.chatbot.context import ChatBotContext


@pytest.mark.asyncio
async def test_build_agent_input_context_carries_sandbox_scope(monkeypatch):
    """meta 指定的沙箱作用域必须进入 input_context（此前被静默丢弃）。"""

    def _no_workspace_ctx(thread_id, uid):
        return ""

    monkeypatch.setattr(ctx, "_load_workspace_agent_context", _no_workspace_ctx)

    result = await ctx.build_agent_input_context(
        {},
        thread_id="t1",
        uid="u1",
        run_id="r1",
        request_id="q1",
        file_thread_id="bench-sandbox",
        skills_thread_id="bench-sandbox",
    )

    assert result["file_thread_id"] == "bench-sandbox"
    assert result["skills_thread_id"] == "bench-sandbox"


def test_chatbot_context_stores_sandbox_scope_fields():
    """ChatBotContext 从 input_context 吸收 file_thread_id/skills_thread_id（此前无字段被丢弃）。"""

    context = ChatBotContext()
    context.update_from_dict(
        {"thread_id": "t1", "uid": "u1", "file_thread_id": "bench-sandbox", "skills_thread_id": "bench-sandbox"}
    )

    assert context.file_thread_id == "bench-sandbox"
    assert context.skills_thread_id == "bench-sandbox"


@pytest.mark.asyncio
async def test_scope_flows_from_build_to_context(monkeypatch):
    """单元层全链路：build_agent_input_context → ChatBotContext.update，作用域不丢失。"""

    def _no_workspace_ctx(thread_id, uid):
        return ""

    monkeypatch.setattr(ctx, "_load_workspace_agent_context", _no_workspace_ctx)

    input_context = await ctx.build_agent_input_context(
        {}, thread_id="t1", uid="u1", file_thread_id="bench-sandbox", skills_thread_id="bench-sandbox"
    )
    context = ChatBotContext()
    context.update_from_dict(input_context)

    assert context.file_thread_id == "bench-sandbox"
    assert context.skills_thread_id == "bench-sandbox"
