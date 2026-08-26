"""人工确认问答对数据模型。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, Boolean, Column, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint

from yuxi.storage.postgres.models_business import Base
from yuxi.utils.datetime_utils import format_utc_datetime, utc_now_naive


class CuratedQAPair(Base):
    """管理员确认后的高优先级问答对。"""

    __tablename__ = "curated_qa_pairs"
    __table_args__ = (
        UniqueConstraint("agent_slug", "question_hash", name="uq_curated_qa_agent_question"),
        Index("ix_curated_qa_agent_enabled", "agent_slug", "enabled"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    agent_slug = Column(String(100), nullable=False, index=True)
    question = Column(Text, nullable=False)
    normalized_question = Column(Text, nullable=False)
    question_hash = Column(String(64), nullable=False, index=True)
    # 问题向量（JSON 数组），用于语义召回；懒回填，缺失时按需计算落库
    question_embedding = Column(JSON, nullable=True)
    answer = Column(Text, nullable=False)
    enabled = Column(Boolean, nullable=False, default=True)

    source_type = Column(String(32), nullable=False, default="feedback")
    source_feedback_id = Column(
        Integer,
        ForeignKey("message_feedbacks.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    source_message_id = Column(
        Integer,
        ForeignKey("messages.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    created_by = Column(String(100), nullable=False)
    updated_by = Column(String(100), nullable=False)
    hit_count = Column(Integer, nullable=False, default=0)
    last_hit_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=utc_now_naive)
    updated_at = Column(DateTime, nullable=False, default=utc_now_naive, onupdate=utc_now_naive)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "agent_slug": self.agent_slug,
            "question": self.question,
            "answer": self.answer,
            "enabled": bool(self.enabled),
            "source_type": self.source_type,
            "source_feedback_id": self.source_feedback_id,
            "source_message_id": self.source_message_id,
            "created_by": self.created_by,
            "updated_by": self.updated_by,
            "hit_count": self.hit_count,
            "last_hit_at": format_utc_datetime(self.last_hit_at),
            "created_at": format_utc_datetime(self.created_at),
            "updated_at": format_utc_datetime(self.updated_at),
        }
