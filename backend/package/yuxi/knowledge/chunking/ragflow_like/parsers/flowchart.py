"""Section-aware chunks for confirmed flowchart semantic Markdown."""

from __future__ import annotations

import re
from typing import Any

from yuxi.knowledge.chunking.ragflow_like import nlp
from yuxi.knowledge.chunking.ragflow_like.parsers import general

PAGE_MARKER = re.compile(r"(?m)^<!-- page:(\d+) -->\s*$")
SECTION_HEADING = re.compile(r"(?m)^## (.+?)\s*$")


def chunk_markdown(markdown: str, parser_config: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Keep flow name, section and page context in each searchable chunk."""
    from yuxi.knowledge.flowchart_analysis import SECTION_NAMES, validate_semantic_markdown

    validate_semantic_markdown(markdown)
    parser_config = parser_config or {}
    token_limit = int(parser_config.get("chunk_token_num", 512) or 512)
    title = markdown.splitlines()[0][2:].strip()
    headings = list(SECTION_HEADING.finditer(markdown))
    chunks: list[dict[str, Any]] = []

    for index, heading in enumerate(headings):
        section = heading.group(1).strip()
        if section not in SECTION_NAMES:
            raise ValueError(f"Unexpected flowchart section: {section}")
        end = headings[index + 1].start() if index + 1 < len(headings) else len(markdown)
        body = markdown[heading.end() : end].strip()
        markers = list(PAGE_MARKER.finditer(body))
        passages = (
            [
                (
                    int(marker.group(1)),
                    body[
                        marker.end() : markers[pos + 1].start() if pos + 1 < len(markers) else len(body)
                    ].strip(),
                )
                for pos, marker in enumerate(markers)
            ]
            if markers
            else [(None, body)]
        )
        if markers and (unmarked := body[: markers[0].start()].strip()):
            passages.insert(0, (None, unmarked))

        for page_number, passage in passages:
            if not passage or passage == "未识别到明确内容":
                continue
            prefix = f"流程：{title}\n章节：{section}"
            if page_number is not None:
                prefix += f"\n页码：{page_number}"
            content = f"{prefix}\n\n{passage}"
            parts = [passage]
            if nlp.count_tokens(content) > token_limit:
                parts = general.chunk_markdown(passage, parser_config)
            for part in parts:
                chunks.append(
                    {
                        "content": f"{prefix}\n\n{part}",
                        "section": section,
                        "page_numbers": [page_number] if page_number is not None else [],
                    }
                )
    if not chunks:
        raise ValueError("Flowchart has no indexable semantic sections")
    return chunks
