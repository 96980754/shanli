"""按标题分 Section：将 markdown 按标题层级切成多个 section，供并行局部增强。

复用 markdown-it 的 heading 遍历（与 chunking/ragflow_like/parsers/semantic.py 同思路），
维护标题栈生成完整标题路径。超大 section 按段落二次切分，避免单 section 过大。
"""

from __future__ import annotations

from markdown_it import MarkdownIt

# 单 section 的近似字符上限（约 2000 token），超出的按段落切分
_SECTION_MAX_CHARS = 2000 * 4  # 粗略 4 char/token

_md = MarkdownIt("commonmark", {"html": True}).enable("table")


def _estimate_chars(text: str) -> int:
    return len(text or "")


def _split_section_content(content: str, max_chars: int) -> list[str]:
    """将单个 section 的内容按段落切分，避免单块过大。"""
    if _estimate_chars(content) <= max_chars:
        return [content]
    paragraphs = [p for p in content.split("\n\n") if p.strip()]
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for para in paragraphs:
        para_len = _estimate_chars(para)
        if current_len + para_len > max_chars and current:
            chunks.append("\n\n".join(current))
            current = []
            current_len = 0
        current.append(para)
        current_len += para_len
    if current:
        chunks.append("\n\n".join(current))
    return chunks


def _full_title(title_stack: list[str]) -> str:
    return " / ".join(t for t in title_stack if t)


def split_markdown_by_headings(markdown: str) -> list[dict]:
    """按标题将 markdown 切成 sections。

    Returns:
        list[dict]: [{title: str, level: int, content: str}]
        - title 为完整标题路径（如 "产品介绍 / 技术参数"），无标题前言为空串
        - level 为最深层级
        - content 为该 section 的 markdown 正文（不含标题行）
    """
    if not markdown or not markdown.strip():
        return []

    tokens = _md.parse(markdown)
    sections: list[dict] = []
    title_stack: list[str] = [""] * 6
    current_title = ""
    current_level = 0
    current_lines: list[str] = []

    def flush() -> None:
        nonlocal current_title, current_level, current_lines
        content = "\n".join(current_lines).strip()
        current_lines = []
        if not content and not current_title:
            return
        sections.append(
            {
                "title": current_title,
                "full_title": _full_title(title_stack),
                "level": current_level,
                "content": content,
            }
        )
        current_title = ""
        current_level = 0

    i = 0
    n = len(tokens)
    while i < n:
        token = tokens[i]
        ttype = token.type

        if ttype == "heading_open":
            flush()
            level = int(token.tag[1:]) if token.tag and len(token.tag) > 1 else 1
            inline = tokens[i + 1] if i + 1 < n and tokens[i + 1].type == "inline" else None
            title = inline.content.strip() if inline else ""
            title_stack[level - 1] = title
            for j in range(level, 6):
                title_stack[j] = ""
            current_title = title
            current_level = level
            i += 3
        elif ttype == "table_open":
            # 收集表格块所有 inline 行直到 table_close
            j = i
            rows: list[str] = []
            while j < n and tokens[j].type != "table_close":
                if tokens[j].type == "inline":
                    rows.append(tokens[j].content)
                j += 1
            if rows:
                current_lines.append("| " + " | ".join(rows) + " |")
            i = j + 1 if j < n else n
        elif ttype in {"paragraph_open", "bullet_list_open", "ordered_list_open"}:
            inline = tokens[i + 1] if i + 1 < n and tokens[i + 1].type == "inline" else None
            if inline:
                current_lines.append(inline.content.strip())
            i += 3
        elif ttype == "fence":
            current_lines.append(f"```\n{token.content}\n```")
            i += 1
        elif ttype == "html_block":
            current_lines.append(token.content)
            i += 1
        elif ttype in {"blockquote_open", "blockquote_close", "bullet_list_close", "ordered_list_close"}:
            i += 1
        else:
            i += 1

    flush()

    # 对超大 section 二次切分
    expanded: list[dict] = []
    for sec in sections:
        for part in _split_section_content(sec["content"], _SECTION_MAX_CHARS):
            expanded.append({**sec, "content": part})
    return expanded
