"""用户反馈统计接口。"""

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from server.utils.auth_middleware import get_db, get_superadmin_user
from yuxi.services.feedback_service import (
    FEEDBACK_REASON_OPTIONS,
    build_satisfaction_stats,
    count_evaluable_answers,
    parse_feedback_reason,
)
from yuxi.storage.postgres.models_business import Conversation, Message, MessageFeedback, User


feedback_dashboard = APIRouter(prefix="/dashboard", tags=["Dashboard"])


class FeedbackReasonStat(BaseModel):
    code: str
    label: str
    count: int


class FeedbackSummaryResponse(BaseModel):
    total_feedbacks: int
    like_count: int
    dislike_count: int
    evaluable_count: int
    silent_count: int
    satisfaction_rate: float
    participation_rate: float
    reason_stats: list[FeedbackReasonStat]
    legacy_unclassified_count: int


@feedback_dashboard.get("/feedback-summary", response_model=FeedbackSummaryResponse)
async def get_feedback_summary(
    agent_id: str | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_superadmin_user),
):
    """获取反馈总量、点赞/点踩及点踩原因分布（超级管理员权限）。"""
    del current_user

    query = select(MessageFeedback.rating, MessageFeedback.reason)
    if agent_id:
        query = (
            query.join(Message, MessageFeedback.message_id == Message.id)
            .join(Conversation, Message.conversation_id == Conversation.id)
            .filter(Conversation.agent_id == agent_id)
        )

    result = await db.execute(query)
    rows = result.all()

    total_feedbacks = len(rows)
    like_count = 0
    dislike_count = 0
    reason_counts = {code: 0 for code in FEEDBACK_REASON_OPTIONS}
    legacy_unclassified_count = 0

    for rating, reason in rows:
        if rating == "like":
            like_count += 1
            continue
        if rating != "dislike":
            continue

        dislike_count += 1
        parsed = parse_feedback_reason(reason)
        reason_code = parsed.get("reason_code")
        if reason_code in reason_counts:
            reason_counts[reason_code] += 1
        elif reason:
            legacy_unclassified_count += 1

    # 满意度口径：未反馈默认计满意（satisfaction_rate = (好评 + 未反馈) / 可评价基数）
    evaluable_count = await count_evaluable_answers(db=db, agent_id=agent_id)
    stats = build_satisfaction_stats(
        evaluable_count=evaluable_count,
        like_count=like_count,
        dislike_count=dislike_count,
    )
    reason_stats = [
        FeedbackReasonStat(code=code, label=label, count=reason_counts[code])
        for code, label in FEEDBACK_REASON_OPTIONS.items()
    ]

    return FeedbackSummaryResponse(
        total_feedbacks=total_feedbacks,
        like_count=stats["like_count"],
        dislike_count=stats["dislike_count"],
        evaluable_count=stats["evaluable_count"],
        silent_count=stats["silent_count"],
        satisfaction_rate=stats["satisfaction_rate"],
        participation_rate=stats["participation_rate"],
        reason_stats=reason_stats,
        legacy_unclassified_count=legacy_unclassified_count,
    )
