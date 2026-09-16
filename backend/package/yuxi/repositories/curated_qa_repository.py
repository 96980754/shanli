"""人工确认问答对仓储。"""

from __future__ import annotations

import hashlib
import re

from sqlalchemy import delete, func, or_, select, update
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

    async def get_enabled(self, qa_id: int) -> CuratedQAPair | None:
        result = await self.session.execute(
            select(CuratedQAPair).where(
                CuratedQAPair.id == qa_id,
                CuratedQAPair.enabled.is_(True),
            )
        )
        return result.scalar_one_or_none()

    async def list_enabled_for_agent(self, agent_slug: str) -> list[CuratedQAPair]:
        """某 agent 的全部启用问答对，供语义召回扫描（含懒回填前的空向量）。"""
        result = await self.session.execute(
            select(CuratedQAPair)
            .where(
                CuratedQAPair.agent_slug == str(agent_slug),
                CuratedQAPair.enabled.is_(True),
            )
            .order_by(CuratedQAPair.id.asc())
        )
        return list(result.scalars().all())

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

    async def upsert_from_candidate(
        self,
        *,
        agent_slug: str,
        question: str,
        answer: str,
        operator_uid: str,
        source_conversation_id: str,
    ) -> CuratedQAPair:
        """客服记录候选采纳入库（C6：一律 enabled=False 落库，绝不自动启用）。

        已有同键问答对时仅刷新答案与来源，enabled 保持原值（启用由管理员在
        问答对管理页操作）；幂等键复用 uq_curated_qa_agent_question（C12）。
        """
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
            existing.source_type = "udesk"
            existing.source_conversation_id = source_conversation_id
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
            enabled=False,
            source_type="udesk",
            source_conversation_id=source_conversation_id,
            created_by=str(operator_uid),
            updated_by=str(operator_uid),
            created_at=now,
            updated_at=now,
        )
        self.session.add(item)
        await self.session.flush()
        return item

    async def list_all(
        self,
        *,
        agent_slug: str | None = None,
        source_type: str | None = None,
        enabled: bool | None = None,
        keyword: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[int, list[CuratedQAPair]]:
        """管理端分页查询：按智能体/来源/启用态/关键词筛选，返回 (总数, 当前页)。"""
        conditions = []
        if agent_slug:
            conditions.append(CuratedQAPair.agent_slug == str(agent_slug))
        if source_type:
            conditions.append(CuratedQAPair.source_type == str(source_type))
        if enabled is not None:
            conditions.append(CuratedQAPair.enabled.is_(bool(enabled)))
        if keyword:
            pattern = f"%{keyword.strip()}%"
            conditions.append(or_(CuratedQAPair.question.ilike(pattern), CuratedQAPair.answer.ilike(pattern)))

        total = (await self.session.execute(select(func.count(CuratedQAPair.id)).where(*conditions))).scalar() or 0
        stmt = (
            select(CuratedQAPair)
            .where(*conditions)
            .order_by(CuratedQAPair.updated_at.desc(), CuratedQAPair.id.desc())
            .limit(max(1, min(int(limit), 200)))
            .offset(max(0, int(offset)))
        )
        items = list((await self.session.execute(stmt)).scalars().all())
        return total, items

    async def update_content(
        self, qa_id: int, *, question: str, answer: str, operator_uid: str
    ) -> CuratedQAPair | None:
        """问答对管理页修改内容；问题变更时重算精确匹配键并使语义向量失效（懒回填）。"""
        normalized = normalize_qa_question(question)
        normalized_answer = str(answer or "").strip()
        if not normalized:
            raise ValueError("问题不能为空")
        if not normalized_answer:
            raise ValueError("答案不能为空")
        item = await self.get(qa_id)
        if item is None:
            return None
        if normalized != item.normalized_question:
            # 同 agent 下同题会撞 uq_curated_qa_agent_question，提前给出可读错误
            conflict = await self.get_exact(agent_slug=item.agent_slug, question=question, enabled_only=False)
            if conflict is not None and conflict.id != qa_id:
                raise ValueError("该智能体下已存在相同问题")
            item.question = str(question).strip()
            item.normalized_question = normalized
            item.question_hash = hash_qa_question(normalized)
            item.question_embedding = None
        item.answer = normalized_answer
        item.updated_by = str(operator_uid)
        item.updated_at = utc_now_naive()
        await self.session.flush()
        return item

    async def set_enabled(self, qa_ids: list[int], *, enabled: bool, operator_uid: str) -> int:
        """批量启用/停用，返回实际更新的行数。"""
        if not qa_ids:
            return 0
        result = await self.session.execute(
            update(CuratedQAPair)
            .where(CuratedQAPair.id.in_([int(qa_id) for qa_id in qa_ids]))
            .values(enabled=bool(enabled), updated_by=str(operator_uid), updated_at=utc_now_naive())
        )
        return int(result.rowcount or 0)

    async def delete(self, qa_ids: list[int]) -> int:
        """批量删除，返回实际删除的行数。"""
        if not qa_ids:
            return 0
        result = await self.session.execute(
            delete(CuratedQAPair).where(CuratedQAPair.id.in_([int(qa_id) for qa_id in qa_ids]))
        )
        return int(result.rowcount or 0)

    async def mark_hit(self, item: CuratedQAPair) -> None:
        item.hit_count = int(item.hit_count or 0) + 1
        item.last_hit_at = utc_now_naive()
        item.updated_at = utc_now_naive()
        await self.session.flush()
