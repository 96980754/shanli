from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models import Conversation, KnowledgeIssue, Message, MessageFeedback


class QaOpsService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def record_answer(
        self,
        kb_id: int,
        user_id: int,
        question: str,
        answer: str,
        sources: list[dict[str, Any]],
        conversation_id: int | None = None,
    ) -> tuple[Conversation, Message]:
        if conversation_id is None:
            conversation = Conversation(kb_id=kb_id, user_id=user_id, title=question[:255])
            self.session.add(conversation)
            self.session.flush()
        else:
            conversation = (
                self.session.query(Conversation)
                .filter(Conversation.id == conversation_id, Conversation.kb_id == kb_id, Conversation.user_id == user_id)
                .one_or_none()
            )
            if conversation is None:
                raise ValueError("Conversation not found")

        conversation.updated_at = datetime.utcnow()
        message = Message(
            conversation_id=conversation.id,
            kb_id=kb_id,
            user_id=user_id,
            question=question,
            answer=answer,
            sources=json.dumps(sources, ensure_ascii=False),
        )
        self.session.add(message)
        self.session.commit()
        self.session.refresh(conversation)
        self.session.refresh(message)
        return conversation, message

    def list_conversations(self, kb_id: int, user_id: int) -> list[Conversation]:
        return (
            self.session.query(Conversation)
            .filter(Conversation.kb_id == kb_id, Conversation.user_id == user_id)
            .order_by(Conversation.updated_at.desc(), Conversation.id.desc())
            .all()
        )

    def get_conversation(self, conversation_id: int) -> Conversation | None:
        return self.session.get(Conversation, conversation_id)

    def list_messages(self, conversation_id: int, user_id: int) -> list[Message] | None:
        conversation = self.get_conversation(conversation_id)
        if conversation is None or conversation.user_id != user_id:
            return None
        return (
            self.session.query(Message)
            .filter(Message.conversation_id == conversation_id)
            .order_by(Message.id)
            .all()
        )

    def get_message(self, message_id: int) -> Message | None:
        return self.session.get(Message, message_id)

    def save_feedback(
        self,
        message_id: int,
        user_id: int,
        is_helpful: bool,
        feedback_text: str,
    ) -> tuple[MessageFeedback, KnowledgeIssue | None]:
        message = self.get_message(message_id)
        if message is None or message.user_id != user_id:
            raise ValueError("Message not found")
        feedback = (
            self.session.query(MessageFeedback)
            .filter(MessageFeedback.message_id == message_id, MessageFeedback.user_id == user_id)
            .one_or_none()
        )
        if feedback is None:
            feedback = MessageFeedback(message_id=message_id, user_id=user_id, is_helpful=is_helpful)
            self.session.add(feedback)
        feedback.is_helpful = is_helpful
        feedback.feedback_text = feedback_text

        issue = None
        if not is_helpful:
            issue = (
                self.session.query(KnowledgeIssue)
                .filter(KnowledgeIssue.message_id == message_id, KnowledgeIssue.reason == "negative_feedback")
                .one_or_none()
            )
            if issue is None:
                issue = KnowledgeIssue(
                    kb_id=message.kb_id,
                    message_id=message.id,
                    question=message.question,
                    user_query=message.question,
                    reason="negative_feedback",
                    classification="negative_feedback",
                    status="open",
                )
                self.session.add(issue)
            issue.feedback_text = feedback_text
            issue.status = "open"
            issue.updated_at = datetime.utcnow()

        self.session.commit()
        self.session.refresh(feedback)
        if issue is not None:
            self.session.refresh(issue)
        return feedback, issue

    def list_issues(self, kb_id: int, status: str | None = None) -> list[KnowledgeIssue]:
        query = self.session.query(KnowledgeIssue).filter(KnowledgeIssue.kb_id == kb_id)
        if status:
            query = query.filter(KnowledgeIssue.status == status)
        return query.order_by(KnowledgeIssue.id.desc()).all()

    def get_issue(self, issue_id: int) -> KnowledgeIssue | None:
        return self.session.get(KnowledgeIssue, issue_id)

    def update_issue_status(self, issue_id: int, status: str) -> KnowledgeIssue | None:
        if status not in {"open", "resolved", "ignored"}:
            raise ValueError("Invalid issue status")
        issue = self.get_issue(issue_id)
        if issue is None:
            return None
        issue.status = status
        issue.updated_at = datetime.utcnow()
        self.session.commit()
        self.session.refresh(issue)
        return issue
