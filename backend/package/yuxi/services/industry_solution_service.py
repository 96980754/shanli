"""Validation and document rendering for multi-product industry solutions."""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from pydantic import BaseModel, Field, field_validator

from yuxi.knowledge.utils.office_writer import markdown_to_blocks, write_whitepaper_docx

MIN_PRODUCTS = 2
MAX_PRODUCTS = 5
MAX_PRODUCT_NAME_LENGTH = 80
MAX_SOLUTION_CONTENT_LENGTH = 200_000
INDUSTRY_SOLUTION_FONT = "Microsoft YaHei"
_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._\-\u4e00-\u9fff]+")
_TRAILING_REFERENCES_RE = re.compile(
    r"\n#{1,6}\s*(?:来源(?:\s*/\s*References)?|References)\s*\n.*\Z",
    flags=re.IGNORECASE | re.DOTALL,
)


def _normalize_product_names(products: list[str], *, min_products: int) -> list[str]:
    """去重、去空白并校验产品名长度；去重后不足 min_products 则报错。"""
    normalized: list[str] = []
    seen: set[str] = set()
    for product in products:
        name = str(product or "").strip()
        if not name:
            continue
        if len(name) > MAX_PRODUCT_NAME_LENGTH:
            raise ValueError(f"产品名称不能超过 {MAX_PRODUCT_NAME_LENGTH} 个字符")
        key = name.casefold()
        if key not in seen:
            seen.add(key)
            normalized.append(name)
    if len(normalized) < min_products:
        raise ValueError(f"至少需要 {min_products} 个不同产品")
    return normalized


class IndustrySolutionRequest(BaseModel):
    """行业方案入口请求。

    前端「直接在输入框输入需求」的模式下，行业与产品都可留空，由技能从需求
    描述中识别产品并逐一检索；需求必填。真正执行分产品检索/导出时使用
    ProductResearchInput / ExportIndustrySolutionInput 保持产品数量严格。
    """

    industry: str = Field(default="", max_length=120)
    requirement: str = Field(min_length=1, max_length=2_000)
    products: list[str] = Field(default_factory=list, max_length=MAX_PRODUCTS)

    @field_validator("industry")
    @classmethod
    def normalize_industry(cls, value: str) -> str:
        return str(value or "").strip()

    @field_validator("requirement")
    @classmethod
    def normalize_requirement(cls, value: str) -> str:
        value = str(value or "").strip()
        if not value:
            raise ValueError("需求不能为空")
        return value

    @field_validator("products")
    @classmethod
    def normalize_products(cls, products: list[str]) -> list[str]:
        return _normalize_product_names(products, min_products=0)


def build_retrieval_query(*, product: str, industry: str, requirement: str) -> str:
    return f"产品：{product}\n行业场景：{industry}\n需求：{requirement}"


def normalize_source(result: dict[str, Any], *, product: str, reference: int) -> dict[str, Any]:
    metadata = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
    title = next(
        (
            str(metadata[key]).strip()
            for key in ("source", "file_name", "filename", "document_name", "title")
            if metadata.get(key)
        ),
        "知识库资料",
    )
    title = re.split(r"[/\\]", title)[-1] or "知识库资料"
    source = {
        "reference": reference,
        "product": product,
        "title": title,
        "kb_id": str(result.get("kb_id") or ""),
        "file_id": str(result.get("file_id") or ""),
        "chunk_id": str(result.get("id") or ""),
    }
    url = metadata.get("url") or metadata.get("source_url")
    if isinstance(url, str) and url.startswith(("https://", "http://")):
        source["url"] = url
    return source


def render_solution_docx(
    *,
    title: str,
    industry: str,
    products: list[str],
    content: str,
    sources: list[dict[str, Any]],
) -> tuple[bytes, str]:
    normalized_content = str(content or "").strip()
    normalized_content = _TRAILING_REFERENCES_RE.sub("", normalized_content).strip()
    if not normalized_content:
        raise ValueError("方案正文不能为空")
    if len(normalized_content) > MAX_SOLUTION_CONTENT_LENGTH:
        raise ValueError("方案正文过长")

    sources_by_reference: dict[int, dict[str, Any]] = {}
    for source in sources:
        reference = int(source.get("reference") or 0)
        if reference in sources_by_reference:
            raise ValueError(f"来源编号 [{reference}] 重复")
        sources_by_reference[reference] = source

    cited_references = list(dict.fromkeys(int(value) for value in re.findall(r"\[(\d+)]", normalized_content)))
    if not cited_references:
        raise ValueError("方案正文必须包含来源编号引用")
    missing_references = [reference for reference in cited_references if reference not in sources_by_reference]
    if missing_references:
        missing = ", ".join(f"[{reference}]" for reference in missing_references)
        raise ValueError(f"方案正文引用了不存在的来源: {missing}")

    reference_lines = ["# 来源 / References"]
    for reference in cited_references:
        source = sources_by_reference[reference]
        product = str(source.get("product") or "").strip()
        source_title = str(source.get("title") or "知识库资料").strip()
        source_title = source_title.replace("`", "'")
        markdown_source_title = f"`{source_title}`"
        line = f"[{reference}] {product} - {markdown_source_title}"
        if source.get("url"):
            line += f" - {source['url']}"
        reference_lines.append(line)

    chat_markdown = f"{normalized_content}\n\n" + "\n\n".join(reference_lines)
    # 标题在封面上展示；正文以「方案概述」章节开头，避免二级标题先于一级标题的层级倒挂。
    document_markdown = (
        f"# 方案概述\n\n## 行业 / 场景\n\n{industry}\n\n## 选用产品\n\n{', '.join(products)}\n\n{chat_markdown}"
    )
    today = date.today()
    document_title = str(title or "行业解决方案").strip()
    cover = {
        "label": "行业解决方案",
        "title": document_title,
        "industry": industry,
        "products": "、".join(products),
        "date": f"{today.year}年{today.month}月{today.day}日",
    }
    document_bytes = write_whitepaper_docx(
        markdown_to_blocks(document_markdown),
        cover=cover,
        header_text=document_title,
        font_name=INDUSTRY_SOLUTION_FONT,
    )
    return document_bytes, chat_markdown


def sanitize_docx_filename(value: str) -> str:
    stem = _SAFE_FILENAME_RE.sub("-", str(value or "").strip()).strip(".-_") or "行业解决方案"
    return f"{stem[:100]}.docx"
