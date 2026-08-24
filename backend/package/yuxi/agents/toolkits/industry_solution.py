"""Agent tools for per-product research and Word delivery."""

import asyncio
from typing import Any

from langgraph.prebuilt.tool_node import ToolRuntime
from pydantic import BaseModel, Field, field_validator

from yuxi.agents.backends.sandbox.paths import (
    ensure_thread_dirs,
    sandbox_outputs_dir,
    virtual_path_for_thread_file,
)
from yuxi.agents.toolkits.kbs.tools import retrieve_kbs
from yuxi.agents.toolkits.registry import tool
from yuxi.services.industry_solution_service import (
    IndustrySolutionRequest,
    build_retrieval_query,
    normalize_source,
    render_solution_docx,
    sanitize_docx_filename,
)

_MAX_EVIDENCE_PER_PRODUCT = 5


def _matches_product(result: dict[str, Any], product: str) -> bool:
    metadata = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
    searchable = " ".join(
        str(value or "")
        for value in (
            result.get("content"),
            metadata.get("source"),
            metadata.get("file_name"),
            metadata.get("filename"),
            metadata.get("document_name"),
            metadata.get("title"),
        )
    )
    product_key = "".join(character for character in product.casefold() if character.isalnum())
    searchable_key = "".join(character for character in searchable.casefold() if character.isalnum())
    return bool(product_key and product_key in searchable_key)


class ProductResearchInput(IndustrySolutionRequest):
    kb_ids: list[str] = Field(min_length=1, max_length=20, description="本次研究使用的知识库 kb_id")


class SolutionSource(BaseModel):
    reference: int = Field(ge=1)
    product: str = Field(min_length=1, max_length=80)
    title: str = Field(min_length=1, max_length=500)
    kb_id: str = ""
    file_id: str = ""
    chunk_id: str = ""
    url: str | None = None

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str) -> str:
        return value.replace("\\", "/").rsplit("/", 1)[-1].strip() or "知识库资料"

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str | None) -> str | None:
        if value is not None and not value.startswith(("https://", "http://")):
            raise ValueError("来源 URL 仅支持 HTTP/HTTPS")
        return value


class ExportIndustrySolutionInput(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    industry: str = Field(min_length=1, max_length=120)
    products: list[str] = Field(min_length=2, max_length=5)
    content: str = Field(min_length=1, max_length=200_000, description="不含来源列表的完整方案 Markdown")
    sources: list[SolutionSource] = Field(min_length=1, max_length=100)

    @field_validator("products")
    @classmethod
    def normalize_products(cls, products: list[str]) -> list[str]:
        return IndustrySolutionRequest(industry="export", requirement="export", products=products).products


@tool(
    category="knowledge",
    tags=["知识库", "行业解决方案"],
    display_name="分产品调研",
    args_schema=ProductResearchInput,
)
async def research_industry_products(
    industry: str,
    requirement: str,
    products: list[str],
    kb_ids: list[str],
    runtime: ToolRuntime,
) -> dict[str, Any]:
    """对每个产品分别执行真实知识库检索，并保持产品、证据和来源的关联。"""
    request = IndustrySolutionRequest(industry=industry, requirement=requirement, products=products)

    async def research_product(product: str) -> tuple[str, str, dict[str, Any]]:
        query = build_retrieval_query(product=product, industry=request.industry, requirement=request.requirement)
        result = await retrieve_kbs(kb_ids, query, runtime=runtime)
        return product, query, result

    retrieved = await asyncio.gather(*(research_product(product) for product in request.products))
    product_results: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    next_reference = 1
    for product, query, output in retrieved:
        evidence: list[dict[str, Any]] = []
        if output.get("status") == "ok":
            product_results_only = [result for result in output.get("results", []) if _matches_product(result, product)]
            for result in product_results_only[:_MAX_EVIDENCE_PER_PRODUCT]:
                source = normalize_source(result, product=product, reference=next_reference)
                evidence.append(
                    {
                        "content": result.get("content", ""),
                        "source_reference": next_reference,
                        "source": source,
                    }
                )
                sources.append(source)
                next_reference += 1
        product_results.append(
            {
                "product": product,
                "query": query,
                "status": "ok" if evidence else "insufficient",
                "reason": None if evidence else output.get("reason") or "no_product_evidence",
                "evidence": evidence,
            }
        )

    return {
        "industry": request.industry,
        "requirement": request.requirement,
        "products": product_results,
        "sources": sources,
    }


@tool(
    category="buildin",
    tags=["文件", "行业解决方案"],
    display_name="生成行业解决方案 Word",
    args_schema=ExportIndustrySolutionInput,
)
def export_industry_solution_docx(
    title: str,
    industry: str,
    products: list[str],
    content: str,
    sources: list[dict[str, Any]],
    runtime: ToolRuntime,
) -> dict[str, Any]:
    """把已经生成的方案正文和同一来源列表写入线程 Word 交付物。"""
    context = runtime.context
    thread_id = getattr(context, "file_thread_id", None) or getattr(context, "thread_id", None)
    uid = getattr(context, "uid", None)
    if not thread_id or not uid:
        raise ValueError("当前运行时缺少文件作用域")

    source_dicts = [SolutionSource.model_validate(source).model_dump(exclude_none=True) for source in sources]
    document_bytes, chat_markdown = render_solution_docx(
        title=title,
        industry=industry,
        products=products,
        content=content,
        sources=source_dicts,
    )
    ensure_thread_dirs(str(thread_id), str(uid))
    output_path = sandbox_outputs_dir(str(thread_id)) / sanitize_docx_filename(title)
    output_path.write_bytes(document_bytes)
    virtual_path = virtual_path_for_thread_file(str(thread_id), output_path, uid=str(uid))
    return {"file_path": virtual_path, "chat_markdown": chat_markdown, "sources": source_dicts}
