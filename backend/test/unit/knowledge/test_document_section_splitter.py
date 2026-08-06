from __future__ import annotations

from yuxi.knowledge.utils.document_section_splitter import (
    _split_section_content,
    split_markdown_by_headings,
)


def test_splits_by_headings_with_title_path():
    md = "# 产品介绍\n\n第一段。\n\n## 技术参数\n\n支持 4G。\n\n# 部署\n\n说明。"
    sections = split_markdown_by_headings(md)
    assert len(sections) == 3
    assert sections[0]["title"] == "产品介绍"
    assert "第一段" in sections[0]["content"]
    assert sections[1]["title"] == "技术参数"
    assert sections[1]["full_title"] == "产品介绍 / 技术参数"
    assert "4G" in sections[1]["content"]
    assert sections[2]["title"] == "部署"


def test_keeps_untitled_intro_as_empty_title_section():
    md = "前言无标题。\n\n# 第一章\n\n正文。"
    sections = split_markdown_by_headings(md)
    assert sections[0]["title"] == ""
    assert "前言无标题" in sections[0]["content"]
    assert sections[1]["title"] == "第一章"


def test_empty_input_returns_empty():
    assert split_markdown_by_headings("") == []
    assert split_markdown_by_headings("   ") == []


def test_split_section_content_oversized_splits_by_paragraph():
    big = "\n\n".join(f"第{i}段内容 " * 50 for i in range(20))
    parts = _split_section_content(big, max_chars=2000)
    assert len(parts) > 1
    assert all(len(p) <= 2000 * 2 for p in parts)
