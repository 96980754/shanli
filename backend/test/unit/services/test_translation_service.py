"""translation_service 单元测试：语言检测 + 入口中译 + 出口本地化。

全部用例通过 caller 注入假模型响应，或关闭 TRANSLATION_MODEL，确保测试不发真实 LLM 调用。
"""

from unittest.mock import AsyncMock

from yuxi.services import translation_service as ts

# ---------------------------------------------------------------- 语言检测/归一


def test_is_chinese_text():
    assert ts.is_chinese_text("调度台怎么配置？") is True
    assert ts.is_chinese_text("cat1 模组如何配网") is True  # 中英混合技术提问算中文
    assert ts.is_chinese_text("How to configure cat1 module") is False
    assert ts.is_chinese_text("") is False
    assert ts.is_chinese_text(None) is False  # type: ignore[arg-type]


def test_normalize_lang():
    assert ts.normalize_lang("id") == "id"
    assert ts.normalize_lang("Indonesian") == "id"
    assert ts.normalize_lang("ZH-CN") == "zh"
    assert ts.normalize_lang("Bahasa-Melayu") == "ms"
    assert ts.normalize_lang("malay") == "ms"
    assert ts.normalize_lang("fr-FR") == "fr"  # 未登记别名退回主码
    assert ts.normalize_lang(" en ") == "en"
    assert ts.normalize_lang("") == ""
    assert ts.normalize_lang(None) == ""  # type: ignore[arg-type]


# ---------------------------------------------------------------- 入口翻译


async def test_translate_to_chinese_happy_path():
    async def fake_caller(_messages):
        return '{"source_lang": "id", "chinese": "如何配置 cat1 模组的网络？"}'

    result = await ts.translate_to_chinese("Bagaimana cara mengonfigurasi jaringan modul cat1?", caller=fake_caller)
    assert result is not None
    assert result.source_lang == "id"
    assert result.chinese == "如何配置 cat1 模组的网络？"


async def test_translate_to_chinese_tolerates_fence_and_noise():
    async def fake_caller(_messages):
        return '这里给出结果：\n```json\n{"source_lang": "en", "chinese": "调度台如何登录？"}\n```\n完'

    result = await ts.translate_to_chinese("How do I log in to the dispatch console?", caller=fake_caller)
    assert result is not None
    assert result.source_lang == "en"
    assert result.chinese == "调度台如何登录？"


async def test_translate_to_chinese_skips_chinese_input_without_calling():
    caller = AsyncMock()
    result = await ts.translate_to_chinese("调度台如何登录？", caller=caller)
    assert result is None
    caller.assert_not_awaited()


async def test_translate_to_chinese_rejects_zh_detection():
    async def fake_caller(_messages):
        return '{"source_lang": "zh", "chinese": "调度台如何登录？"}'

    result = await ts.translate_to_chinese("How do I log in?", caller=fake_caller)
    assert result is None


async def test_translate_to_chinese_unparseable_returns_none():
    async def fake_caller(_messages):
        return "抱歉，我无法处理该请求。"

    result = await ts.translate_to_chinese("How do I log in?", caller=fake_caller)
    assert result is None


async def test_translate_to_chinese_no_model_returns_none(monkeypatch):
    monkeypatch.setattr(ts, "TRANSLATION_MODEL", "")
    result = await ts.translate_to_chinese("How do I log in?")
    assert result is None


async def test_translate_to_chinese_empty_input():
    caller = AsyncMock()
    result = await ts.translate_to_chinese("   ", caller=caller)
    assert result is None
    caller.assert_not_awaited()


# ---------------------------------------------------------------- 出口翻译


async def test_translate_from_chinese_to_target_lang():
    async def fake_caller(messages):
        assert messages[1]["role"] == "user"
        return "Bagaimana cara mengonfigurasi jaringan modul cat1?"

    result = await ts.translate_from_chinese("如何配置 cat1 模组的网络？", "id", caller=fake_caller)
    assert result == "Bagaimana cara mengonfigurasi jaringan modul cat1?"


