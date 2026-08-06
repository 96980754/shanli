"""规则清洗器：用正则/规则零 LLM 清理文档排版噪声。

处理 PDF/网页/OCR 最常见的格式问题：页码、页眉页脚、连续空行、被硬换行切断的句子、
OCR 残留标记。只做机械整理，不改变语义、不增删信息。
"""

from __future__ import annotations

import re

# 页码行：纯数字、- 3 -、— 12 —、Page 12 / 12 / 50、第 3 页 等
_PAGE_NUMBER_PATTERN = re.compile(
    r"^\s*(?:[-—–]+\s*\d+\s*[-—–]+|第\s*\d+\s*页|"
    r"page\s+\d+\s*(?:/\s*\d+)?|p\.?\s*\d+|"
    r"\d+\s*/\s*\d+)\s*$",
    re.IGNORECASE,
)

# 常见页眉页脚关键词（允许行内含少量附加词，如“公司机密文件”）
_HEADER_FOOTER_KEYWORDS = re.compile(
    r"^(?:公司机密(?:文件)?|机密文件|内部资料|仅供内部使用|"
    r"copyright\s*(?:©|\(c\))?\s*\d{0,4}|all rights reserved|"
    r"www\.\S+|http(?:s)?://\S+)\s*$",
    re.IGNORECASE,
)

# OCR 残留标记：@@0123\t...## 等
_OCR_RESIDUE_PATTERN = re.compile(r"@@[0-9-]+(?:\t[0-9.\t]+)*##")

# 连续空行折叠
_BLANK_LINE_FOLD = re.compile(r"\n{3,}")

# 行尾是否已是完整句（以标点/冒号/反引号/列表标记/表格等结尾，不参与合并）
_SENTENCE_END = re.compile(r"[。；！？:：,，.…%!?)]$|[`|>]\s*$|[-—–]\s*$|^\s*[-*+]\s")


def _is_page_number_line(line: str) -> bool:
    return bool(_PAGE_NUMBER_PATTERN.match(line.strip()))


def _is_header_footer_line(line: str) -> bool:
    return bool(_HEADER_FOOTER_KEYWORDS.match(line.strip()))


def rule_clean_markdown(markdown: str) -> str:
    """机械清洗 markdown，返回清洗后的文本。"""
    if not markdown:
        return markdown

    text = markdown
    # 1. 清理 OCR 残留标记
    text = _OCR_RESIDUE_PATTERN.sub("", text)

    # 2. 逐行处理：去页码、页眉页脚、折叠空行、合并硬换行
    lines = text.split("\n")
    cleaned_lines: list[str] = []
    in_code_block = False
    i = 0
    while i < len(lines):
        raw_line = lines[i]
        line = raw_line

        # 代码块内容原样保留（不合并不删行）
        if line.strip().startswith("```"):
            in_code_block = not in_code_block
            cleaned_lines.append(line)
            i += 1
            continue

        if in_code_block:
            cleaned_lines.append(line)
            i += 1
            continue

        stripped = line.strip()
        if not stripped:
            # 连续空行折叠：仅当上一行非空时才保留一个空行
            if cleaned_lines and cleaned_lines[-1].strip():
                cleaned_lines.append("")
            i += 1
            continue

        # 页码行直接丢弃
        if _is_page_number_line(line):
            i += 1
            continue

        # 页眉页脚行丢弃
        if _is_header_footer_line(line):
            i += 1
            continue

        # 合并被硬换行切断的句子：当前行非空、行尾无完整句标点、
        # 下一行存在且非空、非列表/表格/代码，则与下一行拼接
        if (
            i + 1 < len(lines)
            and not _SENTENCE_END.search(line)
            and lines[i + 1].strip()
            and not lines[i + 1].strip().startswith(("-", "*", "+", "|", "#", ">", "```"))
            and not _is_page_number_line(lines[i + 1])
        ):
            cleaned_lines.append(line.rstrip() + lines[i + 1].strip())
            i += 2
            continue

        cleaned_lines.append(line)
        i += 1

    return "\n".join(cleaned_lines).strip()
