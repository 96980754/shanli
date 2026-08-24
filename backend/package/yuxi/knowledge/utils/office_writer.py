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


def _set_run_font(run: Any, font_name: str) -> None:
    from docx.oxml.ns import qn

    run.font.name = font_name
    fonts = run._element.get_or_add_rPr().get_or_add_rFonts()
    for attribute in ("ascii", "hAnsi", "eastAsia"):
        fonts.set(qn(f"w:{attribute}"), font_name)


def _configure_docx_styles(document: Any, font_name: str) -> None:
    from docx.oxml.ns import qn
    from docx.shared import Pt

    style_specs = {
        "Normal": (11, False),
        "Title": (20, True),
        "Heading 1": (18, True),
        "Heading 2": (15, True),
        "Heading 3": (13, True),
        "Heading 4": (11, True),
        "Heading 5": (11, True),
        "Heading 6": (11, True),
        "List Bullet": (11, False),
        "List Number": (11, False),
    }
    for style_name, (size, bold) in style_specs.items():
        style = document.styles[style_name]
        style.font.name = font_name
        style.font.size = Pt(size)
        style.font.bold = bold
        fonts = style._element.get_or_add_rPr().get_or_add_rFonts()
        for attribute in ("ascii", "hAnsi", "eastAsia"):
            fonts.set(qn(f"w:{attribute}"), font_name)


def _write_inline(paragraph: Any, value: Any, font_name: str | None) -> None:
    if isinstance(value, dict):
        runs = value.get("runs")
        text = str(value.get("text") or "")
    else:
        runs = None
        text = str(value or "")

    if runs:
        for item in runs:
            run = paragraph.add_run(str(item.get("text") or ""))
            run.bold = bool(item.get("bold"))
            run.italic = bool(item.get("italic"))
            if font_name:
                _set_run_font(run, font_name)
        return

    run = paragraph.add_run(text)
    if font_name:
        _set_run_font(run, font_name)


def write_docx(blocks: list[dict], *, font_name: str | None = None) -> bytes:
    """将编辑后的段落/标题/表格写回 .docx 字节。"""
    from docx import Document

    document = Document()
    if font_name:
        _configure_docx_styles(document, font_name)
    for block in blocks or []:
        kind = block.get("kind")
        if kind == "heading":
            level = min(max(int(block.get("level") or 1), 1), 6)
            paragraph = document.add_heading(level=level)
            _write_inline(paragraph, block, font_name)
        elif kind == "table":
            rows = block.get("rows") or []
            if rows:
                ncols = max(len(r) for r in rows)
                table = document.add_table(rows=len(rows), cols=ncols)
                table.style = "Table Grid"
                for i, row in enumerate(rows):
                    for j in range(ncols):
                        cell = table.cell(i, j)
                        _write_inline(cell.paragraphs[0], row[j] if j < len(row) else "", font_name)
        elif kind == "list_item":
            paragraph = document.add_paragraph(
                style="List Number" if block.get("ordered") else "List Bullet"
            )
            _write_inline(paragraph, block, font_name)
        else:
            paragraph = document.add_paragraph()
            _write_inline(paragraph, block, font_name)
    buf = io.BytesIO()
    document.save(buf)
    return buf.getvalue()


def write_xlsx(sheets: list[dict]) -> bytes:
    """将编辑后的工作表写回 .xlsx 字节。"""
    from openpyxl import Workbook

    workbook = Workbook()
    for idx, sheet in enumerate(sheets or []):
        name = (sheet.get("name") or f"Sheet{idx + 1}")[:31]
        ws = workbook.active if idx == 0 else workbook.create_sheet()
        ws.title = name
        for row in sheet.get("rows") or []:
            ws.append(list(row))
    buf = io.BytesIO()
    workbook.save(buf)
    return buf.getvalue()


def _inline_value(token: Any) -> str | dict[str, Any]:
    children = token.children or []
    if not children:
        return str(token.content or "").strip()

    runs: list[dict[str, Any]] = []
    bold_depth = 0
    italic_depth = 0
    for child in children:
        if child.type == "strong_open":
            bold_depth += 1
            continue
        if child.type == "strong_close":
            bold_depth = max(0, bold_depth - 1)
            continue
        if child.type == "em_open":
            italic_depth += 1
            continue
        if child.type == "em_close":
            italic_depth = max(0, italic_depth - 1)
            continue
        if child.type in {"softbreak", "hardbreak"}:
            text = "\n"
        elif child.type in {"text", "code_inline", "html_inline"}:
            text = str(child.content or "")
        else:
            continue
        if not text:
            continue
        item = {"text": text, "bold": bold_depth > 0, "italic": italic_depth > 0}
        if runs and all(runs[-1][key] == item[key] for key in ("bold", "italic")):
            runs[-1]["text"] += text
        else:
            runs.append(item)

    text = "".join(item["text"] for item in runs).strip()
    if not text:
        return ""
    if not any(item["bold"] or item["italic"] for item in runs):
        return text
    return {"text": text, "runs": runs}


def _collect_table_rows(tokens: list[Any], start: int) -> list[list[Any]]:
    """从 table_open 开始收集表格行，每行一个单元格字符串列表。"""
    rows: list[list[Any]] = []
    current: list[Any] = []
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
            value = _inline_value(tokens[j])
            if value:
                current.append(value)
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
    list_stack: list[bool] = []
    i = 0
    n = len(tokens)
    while i < n:
        ttype = tokens[i].type
        if ttype == "bullet_list_open":
            list_stack.append(False)
            i += 1
        elif ttype == "ordered_list_open":
            list_stack.append(True)
            i += 1
        elif ttype in {"bullet_list_close", "ordered_list_close"}:
            if list_stack:
                list_stack.pop()
            i += 1
        elif ttype == "heading_open":
            level = int(tokens[i].tag[1:]) if tokens[i].tag and len(tokens[i].tag) > 1 else 1
            inline = tokens[i + 1] if i + 1 < n and tokens[i + 1].type == "inline" else None
            value = _inline_value(inline) if inline else ""
            text = value.get("text", "") if isinstance(value, dict) else value
            if text:
                block = {"kind": "heading", "text": text, "level": level}
                if isinstance(value, dict):
                    block["runs"] = value["runs"]
                blocks.append(block)
            i += 3
        elif ttype == "paragraph_open":
            inline = tokens[i + 1] if i + 1 < n and tokens[i + 1].type == "inline" else None
            value = _inline_value(inline) if inline else ""
            text = value.get("text", "") if isinstance(value, dict) else value
            if text:
                block = {
                    "kind": "list_item" if list_stack else "para",
                    "text": text,
                }
                if list_stack:
                    block["ordered"] = list_stack[-1]
                if isinstance(value, dict):
                    block["runs"] = value["runs"]
                blocks.append(block)
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
            rows.extend(
                [
                    [cell.get("text", "") if isinstance(cell, dict) else cell for cell in row]
                    for row in _collect_table_rows(tokens, i)
                ]
            )
            i = _skip_to_token(tokens, i, "table_close") + 1
        else:
            i += 1
    return [{"name": "Sheet1", "rows": rows}] if rows else []


def serialize_edited_content(content_type: str, payload: dict) -> bytes:
    """根据 type 把编辑后的 blocks/sheets 序列化为 docx/xlsx 字节。"""
    if content_type == "xlsx":
        return write_xlsx(payload.get("sheets") or [])
    return write_docx(payload.get("blocks") or [])
