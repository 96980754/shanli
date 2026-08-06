"""Word/Excel 写回：把网页编辑后的文字/单元格写回 docx/xlsx。

- write_docx(blocks) → bytes：python-docx 生成 .docx
- write_xlsx(sheets) → bytes：openpyxl 生成 .xlsx
- markdown_to_blocks(markdown) / markdown_to_sheets(markdown)：清洗后的 markdown 转结构化内容，供写回原格式
- replace_document_content(...)：写回后入库并删旧版
"""

from __future__ import annotations

import io
from typing import Any

from markdown_it import MarkdownIt

# 与清洗链路共用同一 markdown 解析器（document_section_splitter 同款），
# 保证清洗后的 markdown 写回原格式时标题/表格解析一致。
_md = MarkdownIt("commonmark", {"html": True}).enable("table")


def write_docx(blocks: list[dict]) -> bytes:
    """将编辑后的段落/标题/表格写回 .docx 字节。"""
    from docx import Document

    document = Document()
    for block in blocks or []:
        kind = block.get("kind")
        if kind == "heading":
            level = min(max(int(block.get("level") or 1), 1), 6)
            document.add_heading(block.get("text", ""), level=level)
        elif kind == "table":
            rows = block.get("rows") or []
            if rows:
                ncols = max(len(r) for r in rows)
                table = document.add_table(rows=len(rows), cols=ncols)
                table.style = "Table Grid"
                for i, row in enumerate(rows):
                    for j in range(ncols):
                        table.cell(i, j).text = row[j] if j < len(row) else ""
        else:
            document.add_paragraph(block.get("text", ""))
    buf = io.BytesIO()
    document.save(buf)
    return buf.getvalue()


def write_xlsx(sheets: list[dict]) -> bytes:
    """将编辑后的工作表写回 .xlsx 字节。"""
    from openpyxl import Workbook

    workbook = Workbook()
    default = workbook.active
    for idx, sheet in enumerate(sheets or []):
        name = (sheet.get("name") or f"Sheet{idx + 1}")[:31]
        ws = workbook.active if idx == 0 else workbook.create_sheet()
        ws.title = name
        for row in sheet.get("rows") or []:
            ws.append(list(row))
    buf = io.BytesIO()
    workbook.save(buf)
    return buf.getvalue()


def _collect_table_rows(tokens: list[Any], start: int) -> list[list[str]]:
    """从 table_open 开始收集表格行，每行一个单元格字符串列表。"""
    rows: list[list[str]] = []
    current: list[str] = []
    j = start + 1
    n = len(tokens)
    while j < n and tokens[j].type != "table_close":
        if tokens[j].type == "tr_open":
            current = []
        elif tokens[j].type == "tr_close":
            if current:
                rows.append(current)
                current = []
        elif tokens[j].type == "inline":
            cell = tokens[j].content.strip()
            if cell:
                current.append(cell)
        j += 1
    if current:
        rows.append(current)
    return rows


def _skip_to_token(tokens: list[Any], start: int, token_type: str) -> int:
    """返回从 start 起第一个 type == token_type 的下标，找不到则返回 len。"""
    n = len(tokens)
    j = start
    while j < n and tokens[j].type != token_type:
        j += 1
    return j


def markdown_to_blocks(markdown: str) -> list[dict]:
    """将清洗后的 markdown 解析为 docx blocks: [{kind: heading|para|table, text|rows, level?}]"""
    if not markdown or not markdown.strip():
        return []
    tokens = _md.parse(markdown)
    blocks: list[dict] = []
    i = 0
    n = len(tokens)
    while i < n:
        ttype = tokens[i].type
        if ttype == "heading_open":
            level = int(tokens[i].tag[1:]) if tokens[i].tag and len(tokens[i].tag) > 1 else 1
            inline = tokens[i + 1] if i + 1 < n and tokens[i + 1].type == "inline" else None
            text = (inline.content or "").strip() if inline else ""
            if text:
                blocks.append({"kind": "heading", "text": text, "level": level})
            i += 3
        elif ttype == "paragraph_open":
            inline = tokens[i + 1] if i + 1 < n and tokens[i + 1].type == "inline" else None
            text = (inline.content or "").strip() if inline else ""
            if text:
                blocks.append({"kind": "para", "text": text})
            i += 3
        elif ttype == "table_open":
            rows = _collect_table_rows(tokens, i)
            if rows:
                blocks.append({"kind": "table", "rows": rows})
            i = _skip_to_token(tokens, i, "table_close") + 1
        else:
            i += 1
    return blocks


def markdown_to_sheets(markdown: str) -> list[dict]:
    """将清洗后的 markdown 解析为 xlsx sheets: [{name, rows: [[...]]}]。

    表格行保留多列结构；标题/段落作为单列文本行，全部归入默认 Sheet。
    """
    if not markdown or not markdown.strip():
        return []
    tokens = _md.parse(markdown)
    rows: list[list[str]] = []
    i = 0
    n = len(tokens)
    while i < n:
        ttype = tokens[i].type
        if ttype == "heading_open":
            inline = tokens[i + 1] if i + 1 < n and tokens[i + 1].type == "inline" else None
            text = (inline.content or "").strip() if inline else ""
            if text:
                rows.append([text])
            i += 3
        elif ttype == "paragraph_open":
            inline = tokens[i + 1] if i + 1 < n and tokens[i + 1].type == "inline" else None
            text = (inline.content or "").strip() if inline else ""
            if text:
                rows.append([text])
            i += 3
        elif ttype == "table_open":
            rows.extend(_collect_table_rows(tokens, i))
            i = _skip_to_token(tokens, i, "table_close") + 1
        else:
            i += 1
    return [{"name": "Sheet1", "rows": rows}] if rows else []


def serialize_edited_content(content_type: str, payload: dict) -> bytes:
    """根据 type 把编辑后的 blocks/sheets 序列化为 docx/xlsx 字节。"""
    if content_type == "xlsx":
        return write_xlsx(payload.get("sheets") or [])
    return write_docx(payload.get("blocks") or [])
