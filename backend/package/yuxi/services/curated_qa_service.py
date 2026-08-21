"""人工确认问答调优业务逻辑。"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.repositories.curated_qa_repository import CuratedQARepository
from yuxi.storage.postgres.models_business import Conversation, Message, MessageFeedback


class CuratedQAService:
    @staticmethod
    def normalize_answer(answer: str) -> str:
        normalized = str(answer or "").strip()
        if not normalized:
            raise ValueError("答案不能为空")
        if len(normalized) > 20_000:
            raise ValueError("答案不能超过 20000 个字符")
        return normalized

    async def get_feedback_context(self, session: AsyncSession, feedback_id: int) -> dict | None:
        result = await session.execute(
            select(MessageFeedback, Message, Conversation)
            .join(Message, MessageFeedback.message_id == Message.id)
            .join(Conversation, Message.conversation_id == Conversation.id)
            .where(MessageFeedback.id == feedback_id)
        )
        row = result.one_or_none()
        if row is None:
            return None

        feedback, assistant_message, conversation = row
        question_result = await session.execute(
            select(Message)
            .where(
                Message.conversation_id == assistant_message.conversation_id,
                Message.role == "user",
                Message.id < assistant_message.id,
            )
            .order_by(Message.id.desc())
            .limit(1)
        )
        question_message = question_result.scalar_one_or_none()
        if question_message is None:
            raise ValueError("未找到该回答对应的用户问题")

        qa_pair = await CuratedQARepository(session).get_exact(
            agent_slug=conversation.agent_id,
            question=question_message.content,
            enabled_only=False,
        )
        return {
            "feedback_id": feedback.id,
            "rating": feedback.rating,
            "reason": feedback.reason,
            "message_id": assistant_message.id,
            "question_message_id": question_message.id,
            "question": question_message.content,
            "current_answer": assistant_message.content,
            "agent_slug": conversation.agent_id,
            "conversation_thread_id": conversation.thread_id,
            "qa_pair": qa_pair.to_dict() if qa_pair else None,
        }

    async def save_from_feedback(
        self,
        session: AsyncSession,
        *,
        feedback_id: int,
        answer: str,
        operator_uid: str,
    ) -> dict | None:
        context = await self.get_feedback_context(session, feedback_id)
        if context is None:
            return None
        if context["rating"] != "dislike":
            raise ValueError("仅点踩反馈支持答案调优")

        qa_pair = await CuratedQARepository(session).upsert(
            agent_slug=context["agent_slug"],
            question=context["question"],
            answer=self.normalize_answer(answer),
            operator_uid=operator_uid,
            source_type="feedback",
            source_feedback_id=feedback_id,
            source_message_id=context["message_id"],
        )
        await session.commit()
        await session.refresh(qa_pair)
        return qa_pair.to_dict()
