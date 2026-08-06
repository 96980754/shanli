"""文档清洗 V2：文档增强流水线。

不再把整篇文档一次性丢给 LLM 重写，而是：
    Parser → 规则清洗 → 按标题分 Section → 并行 LLM 局部增强 → 合并 → 质量校验

- 规则清洗（零 LLM）先去掉页码/页眉/连续空行/硬换行等格式噪声。
- LLM 只做局部结构增强（标题补全、段落合并、表格转 markdown），不全文重写。
- 多 section 用 asyncio.gather 并行，单 section 失败原样保留，不阻塞整体。
- Quality Validator 校验数字/实体/长度一致性，严重丢失时回退原文。

复用导图/示例问题同款的模型调用链路（select_model + LangChainChatAdapter.call）。
QA 对生成留待后续阶段（走 chunk 级抽取，不走全文生成）。
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

from yuxi import config
from yuxi.models import select_model
from yuxi.utils import logger

from .document_quality_validator import validate_clean_result
from .document_rule_cleaner import rule_clean_markdown
from .document_section_splitter import split_markdown_by_headings

# 单 section 并行 LLM 的并发上限，避免一次全发压垮模型接口
_SECTION_CONCURRENCY = 8

# 局部结构增强 prompt：只针对单 section，不做全文重写，禁止改数字/专名/增删内容
SECTION_ENHANCE_SYSTEM_PROMPT = (
    "你是一名文档结构整理助手。请对下面这一小段文档内容做局部结构整理，输出规范 Markdown。\n"
    "要求：\n"
    "1. 只修正呈现结构：调整标题层级、合并被换行切断的句子、把散乱的表格整理为 Markdown 表格、规整列表。\n"
    "2. 严格保真：保留所有数字、产品名、专有名词、单位原样（如 4GB、MCSTARS、WiFi、蓝牙），不得改写措辞、"
    "不得总结压缩、不得增删或编造内容。\n"
    "3. 若该段本已结构清晰，可原样返回，不必强行改动。\n"
    "4. 仅返回整理后的 Markdown 文本本身，不要任何解释、前言或代码块包裹。"
)


def build_section_enhance_user_message(section: dict) -> str:
    # 只传正文，不传标题，避免模型在输出中重复标题；标题由 _reassemble 统一拼接
    return section.get("content", "")


async def _enhance_section(section: dict, model: Any) -> str:
    """对单个 section 做局部结构增强。失败返回原文，不阻塞整体。"""
    try:
        messages = [
            {"role": "system", "content": SECTION_ENHANCE_SYSTEM_PROMPT},
            {"role": "user", "content": build_section_enhance_user_message(section)},
        ]
        response = await model.call(messages, stream=False)
        content = response.content if hasattr(response, "content") else str(response)
        cleaned = str(content or "").strip()
        return cleaned or section.get("content", "")
    except Exception as e:
        logger.warning(f"Section 清洗失败，保留原文: {e}")
        return section.get("content", "")


async def _enhance_sections(sections: list[dict], model: Any) -> list[str]:
    """并行增强所有 section，返回按顺序的增强结果。"""
    semaphore = asyncio.Semaphore(_SECTION_CONCURRENCY)

    async def _limited(section: dict) -> str:
        async with semaphore:
            return await _enhance_section(section, model)

    return list(await asyncio.gather(*(_limited(s) for s in sections)))


def _reassemble(sections: list[dict], enhanced: list[str]) -> str:
    """按顺序重组 section，按层级生成标题行 + 增强内容。"""
    parts: list[str] = []
    for section, body in zip(sections, enhanced):
        title = section.get("title")
        level = int(section.get("level") or 0)
        if title:
            parts.append(f"{'#' * max(level, 1)} {title}")
        if body:
            parts.append(body)
    return "\n\n".join(p for p in parts if p).strip()


async def clean_document_markdown(raw_markdown: str) -> dict:
    """V2 流水线：规则清洗 → 分 section → 并行增强 → 合并 → 校验。

    Returns:
        dict: {cleaned_markdown: str, warnings: list[str]}
    """
    if not raw_markdown or not raw_markdown.strip():
        raise ValueError("待清洗的文档内容为空")

    original = raw_markdown.strip()

    # 1. 规则清洗（零 LLM）
    rule_cleaned = rule_clean_markdown(original)
    if not rule_cleaned:
        rule_cleaned = original

    # 2. 按标题分 section
    sections = split_markdown_by_headings(rule_cleaned)
    if not sections:
        # 无法切分（无标题无段落），原样返回
        return {"cleaned_markdown": rule_cleaned, "warnings": ["文档结构过于简单，未做增强"]}

    # 3. 并行 LLM 增强
    model = select_model(model_spec=config.default_model)
    enhanced = await _enhance_sections(sections, model)

    # 4. 合并
    cleaned = _reassemble(sections, enhanced)
    if not cleaned:
        cleaned = rule_cleaned

    # 5. 质量校验
    result = validate_clean_result(rule_cleaned, cleaned)
    if result["should_fallback"]:
        logger.warning(f"清洗结果校验失败，回退原文: {result['warnings']}")
        return {"cleaned_markdown": rule_cleaned, "warnings": result["warnings"]}

    return {"cleaned_markdown": cleaned, "warnings": result["warnings"]}


async def clean_document_file(kb_id: str, file_path: str, filename: str | None = None) -> dict:
    """从已上传的 MinIO 原始文件读取并解析为 markdown，再走 V2 清洗流水线。"""
    import tempfile

    from yuxi.knowledge.parser.unified import Parser
    from yuxi.knowledge.runtime import knowledge_base

    kb_instance = await knowledge_base._get_kb_for_database(kb_id)
    raw_bytes = await kb_instance._read_minio_bytes(file_path)
    suffix = os.path.splitext(filename or "")[1].lower() or ".bin"
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(raw_bytes)
            temp_path = tmp.name
        raw_markdown = await Parser.aparse(temp_path)
        return await clean_document_markdown(raw_markdown)
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.unlink(temp_path)
            except OSError:
                pass


def document_clean_error_detail(exc: Any) -> str:
    """清洗失败时返回安全的外部文案，内部日志保留真实异常。"""
    logger.warning(f"文档清洗失败: {exc}")
    return "文档清洗失败，请稍后重试"
