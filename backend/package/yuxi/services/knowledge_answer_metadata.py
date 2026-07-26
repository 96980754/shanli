"""知识库问答消息元数据处理。

负责从 query_kb 工具消息中提取结构化证据，
并将证据绑定到最终助手消息。
"""

from __future__ import annotations

import ast
import json
from typing import Any


_SEARCH_STATUS_TO_ANSWER_STATUS = {
    "sufficient": "answered",
    "insufficient": "refused",
    "error": "error",
}


def message_text(content: Any) -> str:
    """从 LangChain 消息内容中提取纯文本。"""

    if isinstance(content, str):
        return content

    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(part for part in parts if part)

    if content is None:
        return ""

    return str(content)


def _parse_mapping(value: Any) -> dict[str, Any] | None:
    """兼容工具直接返回 dict、JSON 字符串和 Python 字典字符串。"""

    if isinstance(value, dict):
        return dict(value)

    if isinstance(value, list) and len(value) == 1 and isinstance(value[0], dict):
        return dict(value[0])

    if not isinstance(value, str):
        return None

    text = value.strip()
    if not text:
        return None

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        try:
            parsed = ast.literal_eval(text)
        except (ValueError, SyntaxError):
            return None

    return dict(parsed) if isinstance(parsed, dict) else None


def _normalized_citations(payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw_citations = payload.get("citations")
    if not isinstance(raw_citations, list):
        return []

    citations: list[dict[str, Any]] = []

    for item in raw_citations:
        if not isinstance(item, dict):
            continue

        chunk_id = str(item.get("chunk_id") or "").strip()
        kb_id = str(item.get("kb_id") or payload.get("kb_id") or "").strip()

        # 没有片段标识的引用无法稳定追溯，不进行持久化。
        if not chunk_id:
            continue

        citations.append(
            {
                "citation_id": str(item.get("citation_id") or ""),
                "kb_id": kb_id,
                "file_id": str(item.get("file_id") or ""),
                "chunk_id": chunk_id,
                "file_name": item.get("file_name"),
                "quote": str(item.get("quote") or ""),
                "chunk_index": item.get("chunk_index"),
                "updated_at": item.get("updated_at"),
                "score": item.get("score"),
                "rerank_score": item.get("rerank_score"),
            }
        )

    return citations


def extract_query_kb_evidence(
    tool_message: dict[str, Any],
) -> dict[str, Any] | None:
    """从 query_kb 的 ToolMessage 中提取证据摘要。"""

    tool_name = str(tool_message.get("name") or "").strip()
    if tool_name != "query_kb":
        return None

    payload = _parse_mapping(tool_message.get("content"))
    if payload is None:
        return None

    search_status = str(payload.get("status") or "").strip()
    answer_status = _SEARCH_STATUS_TO_ANSWER_STATUS.get(search_status)

    if answer_status is None:
        return None

    kb_id = str(payload.get("kb_id") or "").strip()
    top_score = payload.get("top_score")

    return {
        "search_status": search_status,
        "answer_status": answer_status,
        "reason": payload.get("reason"),
        "kb_ids": [kb_id] if kb_id else [],
        "top_score": top_score,
        "score_type": payload.get("score_type"),
        "citations": _normalized_citations(payload),
        "searches": [
            {
                "kb_id": kb_id,
                "status": search_status,
                "reason": payload.get("reason"),
                "top_score": top_score,
                "score_type": payload.get("score_type"),
            }
        ],
    }


def _max_optional_score(left: Any, right: Any) -> float | None:
    values: list[float] = []

    for value in (left, right):
        if value is None or isinstance(value, bool):
            continue
        try:
            values.append(float(value))
        except (TypeError, ValueError):
            continue

    return max(values) if values else None


def merge_knowledge_evidence(
    current: dict[str, Any] | None,
    incoming: dict[str, Any],
) -> dict[str, Any]:
    """合并同一轮中的多次 query_kb 调用。"""

    if not current:
        return dict(incoming)

    searches = [
        item
        for item in [
            *(current.get("searches") or []),
            *(incoming.get("searches") or []),
        ]
        if isinstance(item, dict)
    ]

    kb_ids: list[str] = []
    for kb_id in [
        *(current.get("kb_ids") or []),
        *(incoming.get("kb_ids") or []),
    ]:
        normalized = str(kb_id or "").strip()
        if normalized and normalized not in kb_ids:
            kb_ids.append(normalized)

    citations: list[dict[str, Any]] = []
    seen_citations: set[tuple[str, str, str]] = set()

    for citation in [
        *(current.get("citations") or []),
        *(incoming.get("citations") or []),
    ]:
        if not isinstance(citation, dict):
            continue

        key = (
            str(citation.get("kb_id") or ""),
            str(citation.get("file_id") or ""),
            str(citation.get("chunk_id") or ""),
        )
        if key in seen_citations:
            continue

        seen_citations.add(key)
        citations.append(citation)

    statuses = {
        str(item.get("answer_status") or "")
        for item in (current, incoming)
    }

    if "answered" in statuses:
        answer_status = "answered"
        reason = None
        search_status = "sufficient"
    elif "refused" in statuses:
        answer_status = "refused"
        search_status = "insufficient"
        reason = incoming.get("reason") or current.get("reason")
    else:
        answer_status = "error"
        search_status = "error"
        reason = incoming.get("reason") or current.get("reason")

    return {
        "search_status": search_status,
        "answer_status": answer_status,
        "reason": reason,
        "kb_ids": kb_ids,
        "top_score": _max_optional_score(
            current.get("top_score"),
            incoming.get("top_score"),
        ),
        "score_type": incoming.get("score_type") or current.get("score_type"),
        "citations": citations,
        "searches": searches,
    }


def is_final_assistant_message(message: dict[str, Any]) -> bool:
    """判断 AI 消息是不是本轮最终自然语言回答。"""

    tool_calls = message.get("tool_calls") or []
    content = message_text(message.get("content")).strip()

    return bool(content) and not tool_calls


def apply_knowledge_evidence(
    message: dict[str, Any],
    evidence: dict[str, Any],
    *,
    question: str | None = None,
) -> dict[str, Any]:
    """将知识库证据写入最终助手消息元数据。"""

    enriched = dict(message)

    enriched["answer_status"] = evidence.get("answer_status")
    enriched["refusal_reason"] = evidence.get("reason")
    enriched["citations"] = list(evidence.get("citations") or [])
    enriched["knowledge_evidence"] = dict(evidence)

    normalized_question = str(question or "").strip()
    if normalized_question:
        enriched["knowledge_question"] = normalized_question

    return enriched