"""Deterministic and optional provider-backed Markdown cleaning."""

from __future__ import annotations

import asyncio
import json
import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from typing import Any

from yuxi.models import select_model

CLEANER_NAME = "deterministic_markdown"
CLEANER_VERSION = "1.0"
MAX_CHANGE_ITEMS = 200

_CONTROL_CHARACTER_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
_EXCESS_HORIZONTAL_SPACE_RE = re.compile(r"[ \t]{2,}")
_EXCESS_BLANK_LINES_RE = re.compile(r"\n{4,}")
_DECORATED_PAGE_NUMBER_RE = re.compile(
    r"^\s*(?:第\s*\d{1,5}\s*页|[-–—]\s*\d{1,5}\s*[-–—])\s*$",
    re.IGNORECASE,
)
_MARKDOWN_STRUCTURAL_RE = re.compile(
    r"^\s*(?:#{1,6}\s|[-*+]\s|\d+[.)]\s|>\s|```|~~~|\|)",
)
_SENTENCE_END_RE = re.compile(r"[。！？!?；;：:.)\]】」』”’]$")
_HTML_DANGEROUS_BLOCK_RE = re.compile(
    r"<\s*(script|style|iframe|object|embed|form|meta|link)\b[^>]*>.*?<\s*/\s*\1\s*>",
    re.IGNORECASE | re.DOTALL,
)
_HTML_DANGEROUS_SINGLE_RE = re.compile(
    r"<\s*/?\s*(script|style|iframe|object|embed|form|meta|link)\b[^>]*>",
    re.IGNORECASE,
)
_HTML_EVENT_HANDLER_RE = re.compile(
    r"\s+on[a-z]+\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^\s>]+)",
    re.IGNORECASE,
)
_HTML_JAVASCRIPT_URL_RE = re.compile(
    r"(\s+(?:href|src)\s*=\s*[\"'])\s*javascript:[^\"']*([\"'])",
    re.IGNORECASE,
)
_FACT_TOKEN_RE = re.compile(
    r"https?://[^\s)>]+|\b\d+(?:[./_-]\d+)*\b|\b[A-Z][A-Z0-9]*(?:[-_/][A-Za-z0-9.]+)+\b",
)
_CONTENT_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]*|[\u3400-\u9fff]")


class AICleaningValidationError(ValueError):
    """Raised when provider output is unsafe or violates the cleaning contract."""


@dataclass(frozen=True)
class CleaningChange:
    change_type: str
    original_text: str
    cleaned_text: str
    reason: str
    position: dict[str, Any] | None = None


@dataclass(frozen=True)
class CleaningResult:
    original_markdown: str
    cleaned_markdown: str
    cleaning_rules: list[str]
    warnings: list[str]
    changes: list[CleaningChange]
    cleaner_name: str = CLEANER_NAME
    cleaner_version: str = CLEANER_VERSION
    ai_applied: bool = False

    def to_metadata(self, *, status: str) -> dict[str, Any]:
        return {
            "cleaning_rules": list(self.cleaning_rules),
            "warnings": list(self.warnings),
            "changes": [asdict(change) for change in self.changes[:MAX_CHANGE_ITEMS]],
            "changes_truncated": len(self.changes) > MAX_CHANGE_ITEMS,
            "cleaner_name": self.cleaner_name,
            "cleaner_version": self.cleaner_version,
            "status": status,
            "ai_applied": self.ai_applied,
        }


def sanitize_markdown_html(markdown: str) -> str:
    """Remove executable HTML while preserving ordinary Markdown and safe inline HTML."""
    cleaned = _HTML_DANGEROUS_BLOCK_RE.sub("", markdown)
    cleaned = _HTML_DANGEROUS_SINGLE_RE.sub("", cleaned)
    cleaned = _HTML_EVENT_HANDLER_RE.sub("", cleaned)
    return _HTML_JAVASCRIPT_URL_RE.sub(r"\1#\2", cleaned)


