from __future__ import annotations

import json
from typing import Any

from yuxi.agents.buildin.chatbot.prompt import KNOWLEDGE_REFUSAL_REPLY, SYSTEM_ERROR_REPLY

QUERY_TOOL_NAME = "query_kb"


def parse_query_kb_output(content: Any) -> dict[str, Any] | None:
    if isinstance(content, dict):
        payload = content
    elif isinstance(content, str):
        try:
            payload = json.loads(content)
        except json.JSONDecodeError:
            return None
    else:
        return None

    if payload.get("schema_version") != 1 or payload.get("status") not in {"ok", "insufficient", "error"}:
        return None
    results = payload.get("results")
    if not isinstance(results, list):
        return None
    return {
        "kb_id": str(payload.get("kb_id") or ""),
        "status": payload["status"],
        "reason": payload.get("reason"),
        "result_count": len(results),
    }


def build_knowledge_evidence(messages: list[Any]) -> tuple[str, dict[str, Any] | None]:
    current_question = ""
    queries: list[dict[str, Any]] = []

    for message in messages:
        data = message.model_dump() if hasattr(message, "model_dump") else dict(message) if isinstance(message, dict) else {}
        message_type = data.get("type") or data.get("role")
        if message_type in {"human", "user"}:
            content = data.get("content")
            current_question = content.strip() if isinstance(content, str) else ""
            queries = []
            continue
        if message_type != "tool" or data.get("name") != QUERY_TOOL_NAME:
            continue
        query = parse_query_kb_output(data.get("content"))
        if query is not None:
            queries.append(query)

    if not queries:
        return current_question, None

    kb_scope = sorted({query["kb_id"] for query in queries if query["kb_id"]})
    return current_question, {"schema_version": 1, "kb_scope": kb_scope, "queries": queries}


def is_final_assistant_message(message: dict[str, Any]) -> bool:
    content = message.get("content")
    if isinstance(content, list):
        content = "\n".join(
            item.get("text", "") for item in content if isinstance(item, dict) and isinstance(item.get("text"), str)
        )
    return (
        isinstance(content, str)
        and bool(content.strip())
        and not (message.get("tool_calls") or [])
        and not message.get("is_error")
        and not message.get("error_type")
    )


def classify_knowledge_disposition(
    content: str,
    evidence: dict[str, Any] | None,
) -> dict[str, Any]:
    normalized = content.strip()
    if normalized == SYSTEM_ERROR_REPLY:
        return {"schema_version": 1, "type": "system_error", "reason": "retrieval_error"}
    if normalized != KNOWLEDGE_REFUSAL_REPLY:
        return {"schema_version": 1, "type": "answered", "reason": None}
    if evidence is None:
        return {"schema_version": 1, "type": "knowledge_refusal", "reason": "no_enabled_knowledge_base"}

    queries = evidence["queries"]
    if queries and all(query["status"] == "error" for query in queries):
        return {"schema_version": 1, "type": "system_error", "reason": "classification_mismatch"}
    if any(query["status"] == "ok" for query in queries):
        reason = "insufficient_evidence"
    elif any(query["reason"] == "empty_content" for query in queries):
        reason = "empty_content"
    else:
        reason = "no_results"
    return {"schema_version": 1, "type": "knowledge_refusal", "reason": reason}


def apply_knowledge_disposition(
    message: dict[str, Any],
    *,
    question: str,
    evidence: dict[str, Any] | None,
) -> dict[str, Any]:
    if not is_final_assistant_message(message):
        return message

    enriched = dict(message)
    content = enriched.get("content")
    if isinstance(content, list):
        content = "\n".join(
            item.get("text", "") for item in content if isinstance(item, dict) and isinstance(item.get("text"), str)
        )
    enriched["knowledge_question"] = question
    if evidence is not None:
        enriched["knowledge_evidence"] = evidence
    enriched["knowledge_disposition"] = classify_knowledge_disposition(str(content or ""), evidence)
    return enriched


__all__ = [
    "apply_knowledge_disposition",
    "build_knowledge_evidence",
    "classify_knowledge_disposition",
    "is_final_assistant_message",
    "parse_query_kb_output",
]
