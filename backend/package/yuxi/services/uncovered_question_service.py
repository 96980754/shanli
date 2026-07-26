"""未覆盖问题记录与管理服务。"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.repositories.uncovered_question_repository import UncoveredQuestionRepository
from yuxi.storage.postgres.models_business import Conversation, Message, UncoveredQuestion

_RECORDABLE_REASONS = {
    "no_result",
    "empty_content",
    "low_relevance",
    "missing_answer_evidence",
    "conflicting_evidence",
}

UNCOVERED_QUESTION_STATUSES = {
    "new",
    "processing",
    "resolved",
    "ignored",
}

_MAX_RESOLUTION_NOTE_LENGTH = 2000


def normalize_uncovered_question(question: str) -> str:
    """标准化问题文本，用于稳定聚合。"""

    return " ".join(str(question or "").split()).strip().lower()


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _normalize_kb_ids(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []

    result: list[str] = []
    for item in value:
        kb_id = str(item or "").strip()
        if kb_id and kb_id not in result:
            result.append(kb_id)
    return sorted(result)


def _optional_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_optional_text(value: str | None) -> str | None:
    normalized = str(value or "").strip()
    return normalized or None


def _validate_status(status: str | None, *, required: bool = False) -> str | None:
    normalized = _normalize_optional_text(status)
    if normalized is None:
        if required:
            raise ValueError("status is required")
        return None
    if normalized not in UNCOVERED_QUESTION_STATUSES:
        allowed = ", ".join(sorted(UNCOVERED_QUESTION_STATUSES))
        raise ValueError(f"invalid status: {normalized}; allowed: {allowed}")
    return normalized


def _normalize_resolution_note(value: str | None) -> str | None:
    normalized = _normalize_optional_text(value)
    if normalized and len(normalized) > _MAX_RESOLUTION_NOTE_LENGTH:
        raise ValueError(f"resolution_note must not exceed {_MAX_RESOLUTION_NOTE_LENGTH} characters")
    return normalized


def build_uncovered_question_data(
    *,
    conversation: Conversation,
    assistant_message: Message,
) -> dict[str, Any] | None:
    """从已持久化的拒答消息构造知识缺口数据。"""

    metadata = assistant_message.extra_metadata or {}
    if metadata.get("answer_status") != "refused":
        return None

    reason = str(metadata.get("refusal_reason") or "").strip()
    if reason not in _RECORDABLE_REASONS:
        return None

    question = str(metadata.get("knowledge_question") or "").strip()
    normalized_question = normalize_uncovered_question(question)
    if not normalized_question:
        return None

    agent_id = str(conversation.agent_id or "").strip()
    uid = str(conversation.uid or "").strip()
    thread_id = str(conversation.thread_id or "").strip()
    if not agent_id or not uid or not thread_id:
        return None

    evidence = metadata.get("knowledge_evidence")
    evidence = evidence if isinstance(evidence, dict) else {}
    kb_ids = _normalize_kb_ids(evidence.get("kb_ids"))
    kb_scope_value = json.dumps(kb_ids, ensure_ascii=False, separators=(",", ":"))

    return {
        "question": question,
        "normalized_question": normalized_question,
        "question_hash": _sha256(normalized_question),
        "kb_scope_hash": _sha256(kb_scope_value),
        "uid": uid,
        "thread_id": thread_id,
        "assistant_message_id": assistant_message.id,
        "agent_id": agent_id,
        "kb_ids": kb_ids,
        "reason": reason,
        "top_score": _optional_float(evidence.get("top_score")),
        "score_type": str(evidence.get("score_type") or "").strip() or None,
    }


async def record_uncovered_question(
    *,
    db: AsyncSession,
    conversation: Conversation,
    assistant_message: Message,
) -> UncoveredQuestion | None:
    """记录一次知识不足拒答；非知识缺口场景直接忽略。"""

    data = build_uncovered_question_data(
        conversation=conversation,
        assistant_message=assistant_message,
    )
    if data is None:
        return None

    return await UncoveredQuestionRepository(db).upsert_occurrence(data)


async def list_uncovered_questions_view(
    *,
    db: AsyncSession,
    status: str | None = None,
    agent_id: str | None = None,
    reason: str | None = None,
    query_text: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """分页查询未覆盖问题。"""

    normalized_status = _validate_status(status)
    records, total = await UncoveredQuestionRepository(db).list_items(
        status=normalized_status,
        agent_id=_normalize_optional_text(agent_id),
        reason=_normalize_optional_text(reason),
        query_text=_normalize_optional_text(query_text),
        limit=limit,
        offset=offset,
    )
    return {
        "items": [record.to_dict() for record in records],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


async def get_uncovered_question_view(
    *,
    db: AsyncSession,
    question_id: int,
) -> dict[str, Any]:
    """读取一条未覆盖问题详情。"""

    record = await UncoveredQuestionRepository(db).get_by_id(question_id)
    if record is None:
        raise LookupError("uncovered question not found")
    return record.to_dict()


async def update_uncovered_question_status_view(
    *,
    db: AsyncSession,
    question_id: int,
    status: str,
    resolution_note: str | None = None,
) -> dict[str, Any]:
    """更新未覆盖问题处理状态。"""

    normalized_status = _validate_status(status, required=True)
    normalized_note = _normalize_resolution_note(resolution_note)
    record = await UncoveredQuestionRepository(db).update_status(
        question_id=question_id,
        status=normalized_status,
        resolution_note=normalized_note,
    )
    if record is None:
        raise LookupError("uncovered question not found")
    return record.to_dict()
