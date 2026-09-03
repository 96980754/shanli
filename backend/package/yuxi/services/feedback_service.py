import asyncio

from fastapi import HTTPException
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from yuxi.services.langfuse_service import submit_user_feedback_score
from yuxi.storage.postgres.models_business import Conversation, Message, MessageFeedback
from yuxi.utils.logging_config import logger


FEEDBACK_REASON_OPTIONS = {
    "answer_incorrect": "答案有误",
    "outdated": "信息过时",
    "irrelevant": "答非所问",
    "other": "其他",
}
# 历史英文界面点踩时把本地化标签存库形成的别名：读时把英文行归入正确 code，
# 保证中英文混存不影响按 code 聚合的统计。新提交一律存稳定 code，不再依赖该表。
FEEDBACK_REASON_EN_LABELS = {
    "answer_incorrect": "Answer is incorrect",
    "outdated": "Information is outdated",
    "irrelevant": "Answer is irrelevant",
    "other": "Other",
}
FEEDBACK_REASON_SEPARATOR = "\n"


def parse_feedback_reason(reason: str | None) -> dict:
    """解析结构化点踩原因，并兼容历史自由文本反馈。

    首行匹配顺序：稳定 code → 中文标签 → 英文标签（历史行）；其余视为历史自由文本。
    detail 以 \n 分隔，随首行一并解析。
    """
    raw_reason = str(reason or "").strip()
    if not raw_reason:
        return {
            "reason_code": None,
            "reason_label": None,
            "reason_detail": None,
        }

    first_line = raw_reason.split(FEEDBACK_REASON_SEPARATOR, 1)[0].strip()
    detail = None
    if FEEDBACK_REASON_SEPARATOR in raw_reason:
        detail = raw_reason.split(FEEDBACK_REASON_SEPARATOR, 1)[1].strip() or None

    label_to_code: dict[str, str] = {}
    for reason_code, zh_label in FEEDBACK_REASON_OPTIONS.items():
        label_to_code[reason_code] = reason_code
        label_to_code[zh_label] = reason_code
    for reason_code, en_label in FEEDBACK_REASON_EN_LABELS.items():
        label_to_code[en_label] = reason_code

    reason_code = label_to_code.get(first_line)
    if reason_code is not None:
        return {
            "reason_code": reason_code,
            "reason_label": FEEDBACK_REASON_OPTIONS[reason_code],
            "reason_detail": detail,
        }

    return {
        "reason_code": None,
        "reason_label": "历史反馈",
        "reason_detail": raw_reason,
    }


# =============================================================================
# 满意度统计（未反馈默认计满意）
# =============================================================================

# 可评价基数的判定：role=assistant 且其紧邻下一条消息（同会话、id 更大）不再是
# assistant——即该条消息是其所在轮次的最后一条 AI 终答（前端赞/踩按钮只出现在这类
# 收尾消息上）。含拒答/转人工等收尾消息，一并纳入分母。
_EVALUABLE_ANSWERS_WHERE = """
    m.role = 'assistant'
    AND NOT EXISTS (
        SELECT 1 FROM messages n
        WHERE n.conversation_id = m.conversation_id
          AND n.id = (
              SELECT MIN(id) FROM messages
              WHERE conversation_id = m.conversation_id AND id > m.id
          )
          AND n.role = 'assistant'
    )
"""


async def count_evaluable_answers(*, db: AsyncSession, agent_id: str | None = None) -> int:
    """统计可评价基数：会话内收尾 AI 终答消息条数。"""
    agent_scope = ""
    params: dict = {}
    if agent_id:
        agent_scope = (
            " AND EXISTS (SELECT 1 FROM conversations c "
            "             WHERE c.id = m.conversation_id AND c.agent_id = :agent_id)"
        )
        params["agent_id"] = agent_id
    sql = text(f"SELECT COUNT(*) FROM messages m WHERE {_EVALUABLE_ANSWERS_WHERE}{agent_scope}")
    result = await db.execute(sql, params)
    return result.scalar() or 0


