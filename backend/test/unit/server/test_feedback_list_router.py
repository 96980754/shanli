"""反馈列表接口单测：分页/筛选/派生列（拒答来源、已补答）与 status PATCH。"""

from __future__ import annotations

from datetime import timedelta

import pytest
import pytest_asyncio
from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from server.routers.dashboard_router import (
    FeedbackStatusUpdate,
    get_all_feedbacks,
    update_feedback_status,
)
from yuxi.repositories.curated_qa_repository import hash_qa_question, normalize_qa_question
from yuxi.storage.postgres.models_business import (
    Base,
    Conversation,
    Message,
    MessageFeedback,
    User,
)
from yuxi.storage.postgres.models_curated_qa import CuratedQAPair
from yuxi.utils.datetime_utils import utc_now_naive

pytestmark = [pytest.mark.asyncio, pytest.mark.unit]


@pytest_asyncio.fixture()
async def feedback_list_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        now = utc_now_naive()
        alice = User(username="Alice", uid="u1", password_hash="x", role="user")
        db.add(alice)
        conv_a = Conversation(
            thread_id="t-fb-1", uid="u1", agent_id="agent-a", title="咨询系统",
            status="active", created_at=now, updated_at=now,
        )
        conv_b = Conversation(
            thread_id="t-fb-2", uid="u1", agent_id="agent-a", title="部署求助",
            status="active", created_at=now, updated_at=now,
        )
        conv_a_msgs = [
            Message(conversation=conv_a, role="user", content="A1问题", created_at=now),
            Message(
                conversation=conv_a, role="assistant", content="正常回答1", created_at=now,
                extra_metadata={"knowledge_disposition": {"type": "answered"}},
            ),
        ]
        conv_b_msgs = [
            Message(conversation=conv_b, role="user", content="怎么部署平台", created_at=now),
            Message(
                conversation=conv_b, role="assistant", content="抱歉，该问题不在业务范围", created_at=now,
                extra_metadata={
                    "knowledge_disposition": {"type": "scope_refusal", "reason": "off_topic"},
                    "handoff_available": False,
                },
            ),
            Message(conversation=conv_b, role="user", content="如何开通权限", created_at=now),
            Message(
                conversation=conv_b, role="assistant", content="权限开通步骤如下", created_at=now,
                extra_metadata={"knowledge_disposition": {"type": "answered"}},
            ),
        ]
        db.add_all([conv_a, conv_b] + conv_a_msgs + conv_b_msgs)
        await db.commit()
        for msg in conv_a_msgs + conv_b_msgs:
            await db.refresh(msg)

        fb_a1 = MessageFeedback(
            message_id=conv_a_msgs[1].id, uid="u1", rating="like",
            status="pending", created_at=now,
        )
        fb_b1 = MessageFeedback(
            message_id=conv_b_msgs[1].id, uid="u1", rating="dislike",
            reason="不该拒答", status="pending", created_at=now + timedelta(minutes=1),
        )
        fb_b2 = MessageFeedback(
            message_id=conv_b_msgs[3].id, uid="u1", rating="dislike",
            reason="不够详细", status="processed", created_at=now + timedelta(minutes=2),
        )
        db.add_all([fb_a1, fb_b1, fb_b2])
        question = conv_b_msgs[2].content
        db.add(
            CuratedQAPair(
                agent_slug="agent-a",
                question=question,
                normalized_question=normalize_qa_question(question),
                question_hash=hash_qa_question(normalize_qa_question(question)),
                answer="开通权限需联系调度台管理员",
                enabled=True,
                created_by="admin",
                updated_by="admin",
            )
        )
        await db.commit()
        for fb in (fb_a1, fb_b1, fb_b2):
            await db.refresh(fb)
        yield db, {"fb_a1": fb_a1, "fb_b1": fb_b1, "fb_b2": fb_b2}
    await engine.dispose()


def _by_id(items):
    return {item["id"]: item for item in items}


async def test_feedback_list_returns_paginated_envelope_with_derived_flags(feedback_list_session):
    db, fbs = feedback_list_session
    data = await get_all_feedbacks(db=db, current_user=None)

    assert data["total"] == 3
    # 默认按时间倒序：fb_b2（最新）在前
    assert [item["id"] for item in data["items"]] == [fbs["fb_b2"].id, fbs["fb_b1"].id, fbs["fb_a1"].id]
    by_id = _by_id(data["items"])
    # 拒答来源：仅 scope_refusal 消息被评那条为 True
    assert by_id[fbs["fb_b1"].id]["is_refusal_source"] is True
    assert by_id[fbs["fb_b2"].id]["is_refusal_source"] is False
    # 已补答：fb_b2 的问题「如何开通权限」已有人工问答对
    assert by_id[fbs["fb_b2"].id]["has_qa_pair"] is True
    assert by_id[fbs["fb_a1"].id]["has_qa_pair"] is False
    assert by_id[fbs["fb_b2"].id]["conversation_thread_id"] == "t-fb-2"
    for item in data["items"]:
        assert item["status"] in {"pending", "processed", "ignored"}


async def test_feedback_list_filters_rating_status_and_keyword(feedback_list_session):
    db, fbs = feedback_list_session

    dislike = await get_all_feedbacks(rating="dislike", db=db, current_user=None)
    assert dislike["total"] == 2

    processed = await get_all_feedbacks(status="processed", db=db, current_user=None)
    assert processed["total"] == 1
    assert processed["items"][0]["id"] == fbs["fb_b2"].id

    keyword = await get_all_feedbacks(keyword="权限", db=db, current_user=None)
    assert keyword["total"] == 1
    assert keyword["items"][0]["id"] == fbs["fb_b2"].id

    no_hit = await get_all_feedbacks(keyword="不存在的词", db=db, current_user=None)
    assert no_hit["total"] == 0


async def test_feedback_list_pagination_and_ordering(feedback_list_session):
    db, fbs = feedback_list_session

    page1 = await get_all_feedbacks(limit=2, offset=0, db=db, current_user=None)
    assert page1["total"] == 3
    assert len(page1["items"]) == 2

    page2 = await get_all_feedbacks(limit=2, offset=2, db=db, current_user=None)
    assert len(page2["items"]) == 1
    assert page2["items"][0]["id"] == fbs["fb_a1"].id

    asc = await get_all_feedbacks(order_by="created_asc", db=db, current_user=None)
    assert asc["items"][0]["id"] == fbs["fb_a1"].id


async def test_update_feedback_status_persists_and_404_for_missing(feedback_list_session):
    db, fbs = feedback_list_session

    result = await update_feedback_status(
        fbs["fb_a1"].id, FeedbackStatusUpdate(status="ignored"), db=db, current_user=None
    )
    assert result == {"id": fbs["fb_a1"].id, "status": "ignored"}

    count = await db.execute(
        func.count(MessageFeedback.id).select().where(
            MessageFeedback.id == fbs["fb_a1"].id, MessageFeedback.status == "ignored"
        )
    )
    assert count.scalar() == 1

    with pytest.raises(HTTPException) as exc_info:
        await update_feedback_status(999999, FeedbackStatusUpdate(status="processed"), db=db, current_user=None)
    assert exc_info.value.status_code == 404
