from __future__ import annotations

from yuxi.knowledge.utils.document_rule_cleaner import rule_clean_markdown


def test_removes_page_number_lines():
    text = "第 1 页\n\n正文\n\n- 3 -\n\nPage 5 / 10\n\n末尾"
    cleaned = rule_clean_markdown(text)
    assert "第 1 页" not in cleaned
    assert "- 3 -" not in cleaned
    assert "Page 5 / 10" not in cleaned
    assert "正文" in cleaned
    assert "末尾" in cleaned


def test_removes_header_footer_lines():
    text = "公司机密\n\n正文内容\n\nCopyright 2026\n\n结尾"
    cleaned = rule_clean_markdown(text)
    assert "公司机密" not in cleaned
    assert "Copyright 2026" not in cleaned
    assert "正文内容" in cleaned


def test_removes_confidential_with_extra_suffix():
    text = "公司机密文件\n\n正文内容\n\n结尾"
    cleaned = rule_clean_markdown(text)
    assert "公司机密" not in cleaned
    assert "正文内容" in cleaned


def test_folds_consecutive_blank_lines():
    text = "第一段\n\n\n\n\n第二段"
    cleaned = rule_clean_markdown(text)
    assert "\n\n\n" not in cleaned
    assert "第一段\n\n第二段" in cleaned


def test_merges_hard_wrapped_lines():
    text = "这是一段被硬换行\n切断的句子，继续"
    cleaned = rule_clean_markdown(text)
    assert "切断的句子" in cleaned
    assert "被硬换行\n切断" not in cleaned


def test_does_not_merge_list_or_code():
    text = "- 列表项一\n- 列表项二\n\n```\ncode line\n```"
    cleaned = rule_clean_markdown(text)
    assert "- 列表项一" in cleaned
    assert "- 列表项二" in cleaned
    assert "```" in cleaned
    assert "code line" in cleaned
