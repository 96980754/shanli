"""预检索知识库快速路径单元测试。"""

from yuxi.agents import base as base_module
from yuxi.agents.buildin.chatbot import prefetch as prefetch_module
from yuxi.agents.buildin.chatbot.prefetch import (
    KNOWLEDGE_BASE_SKILL_SLUG,
    prefetch_knowledge_context,
)
from yuxi.agents.buildin.chatbot.prompt import build_prompt_with_context


class _Ctx:
    """预检索相关字段的最小上下文。"""

    def __init__(self, *, prefetch=True, query="测试问题", kbs=None, prompt_skills=None):
        self.prefetch_knowledge = prefetch
        self._latest_user_query = query
        self._visible_knowledge_bases = (
            kbs
            if kbs is not None
            else [
                {"kb_id": "kb_1", "name": "库A"},
                {"kb_id": "kb_2", "name": "库B"},
            ]
        )
        self._prompt_skills = prompt_skills if prompt_skills is not None else ["knowledge-base", "deep-research"]
        self._prefetch_knowledge_block = None


def _retriever(kb_id, *, empty=False, raise_error=False):
    """模拟 get_retrievers 的 retriever 裸输出：只返回 {kb_id, results}，无 status。"""
    async def retriever(query_text):
        if raise_error:
            raise RuntimeError("milvus down")
        return {
            "kb_id": kb_id,
            "results": []
            if empty
            else [
                {
                    "id": f"{kb_id}-c1",
                    "kb_id": kb_id,
                    "file_id": f"f-{kb_id}",
                    "content": f"{kb_id} 的检索正文",
                    "metadata": {"chunk_id": "chunk-1", "score": 0.9},
                }
            ],
        }

    return retriever


def _retrievers(**overrides):
    base = {
        "kb_1": {"retriever": _retriever("kb_1")},
        "kb_2": {"retriever": _retriever("kb_2", empty=True)},
    }
    base.update(overrides)
    return base


def _run_prefetch(ctx, retrievers):
    import asyncio

    async def _call():
        prefetch_module.knowledge_base.get_retrievers = lambda: retrievers
        await prefetch_knowledge_context(ctx)

    asyncio.run(_call())


def test_prefetch_builds_block_with_sources(monkeypatch):
    ctx = _Ctx()
    _run_prefetch(ctx, _retrievers())
    assert ctx._prefetch_knowledge_block
    block = ctx._prefetch_knowledge_block
    assert "kb_1" in block and "库A" in block
    assert "status=ok" in block
    assert "file_id=f-kb_1" in block and "chunk_id=chunk-1" in block
    assert "kb_1 的检索正文" in block
    assert "status=insufficient" in block and "no_results" in block


def test_prefetch_single_kb_error_does_not_break_others():
    ctx = _Ctx()
    retrievers = _retrievers(kb_2={"retriever": _retriever("kb_2", raise_error=True)})
    _run_prefetch(ctx, retrievers)
    assert ctx._prefetch_knowledge_block
    assert "status=ok" in ctx._prefetch_knowledge_block  # kb_1 正常
    assert "status=error" in ctx._prefetch_knowledge_block  # kb_2 失败标记为系统异常


def test_prefetch_disabled_is_noop():
    ctx = _Ctx(prefetch=False)
    _run_prefetch(ctx, _retrievers())
    assert ctx._prefetch_knowledge_block is None
    assert ctx._prompt_skills == ["knowledge-base", "deep-research"]


def test_prefetch_without_query_or_kbs_is_noop():
    no_query = _Ctx(query="")
    _run_prefetch(no_query, _retrievers())
    assert no_query._prefetch_knowledge_block is None

    no_kbs = _Ctx(kbs=[])
    _run_prefetch(no_kbs, _retrievers())
    assert no_kbs._prefetch_knowledge_block is None


def test_prefetch_removes_knowledge_base_from_prompt_skills():
    ctx = _Ctx()
    _run_prefetch(ctx, _retrievers())
    assert KNOWLEDGE_BASE_SKILL_SLUG not in ctx._prompt_skills
    assert "deep-research" in ctx._prompt_skills


def test_build_prompt_appends_prefetch_block():
    class FakeContext:
        system_prompt = "业务配置"

        def __init__(self):
            self._prefetch_knowledge_block = "<| 知识库预检索结果 |> 预检索块内容"

    prompt = build_prompt_with_context(FakeContext())
    assert "预检索块内容" in prompt
    assert "业务配置" in prompt  # 原业务配置仍保留


def test_latest_human_message_text_extraction():
    from langchain_core.messages import HumanMessage

    extract = base_module._latest_human_message_text
    assert extract({"messages": ["纯字符串"]}) == "纯字符串"
    assert extract({"messages": [{"role": "user", "content": "dict 消息"}]}) == "dict 消息"
    assert extract({"messages": [HumanMessage(content="对象消息")]}) == "对象消息"
    assert base_module._latest_human_message_text({"messages": []}) == ""
    assert base_module._latest_human_message_text("非 dict") == ""
