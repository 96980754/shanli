"""满意度口径单元测试：可评价基数（收尾 AI 终答）与未反馈默认计满意。"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from yuxi.services.feedback_service import build_satisfaction_stats, count_evaluable_answers
from yuxi.storage.postgres.models_business import (
    Base,
    Conversation,
    Message,
    MessageFeedback,
    User,
)
from yuxi.utils.datetime_utils import utc_now_naive

pytestmark = [pytest.mark.asyncio, pytest.mark.unit]


@pytest_asyncio.fixture()
async def satisfaction_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        now = utc_now_naive()
        user = User(username="User", uid="user-1", password_hash="x", role="user")
        db.add(user)
        # 会话1：一轮问答 = 用户提问 + 中间思考行 + 终答；再补一轮用户提问 + 终答。
        conv1 = Conversation(
            thread_id="thread-1", uid="user-1", agent_id="agent-a", title="t1", status="active",
            created_at=now, updated_at=now,
        )
        conv1_msgs = [
            Message(conversation=conv1, role="user", content="Q1", created_at=now),
            Message(conversation=conv1, role="assistant", content="让我查一下…", created_at=now),
            Message(conversation=conv1, role="assistant", content="回答1", created_at=now),
            Message(conversation=conv1, role="user", content="Q2", created_at=now),
            Message(conversation=conv1, role="assistant", content="回答2", created_at=now),
        ]
        # 会话2：单轮拒答终答（含拒答口径应计入）。
        conv2 = Conversation(
            thread_id="thread-2", uid="user-1", agent_id="agent-a", title="t2", status="active",
            created_at=now, updated_at=now,
        )
        conv2_msgs = [
            Message(conversation=conv2, role="user", content="Q-refuse", created_at=now),
            Message(conversation=conv2, role="assistant", content="抱歉，未找到依据", created_at=now),
        ]
        # 会话3：用户提问后尚无回答（进行中/中断）→ 不产生可评价基数。
        conv3 = Conversation(
            thread_id="thread-3", uid="user-1", agent_id="agent-a", title="t3", status="active",
            created_at=now, updated_at=now,
        )
        conv3_msgs = [Message(conversation=conv3, role="user", content="Q3", created_at=now)]
        db.add_all([conv1, conv2, conv3] + conv1_msgs + conv2_msgs + conv3_msgs)
        await db.commit()
        for msg in conv1_msgs + conv2_msgs + conv3_msgs:
            await db.refresh(msg)
        # 对会话1「回答1」点踩
        feedback = MessageFeedback(message_id=conv1_msgs[2].id, uid="user-1", rating="dislike", reason="答案有误")
        db.add(feedback)
        await db.commit()
        yield db
    await engine.dispose()


async def test_count_evaluable_answers_counts_only_turn_ending_ai(satisfaction_session):
    # 可评价 = 回答1（下一条是 user）、回答2（无下一条）、拒答终答（无下一条）→ 3 条；
    # 中间思考行（下一条是 assistant）与 Q3 后的缺失回答不计入。
    assert await count_evaluable_answers(db=satisfaction_session) == 3


async def test_count_evaluable_answers_scoped_by_agent(satisfaction_session):
    assert await count_evaluable_answers(db=satisfaction_session, agent_id="agent-a") == 3
    assert await count_evaluable_answers(db=satisfaction_session, agent_id="other-agent") == 0


async def test_satisfaction_stats_no_feedback_counts_as_satisfied(satisfaction_session):
    db = satisfaction_session
    evaluable = await count_evaluable_answers(db=db)
    stats = build_satisfaction_stats(evaluable_count=evaluable, like_count=0, dislike_count=1)

    # 3 条可评价中 1 条点踩 → 2 条未反馈默认满意
    assert stats["evaluable_count"] == 3
    assert stats["silent_count"] == 2
    assert stats["satisfaction_rate"] == round(2 / 3 * 100, 2)
