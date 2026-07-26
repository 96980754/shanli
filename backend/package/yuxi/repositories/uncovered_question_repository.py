"""未覆盖问题数据访问层。"""

from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.storage.postgres.models_business import UncoveredQuestion
from yuxi.utils.datetime_utils import utc_now_naive


class UncoveredQuestionRepository:
    """未覆盖问题聚合记录 Repository。"""

    def __init__(self, db_session: AsyncSession):
        self.db = db_session

    async def upsert_occurrence(self, data: dict[str, Any]) -> UncoveredQuestion:
        """按问题、智能体和知识库范围聚合一次拒答。"""

        now = utc_now_naive()
        values = {
            **data,
            "status": "new",
            "occurrence_count": 1,
            "first_seen_at": now,
            "last_seen_at": now,
            "resolved_at": None,
            "resolution_note": None,
        }

        insert_stmt = pg_insert(UncoveredQuestion).values(**values)
        stmt = (
            insert_stmt.on_conflict_do_update(
                constraint="uq_uncovered_questions_scope",
                set_={
                    "question": insert_stmt.excluded.question,
                    "normalized_question": insert_stmt.excluded.normalized_question,
                    "uid": insert_stmt.excluded.uid,
                    "thread_id": insert_stmt.excluded.thread_id,
                    "assistant_message_id": insert_stmt.excluded.assistant_message_id,
                    "kb_ids": insert_stmt.excluded.kb_ids,
                    "reason": insert_stmt.excluded.reason,
                    "top_score": insert_stmt.excluded.top_score,
                    "score_type": insert_stmt.excluded.score_type,
                    "status": "new",
                    "occurrence_count": UncoveredQuestion.occurrence_count + 1,
                    "last_seen_at": now,
                    "resolved_at": None,
                    "resolution_note": None,
                },
            )
            .returning(UncoveredQuestion)
        )

        result = await self.db.execute(stmt)
        record = result.scalar_one()
        await self.db.commit()
        return record

    async def get_by_id(self, question_id: int) -> UncoveredQuestion | None:
        """按主键读取一条未覆盖问题。"""

        result = await self.db.execute(
            select(UncoveredQuestion).where(UncoveredQuestion.id == question_id)
        )
        return result.scalar_one_or_none()

    async def list_items(
        self,
        *,
        status: str | None = None,
        agent_id: str | None = None,
        reason: str | None = None,
        query_text: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[UncoveredQuestion], int]:
        """分页列出未覆盖问题，并返回过滤后的总数。"""

        conditions = []
        if status:
            conditions.append(UncoveredQuestion.status == status)
        if agent_id:
            conditions.append(UncoveredQuestion.agent_id == agent_id)
        if reason:
            conditions.append(UncoveredQuestion.reason == reason)
        if query_text:
            pattern = f"%{query_text}%"
            conditions.append(
                or_(
                    UncoveredQuestion.question.ilike(pattern),
                    UncoveredQuestion.normalized_question.ilike(pattern),
                    UncoveredQuestion.uid.ilike(pattern),
                    UncoveredQuestion.thread_id.ilike(pattern),
                )
            )

        count_stmt = select(func.count(UncoveredQuestion.id))
        list_stmt = select(UncoveredQuestion)
        if conditions:
            count_stmt = count_stmt.where(*conditions)
            list_stmt = list_stmt.where(*conditions)

        total_result = await self.db.execute(count_stmt)
        total = int(total_result.scalar() or 0)

        result = await self.db.execute(
            list_stmt.order_by(
                UncoveredQuestion.last_seen_at.desc(),
                UncoveredQuestion.id.desc(),
            )
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all()), total

    async def update_status(
        self,
        *,
        question_id: int,
        status: str,
        resolution_note: str | None,
    ) -> UncoveredQuestion | None:
        """更新处理状态；已解决和忽略状态会写入处理时间。"""

        record = await self.get_by_id(question_id)
        if record is None:
            return None

        record.status = status
        record.resolution_note = resolution_note
        record.resolved_at = utc_now_naive() if status in {"resolved", "ignored"} else None

        await self.db.commit()
        await self.db.refresh(record)
        return record