async def test_translate_from_chinese_target_zh_or_empty_no_call():
    caller = AsyncMock()
    content = "如何配置 cat1 模组的网络？"
    assert await ts.translate_from_chinese(content, "zh", caller=caller) == content
    assert await ts.translate_from_chinese(content, "", caller=caller) == content
    assert await ts.translate_from_chinese(content, "ZH-CN", caller=caller) == content
    caller.assert_not_awaited()


async def test_translate_from_chinese_no_model_returns_original(monkeypatch):
    monkeypatch.setattr(ts, "TRANSLATION_MODEL", "")
    content = "如何配置 cat1 模组的网络？"
    assert await ts.translate_from_chinese(content, "id") == content


async def test_translate_from_chinese_overlong_source_keeps_original(monkeypatch):
    async def fake_caller(_messages):
        raise AssertionError("超长源文本不应触发翻译调用")

    content = "长" * (ts._OUTBOUND_MAX_TOTAL_CHARS + 1)
    assert await ts.translate_from_chinese(content, "id", caller=fake_caller) == content


def _force_small_chunks(monkeypatch, *, max_chunk: int = 15) -> None:
    """把 _split_for_translation 的默认分块上限强制改小（默认参数在 def 时绑定，须包一层）。"""
    orig = ts._split_for_translation
    monkeypatch.setattr(ts, "_split_for_translation", lambda text: orig(text, max_chunk=max_chunk))


async def test_translate_from_chinese_chunks_and_joins(monkeypatch):
    calls: list[str] = []

    async def fake_caller(messages):
        chunk = messages[1]["content"]
        calls.append(chunk)
        return f"译文<{len(chunk)}字>"

    # 强制分块：每行 11 字符（10 汉字 + 换行），cap=15 → 每段恰一行
    _force_small_chunks(monkeypatch)
    content = "一二三四五六七八九十\n" * 3
    result = await ts.translate_from_chinese(content, "id", caller=fake_caller)
    assert len(calls) == 3
    # 每段提示词都带固定"中文回复："前缀；逐段译文以换行拼接
    assert all(call.startswith("中文回复：\n") for call in calls)
    assert result == "\n".join(f"译文<{len(call)}字>" for call in calls)


async def test_translate_from_chinese_any_chunk_failure_keeps_original(monkeypatch):
    async def fake_caller(messages):
        chunk = messages[1]["content"]
        return None if "\n第二" in chunk else "译文片段"

    # 强制分块：三段各 10 字符，第二段含"第二"触发失败 → 整体保留中文（.strip 后与原文一致）
    _force_small_chunks(monkeypatch)
    content = "第一二三四五六七八九\n第二二三四五六七八九\n第三二三四五六七八九"
    assert await ts.translate_from_chinese(content, "id", caller=fake_caller) == content


async def test_translate_from_chinese_empty_text_returns_as_is():
    caller = AsyncMock()
    assert await ts.translate_from_chinese("", "id", caller=caller) == ""
    caller.assert_not_awaited()


# ---------------------------------------------------------------- 文本分段


def test_split_for_translation_line_grouping():
    chunks = ts._split_for_translation("一\n二\n三\n", max_chunk=100)
    assert chunks == ["一\n二\n三\n"]


def test_split_for_translation_respects_chunk_cap():
    # 每行 10 字符（含换行），cap=15 → 每段只能装 1 行
    content = "一二三四五六七八九\n" * 4
    chunks = ts._split_for_translation(content, max_chunk=15)
    assert len(chunks) == 4
    assert all(chunk.endswith("\n") for chunk in chunks)


def test_split_for_translation_hard_splits_overlong_line():
    line = "x" * 50
    chunks = ts._split_for_translation(line, max_chunk=20)
    assert chunks == [line[:20], line[20:40], line[40:]]