def build_satisfaction_stats(*, evaluable_count: int, like_count: int, dislike_count: int) -> dict:
    """满意度口径：未反馈默认计满意。

    silent_count = 可评价基数 − 显式好评 − 显式差评（未反馈的收尾回答，默认满意）；
    satisfaction_rate = (好评 + 未反馈) / 可评价基数。
    """
    silent_count = max(0, evaluable_count - like_count - dislike_count)
    if evaluable_count > 0:
        satisfaction_rate = round((like_count + silent_count) / evaluable_count * 100, 2)
        participation_rate = round((like_count + dislike_count) / evaluable_count * 100, 2)
    else:
        satisfaction_rate = 100.0
        participation_rate = 0.0
    return {
        "evaluable_count": evaluable_count,
        "like_count": like_count,
        "dislike_count": dislike_count,
        "silent_count": silent_count,
        "satisfaction_rate": satisfaction_rate,
        "participation_rate": participation_rate,
    }


async def submit_message_feedback_view(
    *,
    message_id: int,
    rating: str,
    reason: str | None,
    db: AsyncSession,
    current_uid: str,
) -> dict:
    if rating not in ["like", "dislike"]:
        raise HTTPException(status_code=422, detail="Rating must be 'like' or 'dislike'")

    try:
        message_result = await db.execute(select(Message).filter_by(id=message_id))
        message = message_result.scalar_one_or_none()
        if not message:
            raise HTTPException(status_code=404, detail="Message not found")

        conversation_result = await db.execute(select(Conversation).filter_by(id=message.conversation_id))
        conversation = conversation_result.scalar_one_or_none()
        if not conversation or conversation.uid != str(current_uid):
            raise HTTPException(status_code=403, detail="Access denied")

        existing_feedback_result = await db.execute(
            select(MessageFeedback).filter_by(message_id=message_id, uid=str(current_uid))
        )
        existing_feedback = existing_feedback_result.scalar_one_or_none()
        if existing_feedback:
            raise HTTPException(status_code=409, detail="Feedback already submitted for this message")

        new_feedback = MessageFeedback(
            message_id=message_id,
            uid=str(current_uid),
            rating=rating,
            reason=reason,
        )

        db.add(new_feedback)
        await db.commit()
        await db.refresh(new_feedback)

        trace_id = (message.extra_metadata or {}).get("langfuse_trace_id")
        if trace_id:
            # Langfuse comment 用可读文案：code 存库的提交解析回规范中文标签 + detail，
            # 避免上传裸 code；历史自由文本原样透传。
            comment_reason = reason
            parsed = parse_feedback_reason(reason)
            if parsed.get("reason_code") and parsed.get("reason_label"):
                label = parsed["reason_label"]
                detail = parsed.get("reason_detail")
                comment_reason = f"{label}{FEEDBACK_REASON_SEPARATOR}{detail}" if detail else label
            # submit_user_feedback_score 内部会同步调用 client.flush() 发起阻塞网络请求，
            # 放到线程池执行避免阻塞事件循环；本地反馈已落库，上传失败不影响主流程。
            await asyncio.to_thread(
                submit_user_feedback_score,
                trace_id=trace_id,
                feedback_id=new_feedback.id,
                message_id=new_feedback.message_id,
                conversation_id=message.conversation_id,
                uid=str(current_uid),
                rating=rating,
                reason=comment_reason,
            )

        logger.info(f"User {current_uid} submitted {rating} feedback for message {message_id}")

        return {
            "id": new_feedback.id,
            "message_id": new_feedback.message_id,
            "rating": new_feedback.rating,
            "reason": new_feedback.reason,
            "created_at": new_feedback.created_at.isoformat(),
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Error submitting message feedback: {e}")
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to submit feedback: {str(e)}")


async def get_message_feedback_view(
    *,
    message_id: int,
    db: AsyncSession,
    current_uid: str,
) -> dict:
    try:
        feedback_result = await db.execute(
            select(MessageFeedback).filter_by(message_id=message_id, uid=str(current_uid))
        )
        feedback = feedback_result.scalar_one_or_none()

        if not feedback:
            return {"has_feedback": False, "feedback": None}

        return {
            "has_feedback": True,
            "feedback": {
                "id": feedback.id,
                "rating": feedback.rating,
                "reason": feedback.reason,
                "created_at": feedback.created_at.isoformat(),
            },
        }

    except Exception as e:
        logger.exception(f"Error getting message feedback: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to get feedback: {str(e)}")
