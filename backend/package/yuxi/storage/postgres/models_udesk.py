"""Udesk 客服记录与知识回流候选数据模型。

设计见 docs/vibe/udesk/2026-09-14-udesk-to-knowledge-pipeline-design.md §五。
三张表均为「先脱敏再落库」的产物（规范 C3）：customer 标识只存哈希；
stats_json 仅存白名单质量指标字段，严禁整包落库（C1）。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, Column, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint

from yuxi.storage.postgres.models_business import Base
from yuxi.utils.datetime_utils import format_utc_datetime, utc_now

# 时间列一律 timezone=True：库内实际类型为 TIMESTAMPTZ，而数据库会话时区是
# Asia/Shanghai。若模型声明为 naive DateTime，SQLAlchemy 会按 timestamp(无时区) 绑定，
# Postgres 再按会话时区解释 → 写进去的 UTC 时刻被当成北京时间，整体偏 -8 小时。
# 默认值与显式赋值也都必须传 aware（utc_now），naive 一律视为 bug。


class UdeskConversation(Base):
    """脱敏后的 Udesk 会话级记录。"""

    __tablename__ = "udesk_conversations"
    __table_args__ = (
        UniqueConstraint("conversation_id", name="uq_udesk_conversations_conversation_id"),
        Index("ix_udesk_conversations_started_at", "started_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    conversation_id = Column(String(128), nullable=False, index=True)
    # 客户标识哈希（原 token 不落库）；原始会话记录按 TTL 定期清理（C4/D7）
    customer_token_hash = Column(String(128), nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    ended_at = Column(DateTime(timezone=True), nullable=True)
    # 白名单质量指标（消息计数/时长/转人工/渠道来源/满意度等），字段级脱敏；
    # 白名单见 pull_service.CONVERSATION_*_FIELDS
    stats_json = Column(JSON, nullable=True)
    synced_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    # LLM 结构化完成时刻（含"筛掉不值得总结"的判定）；失败不落值，下轮重试
    summarized_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "conversation_id": self.conversation_id,
            "customer_token_hash": self.customer_token_hash,
            "started_at": format_utc_datetime(self.started_at),
            "ended_at": format_utc_datetime(self.ended_at),
            "stats_json": self.stats_json,
            "synced_at": format_utc_datetime(self.synced_at),
            "summarized_at": format_utc_datetime(self.summarized_at),
        }


class UdeskMessage(Base):
    """脱敏后的 Udesk 消息级记录（逐条问答的原料）。"""

    __tablename__ = "udesk_messages"
    __table_args__ = (
        UniqueConstraint("message_id", name="uq_udesk_messages_message_id"),
        Index("ix_udesk_messages_conversation_sent", "conversation_id", "sent_at"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    message_id = Column(String(128), nullable=False, index=True)
    conversation_id = Column(
        String(128),
        ForeignKey("udesk_conversations.conversation_id", ondelete="CASCADE"),
        nullable=False,
    )
    # customer（用户）/ agent（人工客服）/ robot（机器人）/ system
    role = Column(String(16), nullable=False)
    content = Column(Text, nullable=False)
    # 消息信封的类型：message（双方往来）/ rich（富文本卡片）/ close（会话结束系统报文）
    content_type = Column(String(32), nullable=True)
    # 旧契约（KM/机器人族的 logType：4=用户提出的问题 等）字段，现接口无等价字段，恒为 NULL
    log_type = Column(Integer, nullable=True)
    sent_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "message_id": self.message_id,
            "conversation_id": self.conversation_id,
            "role": self.role,
            "content": self.content,
            "content_type": self.content_type,
            "log_type": self.log_type,
            "sent_at": format_utc_datetime(self.sent_at),
        }


class CuratedQACandidate(Base):
    """客服记录 LLM 结构化后的候选知识（待人工审核，启用后写入 curated_qa_pairs）。

    source_conversation_id 不设外键：原始会话按 TTL 清理后候选与已入库问答对仍需可溯源。
    """

    __tablename__ = "curated_qa_candidates"
    __table_args__ = (
        UniqueConstraint("source_conversation_id", "question_hash", name="uq_curated_qa_candidates_conv_question"),
        Index("ix_curated_qa_candidates_review_status", "review_status"),
        Index("ix_curated_qa_candidates_domain", "domain"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    source_conversation_id = Column(String(128), nullable=False, index=True)
    question = Column(Text, nullable=False)
    # 归一化口径与 curated_qa_repository.normalize_qa_question 一致，便于采纳时直接迁移
    normalized_question = Column(Text, nullable=False)
    question_hash = Column(String(64), nullable=False)
    answer = Column(Text, nullable=False)
    # 业务域，取 BusinessLine.code
    domain = Column(String(32), nullable=True)
    confidence = Column(Float, nullable=True)
    # 客服原话片段（C7：审核核对用，LLM 不得改动事实）
    evidence_quote = Column(Text, nullable=True)
    ambiguity_note = Column(Text, nullable=True)
    # 查重结论：pending / duplicate / conflict / unique
    dedup_status = Column(String(32), nullable=False, default="pending")
    # 审核状态：pending / accepted（C6：审核通过前绝不写入启用问答对）。
    # 不采纳就是删除行，不再落 rejected；历史行仍可能是 rejected，前端按只读标签渲染。
    review_status = Column(String(32), nullable=False, default="pending")
    reviewed_by = Column(String(100), nullable=True)
    reviewed_at = Column(DateTime(timezone=True), nullable=True)
    review_note = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source_conversation_id": self.source_conversation_id,
            "question": self.question,
            "answer": self.answer,
            "domain": self.domain,
            "confidence": self.confidence,
            "evidence_quote": self.evidence_quote,
            "ambiguity_note": self.ambiguity_note,
            "dedup_status": self.dedup_status,
            "review_status": self.review_status,
            "reviewed_by": self.reviewed_by,
            "reviewed_at": format_utc_datetime(self.reviewed_at),
            "review_note": self.review_note,
            "created_at": format_utc_datetime(self.created_at),
        }


class UdeskSyncState(Base):
    """拉取 / 总结的同步状态（单行表，id 恒为 1）：游标水位 + 运行租约 + 进度。

    - watermark 只在「拉取 + 入库」全部成功后推进（D8/D13），推进时带
      `watermark < 新值` 条件永不倒退；游标只负责「不漏」，重复由唯一键兜底（D11）。
    - lease_expires_at / summarize_lease_expires_at 是执行防并发的第二道闸
      （第一道为 arq unique job id）：非空且未过期 → 已有一份在跑；带 TTL，
      进程崩溃后自然过期不永久堵塞。
    - progress_done/progress_total 是**本轮**拉取进度，逐会话提交，供页面在一轮
      长达数分钟的运行中看到进展。total 是「列表搜索已发现的会话数」而非待处理
      总数，分母会随翻页增长，故为处理次数口径，不保证 done/total 收敛到 1。
      抢到租约时清零。
    - 会话侧无「新增」口径：upsert 是 ON CONFLICT DO UPDATE，rowcount 区分不出
      新增与更新，故只报处理数；消息侧是 DO NOTHING + RETURNING，新增数是真值。
    """

    __tablename__ = "udesk_sync_state"

    id = Column(Integer, primary_key=True)
    # 最后成功同步位点（UTC aware）
    watermark = Column(DateTime(timezone=True), nullable=True)
    lease_expires_at = Column(DateTime(timezone=True), nullable=True)
    last_run_at = Column(DateTime(timezone=True), nullable=True)
    # running / succeeded / failed / skipped_lease
    last_run_status = Column(String(32), nullable=True)
    last_error = Column(Text, nullable=True)
    # 本轮已处理会话数 / 本轮真实新增消息数
    last_run_conversations = Column(Integer, nullable=False, default=0)
    last_run_messages = Column(Integer, nullable=False, default=0)
    # 本轮拉取进度（抢到租约时清零）
    progress_done = Column(Integer, nullable=False, default=0)
    progress_total = Column(Integer, nullable=False, default=0)
    # 总结链路的运行状态，与拉取分开（两者可以同时在跑）
    summarize_lease_expires_at = Column(DateTime(timezone=True), nullable=True)
    # running / succeeded / failed / skipped_lease
    summarize_status = Column(String(32), nullable=True)
    summarize_last_error = Column(Text, nullable=True)
    summarize_last_run_at = Column(DateTime(timezone=True), nullable=True)
    # 本轮真实新增的候选问答条数。页面要落一句「新生成 N 条候选问答对」，
    # 而 run_batch 的计数原先只返回给 arq 就丢了——这里存下来，累计口径
    # （候选表总行数）与单轮口径才分得开
    summarize_last_candidates = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "watermark": format_utc_datetime(self.watermark),
            "lease_expires_at": format_utc_datetime(self.lease_expires_at),
            "last_run_at": format_utc_datetime(self.last_run_at),
            "last_run_status": self.last_run_status,
            "last_error": self.last_error,
            "last_run_conversations": self.last_run_conversations,
            "last_run_messages": self.last_run_messages,
            "progress_done": self.progress_done,
            "progress_total": self.progress_total,
            "summarize_lease_expires_at": format_utc_datetime(self.summarize_lease_expires_at),
            "summarize_status": self.summarize_status,
            "summarize_last_error": self.summarize_last_error,
            "summarize_last_run_at": format_utc_datetime(self.summarize_last_run_at),
            "summarize_last_candidates": self.summarize_last_candidates,
            "updated_at": format_utc_datetime(self.updated_at),
        }
