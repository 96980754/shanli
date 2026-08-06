from __future__ import annotations

import pytest

from yuxi.knowledge.utils.document_cleaner import (
    SECTION_ENHANCE_SYSTEM_PROMPT,
    build_section_enhance_user_message,
    clean_document_file,
    clean_document_markdown,
)


class _FakeResponse:
    def __init__(self, content: str):
        self.content = content


@pytest.mark.asyncio
async def test_clean_document_markdown_pipeline_rules_and_sections(monkeypatch):
    captured = {}

    class _FakeModel:
        async def call(self, messages, stream=False):
            captured.setdefault("count", 0)
            captured["count"] += 1
            # 每个 section 原样返回（内容保真）
            return _FakeResponse(messages[1]["content"])

    monkeypatch.setattr(
        "yuxi.knowledge.utils.document_cleaner.select_model",
        lambda model_spec=None: _FakeModel(),
    )

    raw = "# 第一章\n\n第一段 4GB 内存。\n\n## 小节\n\n第二段 MCSTARS。"
    result = await clean_document_markdown(raw)

    assert result["cleaned_markdown"]
    assert "4GB" in result["cleaned_markdown"]
    assert "MCSTARS" in result["cleaned_markdown"]
    # 有标题 → 至少 2 个 section（第一章 + 小节），且并行调用
    assert captured["count"] >= 2


@pytest.mark.asyncio
async def test_clean_document_markdown_empty_input_raises(monkeypatch):
    async def _no_call(*_args, **_kwargs):
        raise AssertionError("model should not be called for empty input")

    monkeypatch.setattr(
        "yuxi.knowledge.utils.document_cleaner.select_model",
        lambda model_spec=None: type("M", (), {"call": _no_call})(),
    )

    with pytest.raises(ValueError):
        await clean_document_markdown("   ")


@pytest.mark.asyncio
async def test_clean_document_markdown_rules_remove_page_numbers(monkeypatch):
    class _FakeModel:
        async def call(self, messages, stream=False):
            return _FakeResponse(messages[1]["content"])

    monkeypatch.setattr(
        "yuxi.knowledge.utils.document_cleaner.select_model",
        lambda model_spec=None: _FakeModel(),
    )

    raw = "第 1 页\n\n# 标题\n\n正文内容\n\n- 3 -\n\n- 4 -"
    result = await clean_document_markdown(raw)

    # 页码行被规则清洗移除
    assert "第 1 页" not in result["cleaned_markdown"]
    assert "- 3 -" not in result["cleaned_markdown"]
    assert "# 标题" in result["cleaned_markdown"]
    assert "正文内容" in result["cleaned_markdown"]


@pytest.mark.asyncio
async def test_clean_document_markdown_validator_fallback_on_shrink(monkeypatch):
    class _FakeModel:
        async def call(self, messages, stream=False):
            # 模拟 LLM 严重丢失内容
            return _FakeResponse("仅剩一句话")

    monkeypatch.setattr(
        "yuxi.knowledge.utils.document_cleaner.select_model",
        lambda model_spec=None: _FakeModel(),
    )

    raw = "# 标题\n\n" + "很长的正文内容 " * 100
    result = await clean_document_markdown(raw)

    # 严重缩减 → 回退为规则清洗后的原文，且带 warning
    assert result["cleaned_markdown"]
    assert result["warnings"]
    assert "很长的正文内容" in result["cleaned_markdown"]


@pytest.mark.asyncio
async def test_clean_document_file_reads_parses_and_pipeline(monkeypatch):
    from yuxi.knowledge import runtime as kb_runtime
    from yuxi.knowledge.parser import unified as parser_unified

    raw_bytes = "混乱 原始 内容".encode("utf-8")

    class _FakeKbInstance:
        async def _read_minio_bytes(self, file_path: str) -> bytes:
            assert file_path == "minio://kb/x/raw.md"
            return raw_bytes

    class _FakeKnowledgeBase:
        async def _get_kb_for_database(self, kb_id: str):
            assert kb_id == "kb-1"
            return _FakeKbInstance()

    async def _fake_aparse(source: str, params=None) -> str:
        with open(source, "rb") as f:
            return f.read().decode("utf-8")

    async def _fake_clean(raw: str) -> dict:
        return {"cleaned_markdown": f"# 清洗: {raw}", "warnings": []}

    monkeypatch.setattr(kb_runtime, "knowledge_base", _FakeKnowledgeBase())
    monkeypatch.setattr(parser_unified.Parser, "aparse", staticmethod(_fake_aparse))
    monkeypatch.setattr(
        "yuxi.knowledge.utils.document_cleaner.clean_document_markdown",
        _fake_clean,
    )

    result = await clean_document_file("kb-1", "minio://kb/x/raw.md", "raw.md")

    assert result["cleaned_markdown"] == "# 清洗: 混乱 原始 内容"


def test_build_section_enhance_user_message_passes_body_only():
    # 只传正文，标题由 _reassemble 统一拼接，避免模型重复标题
    msg = build_section_enhance_user_message(
        {"title": "技术参数", "full_title": "产品介绍 / 技术参数", "content": "支持 4G"}
    )
    assert msg == "支持 4G"
    assert "技术参数" not in msg


def test_section_system_prompt_mentions_fidelity():
    assert "严格保真" in SECTION_ENHANCE_SYSTEM_PROMPT
    assert "不得增删或编造" in SECTION_ENHANCE_SYSTEM_PROMPT
