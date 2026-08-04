from __future__ import annotations

from typing import Any

from sqlalchemy import and_, case, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.storage.postgres.models_business import KnowledgeGap
from yuxi.utils.datetime_utils import utc_now_naive


class KnowledgeGapRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def record_occurrence(self, data: dict[str, Any]) -> KnowledgeGap:
        existing_message = await self.session.execute(
            select(KnowledgeGap).where(KnowledgeGap.assistant_message_id == data["assistant_message_id"])
        )
        record = existing_message.scalar_one_or_none()
        if record is not None:
            return record

        now = utc_now_naive()
        stmt = insert(KnowledgeGap).values(
            **data,
            occurrence_count=1,
            first_seen_at=now,
            last_seen_at=now,
            created_at=now,
            updated_at=now,
        )
        excluded = stmt.excluded
        stmt = stmt.on_conflict_do_update(
            constraint="uq_knowledge_gaps_scope",
            set_={
                "question": excluded.question,
                "reason": excluded.reason,
                "occurrence_count": KnowledgeGap.occurrence_count + 1,
                "uid": excluded.uid,
                "conversation_thread_id": excluded.conversation_thread_id,
                "assistant_message_id": excluded.assistant_message_id,
                "last_seen_at": now,
                "updated_at": now,
                "status": case((KnowledgeGap.status == "resolved", "new"), else_=KnowledgeGap.status),
                "resolution_note": case(
                    (KnowledgeGap.status == "resolved", None), else_=KnowledgeGap.resolution_note
                ),
                "resolved_at": case((KnowledgeGap.status == "resolved", None), else_=KnowledgeGap.resolved_at),
                "resolved_by": case((KnowledgeGap.status == "resolved", None), else_=KnowledgeGap.resolved_by),
            },
            where=or_(
                KnowledgeGap.assistant_message_id.is_(None),
                KnowledgeGap.assistant_message_id != excluded.assistant_message_id,
            ),
        ).returning(KnowledgeGap)
        result = await self.session.execute(stmt.execution_options(populate_existing=True))
        record = result.scalar_one_or_none()
        if record is None:
            result = await self.session.execute(
                select(KnowledgeGap).where(KnowledgeGap.assistant_message_id == data["assistant_message_id"])
            )
            record = result.scalar_one()
        await self.session.flush()
        return record

    async def get(self, gap_id: int) -> KnowledgeGap | None:
        result = await self.session.execute(select(KnowledgeGap).where(KnowledgeGap.id == gap_id))
        return result.scalar_one_or_none()

    async def list(
        self,
        *,
        status: str | None = None,
        agent_slug: str | None = None,
        reason: str | None = None,
        query: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[KnowledgeGap], int]:
        conditions = []
        if status:
            conditions.append(KnowledgeGap.status == status)
        if agent_slug:
            conditions.append(KnowledgeGap.agent_slug == agent_slug)
        if reason:
            conditions.append(KnowledgeGap.reason == reason)
        if query:
            conditions.append(KnowledgeGap.question.ilike(f"%{query}%"))
        where = and_(*conditions) if conditions else True
        total = int((await self.session.execute(select(func.count()).select_from(KnowledgeGap).where(where))).scalar() or 0)
        result = await self.session.execute(
            select(KnowledgeGap)
            .where(where)
            .order_by(KnowledgeGap.last_seen_at.desc(), KnowledgeGap.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all()), total

    async def update_status(
        self,
        gap_id: int,
        *,
        status: str,
        resolution_note: str | None,
        operator_uid: str,
    ) -> KnowledgeGap | None:
        now = utc_now_naive()
        values: dict[str, Any] = {
            "status": status,
            "resolution_note": resolution_note,
            "updated_at": now,
            "resolved_at": now if status == "resolved" else None,
            "resolved_by": operator_uid if status == "resolved" else None,
        }
        result = await self.session.execute(
            update(KnowledgeGap).where(KnowledgeGap.id == gap_id).values(**values).returning(KnowledgeGap)
        )
        record = result.scalar_one_or_none()
        await self.session.flush()
        return record
