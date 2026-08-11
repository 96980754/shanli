"""人工确认问答对仓储。"""

from __future__ import annotations

import hashlib
import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.storage.postgres.models_curated_qa import CuratedQAPair
from yuxi.utils.datetime_utils import utc_now_naive


def normalize_qa_question(question: str) -> str:
    """统一精确匹配口径：忽略首尾/连续空白和英文大小写。"""
    return re.sub(r"\s+", " ", str(question or "")).strip().casefold()


def hash_qa_question(normalized_question: str) -> str:
    return hashlib.sha256(normalized_question.encode("utf-8")).hexdigest()


class CuratedQARepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_exact(self, *, agent_slug: str, question: str, enabled_only: bool = True) -> CuratedQAPair | None:
        normalized = normalize_qa_question(question)
        if not normalized:
            return None
        query = select(CuratedQAPair).where(
            CuratedQAPair.agent_slug == str(agent_slug),
            CuratedQAPair.question_hash == hash_qa_question(normalized),
            CuratedQAPair.normalized_question == normalized,
        )
        if enabled_only:
            query = query.where(CuratedQAPair.enabled.is_(True))
        result = await self.session.execute(query)
        return result.scalar_one_or_none()

    async def get(self, qa_id: int) -> CuratedQAPair | None:
        result = await self.session.execute(select(CuratedQAPair).where(CuratedQAPair.id == qa_id))
        return result.scalar_one_or_none()

    async def upsert(
        self,
        *,
        agent_slug: str,
        question: str,
        answer: str,
        operator_uid: str,
        source_type: str = "feedback",
        source_feedback_id: int | None = None,
        source_message_id: int | None = None,
    ) -> CuratedQAPair:
        normalized = normalize_qa_question(question)
        normalized_answer = str(answer or "").strip()
        if not normalized:
            raise ValueError("问题不能为空")
        if not normalized_answer:
            raise ValueError("答案不能为空")

        existing = await self.get_exact(agent_slug=agent_slug, question=question, enabled_only=False)
        now = utc_now_naive()
        if existing:
            existing.question = str(question).strip()
            existing.normalized_question = normalized
            existing.question_hash = hash_qa_question(normalized)
            existing.answer = normalized_answer
            existing.enabled = True
            existing.source_type = source_type
            existing.source_feedback_id = source_feedback_id
            existing.source_message_id = source_message_id
            existing.updated_by = str(operator_uid)
            existing.updated_at = now
            await self.session.flush()
            return existing

        item = CuratedQAPair(
            agent_slug=str(agent_slug),
            question=str(question).strip(),
            normalized_question=normalized,
            question_hash=hash_qa_question(normalized),
            answer=normalized_answer,
            enabled=True,
            source_type=source_type,
            source_feedback_id=source_feedback_id,
            source_message_id=source_message_id,
            created_by=str(operator_uid),
            updated_by=str(operator_uid),
            created_at=now,
            updated_at=now,
        )
        self.session.add(item)
        await self.session.flush()
        return item

    async def mark_hit(self, item: CuratedQAPair) -> None:
        item.hit_count = int(item.hit_count or 0) + 1
        item.last_hit_at = utc_now_naive()
        item.updated_at = utc_now_naive()
        await self.session.flush()
