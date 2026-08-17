"""Validation and provider-backed generation for document enrichment."""

from __future__ import annotations
import asyncio
import hashlib
import json
import re
import unicodedata
from typing import Any
from yuxi.models import select_model

ENRICHMENT_COMPONENTS = frozenset({"summary", "keywords", "tags"})
GENERATOR_VERSION = "1.0"
_FACT_TOKEN_RE = re.compile(
    r"https?://[^\s)>]+|\b\d+(?:[./_-]\d+)*\b|\b[A-Z][A-Z0-9]*(?:[-_/][A-Za-z0-9.]+)+\b",
)
_SPACE_RE = re.compile(r"\s+")
_STOPWORDS = {
    "a",
    "an",
    "and",
    "for",
    "in",
    "of",
    "or",
    "the",
    "to",
    "与",
    "了",
    "及",
    "和",
    "在",
    "是",
    "的",
}


class EnrichmentValidationError(ValueError):
    """Raised when generated content violates the document enrichment contract."""


class EnrichmentProviderUnavailable(RuntimeError):
    """Raised when no configured model can generate enrichment."""


def formal_content_hash(markdown: str) -> str:
    return hashlib.sha256(markdown.encode("utf-8")).hexdigest()


def normalize_label(value: str) -> tuple[str, str]:
    display = _SPACE_RE.sub(" ", unicodedata.normalize("NFKC", str(value))).strip()
    return display, display.casefold()


def validate_summary(markdown: str, summary: str, *, max_chars: int) -> str:
    normalized = str(summary).strip()
    if not normalized:
        raise EnrichmentValidationError("摘要为空")
    if len(normalized) > max(1, int(max_chars)):
        raise EnrichmentValidationError("摘要超过允许的最大长度")
    source_facts = set(_FACT_TOKEN_RE.findall(markdown))
    added_facts = sorted(set(_FACT_TOKEN_RE.findall(normalized)) - source_facts)
    if added_facts:
        raise EnrichmentValidationError("摘要包含原文中不存在的数字、链接或型号")
    return normalized


def normalize_keywords(values: list[Any], markdown: str, *, limit: int) -> list[dict[str, str]]:
    normalized_markdown = unicodedata.normalize("NFKC", markdown).casefold()
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in values:
        raw_value = item.get("value") if isinstance(item, dict) else item
        value, normalized_value = normalize_label(str(raw_value or ""))
        if (
            not value
            or normalized_value in seen
            or normalized_value in _STOPWORDS
            or normalized_value not in normalized_markdown
        ):
            continue
        seen.add(normalized_value)
        result.append({"value": value, "normalized_value": normalized_value})
        if len(result) >= max(1, int(limit)):
            break
    return result


def normalize_tags(values: list[Any], *, limit: int) -> list[dict[str, str | None]]:
    result: list[dict[str, str | None]] = []
    seen: set[str] = set()
    for item in values:
        raw_name = item.get("name") if isinstance(item, dict) else item
        name, normalized_name = normalize_label(str(raw_name or ""))
        if not name or normalized_name in seen:
            continue
        seen.add(normalized_name)
        result.append(
            {
                "name": name,
                "normalized_name": normalized_name,
                "taxonomy_id": item.get("taxonomy_id") if isinstance(item, dict) else None,
            }
        )
        if len(result) >= max(1, int(limit)):
            break
    return result


def mark_enrichment_data_outdated(data: dict[str, Any] | None) -> dict[str, Any]:
    component_sources = dict((data or {}).get("component_sources") or {})
    component_statuses = dict((data or {}).get("component_statuses") or {})
    updated = {
        "summary": dict((data or {}).get("summary") or {}),
        "keywords": [dict(item) for item in (data or {}).get("keywords") or []],
        "tags": [dict(item) for item in (data or {}).get("tags") or []],
        "component_sources": component_sources,
        "component_statuses": component_statuses,
    }
    if updated["summary"]:
        updated["summary"]["status"] = "possibly_outdated"
    for component in ("keywords", "tags"):
        if component not in component_sources and updated[component]:
            component_sources[component] = str(updated[component][0].get("source") or "")
        if component_sources.get(component):
            component_statuses[component] = "possibly_outdated"
        for item in updated[component]:
            item["status"] = "possibly_outdated"
    return updated


def _extract_json_object(value: str) -> dict[str, Any]:
    text = value.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise EnrichmentValidationError("模型输出不是有效 JSON") from exc
    if not isinstance(payload, dict):
        raise EnrichmentValidationError("模型输出必须是 JSON 对象")
    return payload


def _split_markdown(markdown: str, max_chars: int) -> list[str]:
    if len(markdown) <= max_chars:
        return [markdown]
    chunks: list[str] = []
    current = ""
    for paragraph in re.split(r"(\n{2,})", markdown):
        if current and len(current) + len(paragraph) > max_chars:
            chunks.append(current)
            current = ""
        if len(paragraph) > max_chars:
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(paragraph[index : index + max_chars] for index in range(0, len(paragraph), max_chars))
        else:
            current += paragraph
    if current:
        chunks.append(current)
    return chunks


