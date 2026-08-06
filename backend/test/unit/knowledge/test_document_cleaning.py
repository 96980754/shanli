from __future__ import annotations
import json
from types import SimpleNamespace
import pytest
from yuxi.knowledge import cleaning
from yuxi.knowledge.cleaning import (
    OptionalAIDocumentCleaner,
    RuleBasedDocumentCleaner,
    sanitize_markdown_html,
)
def test_rule_cleaning_normalizes_unicode_controls_spaces_and_blank_lines():
    source = "Cafe\u0301\x00  文本\r\n\r\n\r\n\r\n下一段"
    result = RuleBasedDocumentCleaner().clean(source)
    assert "Café" in result.cleaned_markdown
    assert "\x00" not in result.cleaned_markdown
    assert "  文本" not in result.cleaned_markdown
    assert "\n\n\n\n" not in result.cleaned_markdown
def test_rule_cleaning_removes_only_consecutive_duplicate_paragraphs():
    source = "第一段。\n\n重复内容。\n\n重复内容。\n\n第一段。"
    result = RuleBasedDocumentCleaner().clean(source)
    assert result.cleaned_markdown.count("重复内容。") == 1
    assert result.cleaned_markdown.count("第一段。") == 2
def test_rule_cleaning_conservatively_removes_repeated_page_headers_and_footers():
    blocks = []
    for page in range(1, 4):
        blocks.extend(
            [
                {"page_number": page, "order": 0, "text": "公司内部资料"},
                {"page_number": page, "order": 1, "text": f"第 {page} 页正文。"},
                {"page_number": page, "order": 2, "text": "保密文件"},
            ]
        )
    source = "\n\n".join(block["text"] for block in blocks)
    result = RuleBasedDocumentCleaner().clean(source, parse_metadata={"blocks": blocks})
    assert "公司内部资料" not in result.cleaned_markdown
    assert "保密文件" not in result.cleaned_markdown
    assert "第 1 页正文。" in result.cleaned_markdown
def test_rule_cleaning_does_not_guess_headers_with_fewer_than_three_pages():
    blocks = [
        {"page_number": 1, "order": 0, "text": "产品说明"},
        {"page_number": 1, "order": 1, "text": "正文 A。"},
        {"page_number": 2, "order": 0, "text": "产品说明"},
        {"page_number": 2, "order": 1, "text": "正文 B。"},
    ]
    result = RuleBasedDocumentCleaner().clean(
        "\n\n".join(block["text"] for block in blocks),
        parse_metadata={"blocks": blocks},
    )
    assert result.cleaned_markdown.count("产品说明") == 2
def test_rule_cleaning_preserves_markdown_tables_and_code_blocks():
    source = '| 型号 | 版本 |\n| --- | --- |\n| AX-  12 | v1.2.3 |\n\n```python\nvalue  =  "A  B"\nprint(value)\n```\n'
    result = RuleBasedDocumentCleaner().clean(source)
    assert "| AX-  12 | v1.2.3 |" in result.cleaned_markdown
    assert 'value  =  "A  B"' in result.cleaned_markdown
def test_rule_cleaning_preserves_models_versions_numbers_urls_and_paths():
    source = "型号 AB-120  版本 v2.10.3  数值 1,024.50  https://example.test/a  C:\\Program Files\\A"
    result = RuleBasedDocumentCleaner().clean(source)
    assert "AB-120" in result.cleaned_markdown
    assert "v2.10.3" in result.cleaned_markdown
    assert "1,024.50" in result.cleaned_markdown
    assert "https://example.test/a" in result.cleaned_markdown
    assert "C:\\Program Files\\A" in result.cleaned_markdown
def test_markdown_html_sanitizer_removes_executable_html():
    source = '<script>alert(1)</script><a href="javascript:alert(2)" onclick="x()">安全文本</a>'
    cleaned = sanitize_markdown_html(source)
    assert "<script" not in cleaned
    assert "onclick" not in cleaned
    assert "javascript:" not in cleaned
    assert "安全文本" in cleaned
@pytest.mark.asyncio
async def test_optional_ai_cleaner_uses_rules_when_provider_is_not_configured():
    result = await OptionalAIDocumentCleaner().clean("正文  内容", enabled=True, model_spec=None)
    assert result.cleaned_markdown == "正文 内容\n"
    assert result.ai_applied is False
    assert any("未配置" in warning for warning in result.warnings)
class _FakeModel:
    def __init__(self, response=None, error: Exception | None = None):
        self.response = response
        self.error = error
    async def ainvoke(self, _messages):
        if self.error:
            raise self.error
        return SimpleNamespace(content=json.dumps(self.response, ensure_ascii=False))
@pytest.mark.asyncio
async def test_optional_ai_cleaner_falls_back_when_provider_fails(monkeypatch):
    monkeypatch.setattr(
        cleaning,
        "select_model",
        lambda **_kwargs: SimpleNamespace(model=_FakeModel(error=TimeoutError("provider unavailable"))),
    )
    result = await OptionalAIDocumentCleaner().clean("规则  结果", enabled=True, model_spec="configured:model")
    assert result.cleaned_markdown == "规则 结果\n"
    assert result.ai_applied is False
    assert any("TimeoutError" in warning for warning in result.warnings)
@pytest.mark.asyncio
async def test_optional_ai_cleaner_rejects_new_facts_and_keeps_rule_result(monkeypatch):
    monkeypatch.setattr(
        cleaning,
        "select_model",
        lambda **_kwargs: SimpleNamespace(
            model=_FakeModel(
                response={
                    "cleaned_markdown": "原始事实 2027",
                    "changes": [{"change_type": "rewrite", "reason": "补充事实"}],
                }
            )
        ),
    )
    result = await OptionalAIDocumentCleaner().clean("原始事实", enabled=True, model_spec="configured:model")
    assert "2027" not in result.cleaned_markdown
    assert result.ai_applied is False
    assert any("AICleaningValidationError" in warning for warning in result.warnings)
@pytest.mark.asyncio
async def test_optional_ai_cleaner_rejects_new_text_without_numbers(monkeypatch):
    monkeypatch.setattr(
        cleaning,
        "select_model",
        lambda **_kwargs: SimpleNamespace(
            model=_FakeModel(
                response={
                    "cleaned_markdown": "原始事实以及新增结论",
                    "changes": [{"change_type": "rewrite", "reason": "扩写"}],
                }
            )
        ),
    )
    result = await OptionalAIDocumentCleaner().clean("原始事实", enabled=True, model_spec="configured:model")
    assert "新增结论" not in result.cleaned_markdown
    assert result.ai_applied is False
