"""客服记录候选知识仓储（审核页与 LLM 结构化共用的查询/状态流转）。"""

from __future__ import annotations

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.storage.postgres.models_curated_qa import CuratedQAPair
from yuxi.storage.postgres.models_udesk import CuratedQACandidate
from yuxi.utils.datetime_utils import utc_now_naive

REVIEW_STATUSES = ("pending", "accepted", "rejected")
DEDUP_STATUSES = ("pending", "duplicate", "conflict", "unique")


class CuratedQACandidateRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get(self, candidate_id: int) -> CuratedQACandidate | None:
        result = await self.session.execute(
            select(CuratedQACandidate).where(CuratedQACandidate.id == candidate_id)
        )
        return result.scalar_one_or_none()

    async def list_all(
        self,
        *,
        review_status: str | None = None,
        dedup_status: str | None = None,
        domain: str | None = None,
        keyword: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[int, list[CuratedQACandidate]]:
        """审核页分页查询：按审核态/查重态/业务域/关键词筛选，待审优先（created 倒序）。"""
        conditions = []
        if review_status:
            conditions.append(CuratedQACandidate.review_status == str(review_status))
        if dedup_status:
            conditions.append(CuratedQACandidate.dedup_status == str(dedup_status))
        if domain:
            conditions.append(CuratedQACandidate.domain == str(domain))
        keyword = str(keyword or "").strip()
        if keyword:
            pattern = f"%{keyword}%"
            conditions.append(
                or_(CuratedQACandidate.question.ilike(pattern), CuratedQACandidate.answer.ilike(pattern))
            )
        base = select(CuratedQACandidate)
        if conditions:
            base = base.where(*conditions)
        total = (await self.session.execute(select(func.count()).select_from(base.subquery()))).scalar() or 0
        items = (
            await self.session.execute(
                base.order_by(CuratedQACandidate.created_at.desc(), CuratedQACandidate.id.desc())
                .limit(max(1, min(int(limit), 200)))
                .offset(max(0, int(offset)))
            )
        ).scalars().all()
        return int(total), list(items)

    async def set_review(
        self,
        candidate_ids: list[int],
        *,
        review_status: str,
        reviewed_by: str,
        note: str | None = None,
    ) -> int:
        """审核状态流转（当前只有采纳走这里，落 accepted）；备注写入 review_note。"""
        if not candidate_ids:
            return 0
        result = await self.session.execute(
            update(CuratedQACandidate)
            .where(CuratedQACandidate.id.in_(candidate_ids))
            .values(
                review_status=review_status,
                reviewed_by=str(reviewed_by),
                reviewed_at=utc_now_naive(),
                review_note=note,
                updated_at=utc_now_naive(),
            )
        )
        return result.rowcount or 0

    async def delete(self, candidate_ids: list[int]) -> int:
        """硬删除候选行。

        审核页上的「删除」就是真删除，不留 rejected 状态：候选是待审的**草稿**，
        采纳后内容已迁进 curated_qa_pairs，被否掉的草稿没有任何下游消费者，
        留着只会让「已删除」的记录还能在列表里被翻出来。
        """
        if not candidate_ids:
            return 0
        result = await self.session.execute(
            delete(CuratedQACandidate).where(CuratedQACandidate.id.in_([int(cid) for cid in candidate_ids]))
        )
        return int(result.rowcount or 0)

    async def existing_question_hashes(self, question_hashes: list[str]) -> set[str]:
        """候选查重（C12 精确口径）：哪些问题哈希已存在于启用问答对。"""
        hashes = [h for h in question_hashes if h]
        if not hashes:
            return set()
        result = await self.session.execute(
            select(CuratedQAPair.question_hash).where(CuratedQAPair.question_hash.in_(hashes))
        )
        return {row[0] for row in result.all()}