class DocumentEnrichmentGenerator:
    """Generate validated structured enrichment through the configured model boundary."""

    async def _call_json(
        self,
        model,
        messages: list[dict[str, str]],
        *,
        timeout_seconds: float,
        attempts: int,
    ) -> dict[str, Any]:
        last_error: Exception | None = None
        prompt = list(messages)
        for attempt in range(max(1, min(int(attempts), 2))):
            try:
                response = await asyncio.wait_for(
                    model.model.ainvoke(prompt),
                    timeout=max(1.0, float(timeout_seconds)),
                )
                content = getattr(response, "text", None) or getattr(response, "content", None) or ""
                return _extract_json_object(str(content))
            except Exception as exc:  # noqa: BLE001 - one bounded repair attempt is part of the contract
                last_error = exc
                if attempt == 0:
                    prompt = [
                        *messages,
                        {
                            "role": "system",
                            "content": "上次输出无效。请严格返回约定的 JSON 对象，不要使用 Markdown 代码块。",
                        },
                    ]
        raise EnrichmentValidationError("模型未返回有效的结构化结果") from last_error

    async def generate(
        self,
        markdown: str,
        *,
        components: set[str],
        model_spec: str | None,
        temperature: float,
        timeout_seconds: float,
        chunk_chars: int,
        attempts: int,
        summary_max_chars: int,
        keyword_limit: int,
        tag_limit: int,
    ) -> dict[str, Any]:
        requested = set(components) & ENRICHMENT_COMPONENTS
        if not requested:
            raise EnrichmentValidationError("没有需要生成的信息增强组件")
        if not model_spec:
            raise EnrichmentProviderUnavailable("文档信息增强模型未配置")
        model = select_model(model_spec=model_spec, temperature=temperature)
        partials: list[dict[str, Any]] = []
        for chunk in _split_markdown(markdown, max(1000, int(chunk_chars))):
            partials.append(
                await self._call_json(
                    model,
                    [
                        {
                            "role": "system",
                            "content": (
                                "你是文档信息提取器。只能依据用户提供的正文，不得扩写、推断或增加事实。"
                                "返回 JSON 对象，可包含 summary（字符串）、keywords（字符串数组）、tags（字符串数组）。"
                                "摘要应简短并保留产品名、版本号、型号、时间和关键数字；关键词必须能在原文中直接找到；"
                                "标签使用简短自由标签。不要输出推理过程。"
                            ),
                        },
                        {"role": "user", "content": chunk},
                    ],
                    timeout_seconds=timeout_seconds,
                    attempts=attempts,
                )
            )
        payload = partials[0]
        if len(partials) > 1:
            payload = await self._call_json(
                model,
                [
                    {
                        "role": "system",
                        "content": (
                            "合并下列分块提取结果，只能删减、去重和压缩，不能增加任何新事实。"
                            "严格返回 summary、keywords、tags 组成的 JSON 对象。"
                        ),
                    },
                    {"role": "user", "content": json.dumps(partials, ensure_ascii=False)},
                ],
                timeout_seconds=timeout_seconds,
                attempts=attempts,
            )
        result: dict[str, Any] = {
            "model_name": str(getattr(model, "model_name", "") or model_spec),
            "model_version": GENERATOR_VERSION,
        }
        if "summary" in requested:
            summary_value = payload.get("summary")
            if isinstance(summary_value, dict):
                summary_value = summary_value.get("text")
            result["summary"] = validate_summary(markdown, str(summary_value or ""), max_chars=summary_max_chars)
        if "keywords" in requested:
            keyword_values = payload.get("keywords")
            if not isinstance(keyword_values, list):
                raise EnrichmentValidationError("关键词输出必须是数组")
            normalized_keywords = [
                item["value"] for item in normalize_keywords(keyword_values, markdown, limit=keyword_limit)
            ]
            if not normalized_keywords:
                raise EnrichmentValidationError("关键词没有可在原文中验证的依据")
            result["keywords"] = normalized_keywords
        if "tags" in requested:
            tag_values = payload.get("tags")
            if not isinstance(tag_values, list):
                raise EnrichmentValidationError("标签输出必须是数组")
            normalized_tags = [item["name"] for item in normalize_tags(tag_values, limit=tag_limit)]
            if not normalized_tags:
                raise EnrichmentValidationError("标签输出为空")
            result["tags"] = normalized_tags
        return result


__all__ = [
    "DocumentEnrichmentGenerator",
    "ENRICHMENT_COMPONENTS",
    "EnrichmentProviderUnavailable",
    "EnrichmentValidationError",
    "formal_content_hash",
    "mark_enrichment_data_outdated",
    "normalize_keywords",
    "normalize_tags",
    "validate_summary",
]
