from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from yuxi.repositories.knowledge_gap_repository import KnowledgeGapRepository
from yuxi.storage.postgres.manager import pg_manager
from yuxi.utils.logging_config import logger

GAP_STATUSES = {"new", "processing", "resolved", "ignored"}
GAP_REASONS = {"no_enabled_knowledge_base", "no_results", "empty_content", "insufficient_evidence"}


def normalize_question(question: str) -> str:
    return re.sub(r"\s+", " ", question).strip().casefold()


def normalize_kb_scope(kb_scope: list[str]) -> list[str]:
    return sorted({str(kb_id).strip() for kb_id in kb_scope if str(kb_id).strip()})


def build_gap_identity(question: str, agent_slug: str, kb_scope: list[str]) -> dict[str, Any]:
    normalized = normalize_question(question)
    scope = normalize_kb_scope(kb_scope)
    if not normalized or not agent_slug:
        raise ValueError("知识缺口缺少问题或 Agent")
    return {
        "normalized_question": normalized,
        "question_hash": hashlib.sha256(normalized.encode()).hexdigest(),
        "agent_slug": agent_slug,
        "kb_scope": scope,
        "kb_scope_hash": hashlib.sha256(json.dumps(scope, ensure_ascii=False).encode()).hexdigest(),
    }


async def record_knowledge_gap(
    *,
    question: str,
    agent_slug: str,
    kb_scope: list[str],
    reason: str,
    uid: str | None,
    conversation_thread_id: str | None,
    assistant_message_id: int,
) -> None:
    if reason not in GAP_REASONS:
        return
    identity = build_gap_identity(question, agent_slug, kb_scope)
    try:
        async with pg_manager.get_async_session_context() as session:
            await KnowledgeGapRepository(session).record_occurrence(
                {
                    **identity,
                    "question": question.strip(),
                    "reason": reason,
                    "uid": uid,
                    "conversation_thread_id": conversation_thread_id,
                    "assistant_message_id": assistant_message_id,
                }
            )
    except Exception as exc:  # noqa: BLE001
        logger.exception("记录知识缺口失败 message_id={}: {}", assistant_message_id, exc)


class KnowledgeGapAdminService:
    @staticmethod
    def validate_status(status: str) -> str:
        if status not in GAP_STATUSES:
            raise ValueError("无效的知识缺口状态")
        return status

    @staticmethod
    def normalize_note(note: str | None) -> str | None:
        normalized = str(note or "").strip() or None
        if normalized and len(normalized) > 2000:
            raise ValueError("处理备注不能超过 2000 个字符")
        return normalized

    async def list(self, session, **filters) -> dict[str, Any]:
        if filters.get("status"):
            self.validate_status(filters["status"])
        items, total = await KnowledgeGapRepository(session).list(**filters)
        return {
            "items": [item.to_dict() for item in items],
            "total": total,
            "limit": filters["limit"],
            "offset": filters["offset"],
        }

    async def get(self, session, gap_id: int) -> dict[str, Any] | None:
        record = await KnowledgeGapRepository(session).get(gap_id)
        return record.to_dict() if record else None

    async def update(self, session, gap_id: int, *, status: str, resolution_note: str | None, operator_uid: str):
        record = await KnowledgeGapRepository(session).update_status(
            gap_id,
            status=self.validate_status(status),
            resolution_note=self.normalize_note(resolution_note),
            operator_uid=operator_uid,
        )
        return record.to_dict() if record else None
