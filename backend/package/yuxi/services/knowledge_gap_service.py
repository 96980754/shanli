from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from typing import Any

from sqlalchemy import select

from yuxi.repositories.knowledge_gap_repository import KnowledgeGapRepository
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_curated_qa import CuratedQAPair
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
        answers = await load_gap_answers(session, [item.assistant_message_id for item in items])
        return {
            "items": [annotate_gap_has_answer(item.to_dict(), answers) for item in items],
            "total": total,
            "limit": filters["limit"],
            "offset": filters["offset"],
        }

    async def get(self, session, gap_id: int) -> dict[str, Any] | None:
        record = await KnowledgeGapRepository(session).get(gap_id)
        if record is None:
            return None
        answers = await load_gap_answers(session, [record.assistant_message_id])
        return annotate_gap_has_answer(record.to_dict(), answers)

    async def update(self, session, gap_id: int, *, status: str, resolution_note: str | None, operator_uid: str):
        record = await KnowledgeGapRepository(session).update_status(
            gap_id,
            status=self.validate_status(status),
            resolution_note=self.normalize_note(resolution_note),
            operator_uid=operator_uid,
        )
        if record is None:
            return None
        answers = await load_gap_answers(session, [record.assistant_message_id])
        return annotate_gap_has_answer(record.to_dict(), answers)


async def load_gap_answers(session: Any, assistant_message_ids: Iterable[int | None]) -> dict[int, str]:
    """返回已有「知识缺口补答」问答对 {assistant_message_id: answer}。

    has_answer 依据：同一 assistant_message_id 下存在 source_type=knowledge_gap
    的启用问答对（由补答流程生成），并带出已存答案供补答弹窗回显。列表接口批量查询一次，避免 N+1。
    """
    ids = {int(gap_id) for gap_id in assistant_message_ids if gap_id is not None}
    if not ids:
        return {}
    result = await session.execute(
        select(CuratedQAPair.source_message_id, CuratedQAPair.answer).where(
            CuratedQAPair.source_type == "knowledge_gap",
            CuratedQAPair.source_message_id.in_(ids),
            CuratedQAPair.enabled.is_(True),
        )
    )
    return {row[0]: row[1] for row in result.all()}


def annotate_gap_has_answer(gap_dict: dict[str, Any], answers: dict[int, str]) -> dict[str, Any]:
    message_id = int(gap_dict.get("assistant_message_id") or 0)
    gap_dict["has_answer"] = message_id in answers
    gap_dict["answer"] = answers.get(message_id)
    return gap_dict
