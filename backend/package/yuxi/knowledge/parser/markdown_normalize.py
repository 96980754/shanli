"""解析结果 Markdown 的出口级规整。

各解析器各自负责上传图片、改写引用，漏写就会在预览里裂图。这里放三个纯函数：
剥掉引擎加在表格上的表现性属性、删除指定的图片引用（图片输入下丢弃引擎切出的
图块时用）、检查是否还有解析器漏改写的引用。
"""

from __future__ import annotations

import re

# 引擎（PaddleOCR-VL / MinerU / PP-Structure-V3）给表格加 style/border 是纯表现性
# 属性：前端 MarkdownPreview 已有 th, td { border: none }，border=1 视觉零影响，而
# style 占掉了 PDF 切片近八成的标记字符。只匹配表格标签——同一套规则若全局施加，
# 会顺手拆掉图片容器的 text-align（那是引擎唯一的居中手段）。
_TABLE_TAG_RE = re.compile(r"<(?:table|thead|tbody|tfoot|tr|td|th)\b[^>]*>", re.IGNORECASE)
_STYLE_ATTR_RE = re.compile(r"""\s+style\s*=\s*(?:"[^"]*"|'[^']*')""", re.IGNORECASE)
_BORDER_ATTR_RE = re.compile(r"""\s+border\s*=\s*(?:"[^"]*"|'[^']*'|\d+)""", re.IGNORECASE)

_IMG_TAG_SRC_RE = re.compile(r"""<img\b[^>]*?\bsrc\s*=\s*(?:"([^"]*)"|'([^']*)')""", re.IGNORECASE)
_MD_IMAGE_RE = re.compile(r"!\[[^\]]*\]\(\s*([^)\s]+)")
_EMPTY_DIV_RE = re.compile(r"<div\b[^>]*>\s*</div>\s*", re.IGNORECASE)

# 能在浏览器里直接渲染的引用前缀
_RENDERABLE_PREFIXES = ("http://", "https://", "data:")


def strip_presentational_html(markdown: str) -> str:
    """剥掉表格标签上的 style 与 border 属性，保留 colspan/rowspan/width/align 等。"""

    def _clean_tag(match: re.Match[str]) -> str:
        return _BORDER_ATTR_RE.sub("", _STYLE_ATTR_RE.sub("", match.group(0)))

    return _TABLE_TAG_RE.sub(_clean_tag, markdown)


def strip_image_reference(markdown: str, image_ref: str) -> str:
    """删除指向 image_ref 的图片引用，并收掉只剩空壳的 div 与多余空行。"""
    if not image_ref:
        return markdown

    escaped = re.escape(image_ref)
    markdown = re.sub(rf"!\[[^\]]*\]\(\s*{escaped}\s*\)", "", markdown)
    markdown = re.sub(rf"""<img\b[^>]*?\bsrc\s*=\s*(["']){escaped}\1[^>]*/?>""", "", markdown)
    markdown = _EMPTY_DIV_RE.sub("", markdown)
    return re.sub(r"\n{3,}", "\n\n", markdown)


def find_dangling_image_refs(markdown: str) -> list[str]:
    """返回渲染不出来的图片引用（既不是 http(s) 也不是 data: 的那部分）。

    解析正确时应当为空；非空即说明某个解析器没有把引擎图块转存并改写引用，
    调用方据此告警，而不是静默删掉——那会把"图片解析丢了"掩盖成"文档本来没图"。
    """
    refs = [m.group(1) or m.group(2) for m in _IMG_TAG_SRC_RE.finditer(markdown)]
    refs.extend(m.group(1) for m in _MD_IMAGE_RE.finditer(markdown))
    return [ref for ref in refs if ref and not ref.startswith(_RENDERABLE_PREFIXES)]