def _snippet(value: str, limit: int = 240) -> str:
    normalized = " ".join(value.split())
    return normalized if len(normalized) <= limit else f"{normalized[: limit - 1]}…"


def _record_change(
    changes: list[CleaningChange],
    *,
    change_type: str,
    before: str,
    after: str,
    reason: str,
    position: dict[str, Any] | None = None,
) -> None:
    if before == after:
        return
    changes.append(
        CleaningChange(
            change_type=change_type,
            original_text=_snippet(before),
            cleaned_text=_snippet(after),
            reason=reason,
            position=position,
        )
    )


def _map_outside_fenced_code(markdown: str, transform) -> str:
    lines = markdown.split("\n")
    output: list[str] = []
    in_fence = False
    fence_marker = ""
    for line_number, line in enumerate(lines, start=1):
        stripped = line.lstrip()
        if stripped.startswith(("```", "~~~")):
            marker = stripped[:3]
            if not in_fence:
                in_fence = True
                fence_marker = marker
            elif marker == fence_marker:
                in_fence = False
                fence_marker = ""
            output.append(line)
            continue
        output.append(line if in_fence else transform(line, line_number))
    return "\n".join(output)


def _normalize_horizontal_spacing(markdown: str, changes: list[CleaningChange]) -> str:
    def transform(line: str, line_number: int) -> str:
        if not line.strip() or _MARKDOWN_STRUCTURAL_RE.match(line):
            return line.rstrip()
        if "://" in line or re.search(r"\b[A-Za-z]:[\\/]", line) or "`" in line:
            return line.rstrip()
        cleaned = _EXCESS_HORIZONTAL_SPACE_RE.sub(" ", line).rstrip()
        _record_change(
            changes,
            change_type="normalize_spacing",
            before=line,
            after=cleaned,
            reason="合并正文中的异常连续空格",
            position={"line": line_number},
        )
        return cleaned

    return _map_outside_fenced_code(markdown, transform)


def _remove_isolated_page_numbers(markdown: str, changes: list[CleaningChange]) -> str:
    def transform(line: str, line_number: int) -> str:
        if not _DECORATED_PAGE_NUMBER_RE.match(line):
            return line
        _record_change(
            changes,
            change_type="remove_page_number",
            before=line,
            after="",
            reason="删除具有明确页码格式的孤立行",
            position={"line": line_number},
        )
        return ""

    return _map_outside_fenced_code(markdown, transform)


def _page_edge_candidates(parse_metadata: dict[str, Any] | None) -> set[str]:
    blocks = (parse_metadata or {}).get("blocks")
    if not isinstance(blocks, list):
        return set()

    pages: dict[int, list[tuple[int, str]]] = defaultdict(list)
    for block in blocks:
        if not isinstance(block, dict):
            continue
        page_number = block.get("page_number")
        if not isinstance(page_number, int) or page_number <= 0:
            continue
        text = str(block.get("markdown") or block.get("text") or "").strip()
        if not text:
            continue
        order = int(block.get("order") or len(pages[page_number]))
        pages[page_number].append((order, text))

    if len(pages) < 3:
        return set()

    edge_counts: Counter[str] = Counter()
    for page_blocks in pages.values():
        ordered = [text for _, text in sorted(page_blocks)]
        page_lines = [line.strip() for text in ordered for line in text.splitlines() if line.strip()]
        if not page_lines:
            continue
        for candidate in {page_lines[0], page_lines[-1]}:
            if (
                3 <= len(candidate) <= 120
                and not _MARKDOWN_STRUCTURAL_RE.match(candidate)
                and not _DECORATED_PAGE_NUMBER_RE.match(candidate)
            ):
                edge_counts[candidate] += 1

    threshold = max(3, (len(pages) * 3 + 4) // 5)
    return {candidate for candidate, count in edge_counts.items() if count >= threshold}


def _remove_repeated_page_edges(
    markdown: str,
    parse_metadata: dict[str, Any] | None,
    changes: list[CleaningChange],
) -> str:
    candidates = _page_edge_candidates(parse_metadata)
    if not candidates:
        return markdown

    def transform(line: str, line_number: int) -> str:
        if line.strip() not in candidates:
            return line
        _record_change(
            changes,
            change_type="remove_repeated_page_edge",
            before=line,
            after="",
            reason="该行在至少三页的页首或页尾重复出现",
            position={"line": line_number},
        )
        return ""

    return _map_outside_fenced_code(markdown, transform)


def _is_joinable_line(line: str) -> bool:
    stripped = line.strip()
    return bool(stripped) and not _MARKDOWN_STRUCTURAL_RE.match(line) and not stripped.endswith("|")


def _repair_soft_line_breaks(markdown: str, changes: list[CleaningChange]) -> str:
    lines = markdown.split("\n")
    output: list[str] = []
    in_fence = False
    fence_marker = ""
    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.lstrip()
        if stripped.startswith(("```", "~~~")):
            marker = stripped[:3]
            if not in_fence:
                in_fence = True
                fence_marker = marker
            elif marker == fence_marker:
                in_fence = False
                fence_marker = ""
            output.append(line)
            index += 1
            continue

        if (
            not in_fence
            and index + 1 < len(lines)
            and _is_joinable_line(line)
            and _is_joinable_line(lines[index + 1])
            and not _SENTENCE_END_RE.search(line.rstrip())
        ):
            next_line = lines[index + 1].strip()
            current = line.rstrip()
            joins_cjk = re.search(r"[\u3400-\u9fff]$", current) and re.match(
                r"^[\u3400-\u9fff]",
                next_line,
            )
            separator = "" if joins_cjk else " "
            joined = f"{current}{separator}{next_line}"
            _record_change(
                changes,
                change_type="repair_line_break",
                before=f"{line}\n{lines[index + 1]}",
                after=joined,
                reason="合并疑似由版面换行产生的正文断行",
                position={"line": index + 1},
            )
            output.append(joined)
            index += 2
            continue

        output.append(line)
        index += 1
    return "\n".join(output)


def _remove_consecutive_duplicate_paragraphs(markdown: str, changes: list[CleaningChange]) -> str:
    paragraphs = re.split(r"\n{2,}", markdown)
    output: list[str] = []
    previous_normalized: str | None = None
    for index, paragraph in enumerate(paragraphs):
        normalized = paragraph.strip()
        protected = (
            "```" in paragraph
            or "~~~" in paragraph
            or any(line.lstrip().startswith("|") for line in paragraph.splitlines())
        )
        if normalized and normalized == previous_normalized and not protected:
            _record_change(
                changes,
                change_type="remove_duplicate_paragraph",
                before=paragraph,
                after="",
                reason="删除与上一段完全相同的连续段落",
                position={"paragraph": index + 1},
            )
            continue
        output.append(paragraph)
        previous_normalized = normalized if normalized else previous_normalized
    return "\n\n".join(output)


class RuleBasedDocumentCleaner:
    """Conservative rules that do not require an external provider."""

    def clean(
        self,
        markdown: str,
        *,
        parse_metadata: dict[str, Any] | None = None,
    ) -> CleaningResult:
        if not isinstance(markdown, str) or not markdown.strip():
            raise ValueError("原始解析内容为空，不能生成清洗草稿")

        changes: list[CleaningChange] = []
        rules: list[str] = []

        cleaned = markdown.replace("\r\n", "\n").replace("\r", "\n")
        _record_change(
            changes,
            change_type="normalize_newlines",
            before=markdown,
            after=cleaned,
            reason="统一换行符",
        )
        rules.append("normalize_newlines")

        normalized = unicodedata.normalize("NFC", cleaned)
        _record_change(
            changes,
            change_type="normalize_unicode",
            before=cleaned,
            after=normalized,
            reason="使用 Unicode NFC 规范化",
        )
        cleaned = normalized
        rules.append("normalize_unicode_nfc")

        without_controls = _CONTROL_CHARACTER_RE.sub("", cleaned)
        _record_change(
            changes,
            change_type="remove_control_characters",
            before=cleaned,
            after=without_controls,
            reason="删除不可见控制字符",
        )
        cleaned = without_controls
        rules.append("remove_control_characters")

        safe_html = sanitize_markdown_html(cleaned)
        _record_change(
            changes,
            change_type="sanitize_html",
            before=cleaned,
            after=safe_html,
            reason="删除 Markdown 中可执行的危险 HTML",
        )
        cleaned = safe_html
        rules.append("sanitize_dangerous_html")

        cleaned = _normalize_horizontal_spacing(cleaned, changes)
        rules.append("normalize_horizontal_spacing")
        cleaned = _remove_isolated_page_numbers(cleaned, changes)
        rules.append("remove_decorated_page_numbers")
        cleaned = _remove_repeated_page_edges(cleaned, parse_metadata, changes)
        rules.append("remove_repeated_page_headers_footers")
        cleaned = _repair_soft_line_breaks(cleaned, changes)
        rules.append("repair_soft_line_breaks")
        cleaned = _remove_consecutive_duplicate_paragraphs(cleaned, changes)
        rules.append("remove_consecutive_duplicate_paragraphs")

        compacted = _EXCESS_BLANK_LINES_RE.sub("\n\n\n", cleaned).strip() + "\n"
        _record_change(
            changes,
            change_type="normalize_blank_lines",
            before=cleaned,
            after=compacted,
            reason="将连续空行限制为最多两个",
        )
        cleaned = compacted
        rules.append("normalize_blank_lines")

        if not cleaned.strip():
            raise ValueError("清洗结果为空")
        return CleaningResult(
            original_markdown=markdown,
            cleaned_markdown=cleaned,
            cleaning_rules=rules,
            warnings=[],
            changes=changes,
        )


def _extract_json_object(value: str) -> dict[str, Any]:
    text = value.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise AICleaningValidationError("AI 清洗结果不是有效 JSON") from exc
    if not isinstance(payload, dict):
        raise AICleaningValidationError("AI 清洗结果必须是对象")
    return payload


def validate_ai_cleaned_markdown(original: str, cleaned: str) -> None:
    if not cleaned.strip():
        raise AICleaningValidationError("AI 清洗结果为空")
    if len(cleaned) > len(original) * 1.15 + 200:
        raise AICleaningValidationError("AI 清洗结果异常扩写")

    original_facts = set(_FACT_TOKEN_RE.findall(original))
    added_facts = sorted(set(_FACT_TOKEN_RE.findall(cleaned)) - original_facts)
    if added_facts:
        raise AICleaningValidationError("AI 清洗结果包含原文中不存在的数字、链接或型号")

    original_tokens = Counter(token.casefold() for token in _CONTENT_TOKEN_RE.findall(original))
    cleaned_tokens = Counter(token.casefold() for token in _CONTENT_TOKEN_RE.findall(cleaned))
    if any(count > original_tokens[token] for token, count in cleaned_tokens.items()):
        raise AICleaningValidationError("AI 清洗结果包含原文中不存在或额外增加的文字")


def _split_for_ai(markdown: str, max_chars: int) -> list[str]:
    if len(markdown) <= max_chars:
        return [markdown]
    paragraphs = re.split(r"(\n{2,})", markdown)
    chunks: list[str] = []
    current = ""
    for part in paragraphs:
        if current and len(current) + len(part) > max_chars:
            chunks.append(current)
            current = ""
        if len(part) > max_chars:
            if current:
                chunks.append(current)
                current = ""
            chunks.extend(part[index : index + max_chars] for index in range(0, len(part), max_chars))
        else:
            current += part
    if current:
        chunks.append(current)
    return chunks


class OptionalAIDocumentCleaner:
    """Optional second pass through the existing configured chat-model boundary."""

    def __init__(self, rule_cleaner: RuleBasedDocumentCleaner | None = None):
        self.rule_cleaner = rule_cleaner or RuleBasedDocumentCleaner()

    async def clean(
        self,
        markdown: str,
        *,
        parse_metadata: dict[str, Any] | None = None,
        enabled: bool = False,
        model_spec: str | None = None,
        temperature: float = 0.0,
        timeout_seconds: float = 60.0,
        chunk_chars: int = 12_000,
    ) -> CleaningResult:
        rule_result = self.rule_cleaner.clean(markdown, parse_metadata=parse_metadata)
        if not enabled or not model_spec:
            warning = "AI Cleaner 未配置，已仅使用确定性规则" if enabled and not model_spec else None
            return CleaningResult(
                original_markdown=rule_result.original_markdown,
                cleaned_markdown=rule_result.cleaned_markdown,
                cleaning_rules=rule_result.cleaning_rules,
                warnings=[*rule_result.warnings, *([warning] if warning else [])],
                changes=rule_result.changes,
                cleaner_name=rule_result.cleaner_name,
                cleaner_version=rule_result.cleaner_version,
                ai_applied=False,
            )

        try:
            model = select_model(model_spec=model_spec, temperature=temperature)
            cleaned_parts: list[str] = []
            ai_changes: list[CleaningChange] = []
            for index, part in enumerate(_split_for_ai(rule_result.cleaned_markdown, max(1000, chunk_chars))):
                prompt = [
                    {
                        "role": "system",
                        "content": (
                            "你是文档排版清洗器。只能整理排版、去除噪声、修复明显断行并规范标题层级；"
                            "必须保持原意，禁止扩写、总结、推断或增加任何新事实。"
                            '仅返回 JSON：{"cleaned_markdown":"...","changes":['
                            '{"change_type":"...","reason":"..."}]}。'
                        ),
                    },
                    {"role": "user", "content": part},
                ]
                response = await asyncio.wait_for(model.model.ainvoke(prompt), timeout=max(1.0, timeout_seconds))
                raw_content = getattr(response, "text", None) or getattr(response, "content", None) or ""
                payload = _extract_json_object(str(raw_content))
                cleaned_part = payload.get("cleaned_markdown")
                if not isinstance(cleaned_part, str):
                    raise AICleaningValidationError("AI 清洗结果缺少 cleaned_markdown")
                validate_ai_cleaned_markdown(part, cleaned_part)
                cleaned_parts.append(cleaned_part)
                for change in payload.get("changes") or []:
                    if not isinstance(change, dict):
                        continue
                    ai_changes.append(
                        CleaningChange(
                            change_type=str(change.get("change_type") or "ai_formatting"),
                            original_text="",
                            cleaned_text="",
                            reason=_snippet(str(change.get("reason") or "AI 排版整理")),
                            position={"ai_chunk": index + 1},
                        )
                    )

            ai_markdown = "".join(cleaned_parts)
            validate_ai_cleaned_markdown(rule_result.cleaned_markdown, ai_markdown)
            return CleaningResult(
                original_markdown=rule_result.original_markdown,
                cleaned_markdown=ai_markdown,
                cleaning_rules=[*rule_result.cleaning_rules, "optional_ai_formatting"],
                warnings=rule_result.warnings,
                changes=[*rule_result.changes, *ai_changes],
                cleaner_name=f"{CLEANER_NAME}+configured_ai",
                cleaner_version=CLEANER_VERSION,
                ai_applied=True,
            )
        except Exception as exc:  # noqa: BLE001 - provider failure must preserve deterministic output
            return CleaningResult(
                original_markdown=rule_result.original_markdown,
                cleaned_markdown=rule_result.cleaned_markdown,
                cleaning_rules=rule_result.cleaning_rules,
                warnings=[*rule_result.warnings, f"AI Cleaner 跳过：{type(exc).__name__}"],
                changes=rule_result.changes,
                cleaner_name=rule_result.cleaner_name,
                cleaner_version=rule_result.cleaner_version,
                ai_applied=False,
            )


__all__ = [
    "AICleaningValidationError",
    "CleaningChange",
    "CleaningResult",
    "OptionalAIDocumentCleaner",
    "RuleBasedDocumentCleaner",
    "sanitize_markdown_html",
    "validate_ai_cleaned_markdown",
]
