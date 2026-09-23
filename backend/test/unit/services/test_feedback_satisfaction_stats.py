"""满意度口径单元测试：可评价基数（收尾 AI 终答）与未反馈默认计满意。"""

from __future__ import annotations

import datetime as dt

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from yuxi.services.feedback_service import (
    build_refusal_stats,
    build_satisfaction_stats,
    count_evaluable_answers,
    count_knowledge_gap_answers,
    count_refusal_answers,
)
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
            thread_id="thread-1",
            uid="user-1",
            agent_id="agent-a",
            title="t1",
            status="active",
            created_at=now,
            updated_at=now,
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
            thread_id="thread-2",
            uid="user-1",
            agent_id="agent-a",
            title="t2",
            status="active",
            created_at=now,
            updated_at=now,
        )
        conv2_msgs = [
            Message(conversation=conv2, role="user", content="Q-refuse", created_at=now),
            Message(
                conversation=conv2,
                role="assistant",
                content="抱歉，未找到依据",
                created_at=now,
                extra_metadata={"knowledge_disposition": {"type": "knowledge_refusal"}},
            ),
        ]
        # 会话3：用户提问后尚无回答（进行中/中断）→ 不产生可评价基数。
        conv3 = Conversation(
            thread_id="thread-3",
            uid="user-1",
            agent_id="agent-a",
            title="t3",
            status="active",
            created_at=now,
            updated_at=now,
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


async def test_count_refusal_answers_counts_all_business_types_but_not_errors(satisfaction_session):
    db = satisfaction_session
    now = utc_now_naive()
    conversations = []
    for index, disposition_type in enumerate(("scope_refusal", "policy_refusal", "system_error"), start=4):
        conversation = Conversation(
            thread_id=f"thread-{index}",
            uid="user-1",
            agent_id="agent-a",
            title=f"t{index}",
            status="active",
            created_at=now,
            updated_at=now,
        )
        conversation.messages = [
            Message(role="user", content="Q", created_at=now),
            Message(
                role="assistant",
                content="result",
                created_at=now,
                extra_metadata={"knowledge_disposition": {"type": disposition_type}},
            ),
        ]
        conversations.append(conversation)
    db.add_all(conversations)
    await db.commit()

    assert await count_refusal_answers(db=db) == 3


async def test_count_refusal_answers_uses_terminal_answer_scope(satisfaction_session):
    assert await count_refusal_answers(db=satisfaction_session) == 1
    assert await count_refusal_answers(db=satisfaction_session, agent_id="agent-a") == 1
    assert await count_refusal_answers(db=satisfaction_session, agent_id="other-agent") == 0


async def test_build_refusal_stats_calculates_rate_and_handles_zero_denominator():
    assert build_refusal_stats(evaluable_count=4, refusal_count=1) == {
        "refusal_count": 1,
        "refusal_rate": 25.0,
    }
    assert build_refusal_stats(evaluable_count=0, refusal_count=0) == {
        "refusal_count": 0,
        "refusal_rate": 0.0,
    }


async def test_satisfaction_stats_no_feedback_counts_as_satisfied(satisfaction_session):
    db = satisfaction_session
    evaluable = await count_evaluable_answers(db=db)
    stats = build_satisfaction_stats(evaluable_count=evaluable, like_count=0, dislike_count=1)

    # 3 条可评价中 1 条点踩 → 2 条未反馈默认满意
    assert stats["evaluable_count"] == 3
    assert stats["silent_count"] == 2
    assert stats["satisfaction_rate"] == round(2 / 3 * 100, 2)


async def test_count_helpers_respect_time_bounds_over_beijing_days(satisfaction_session):
    """时间界按 naive UTC 比较，北京日界换算后的边界由路由层负责（见 test_dashboard_time_bounds）。

    三条终答：
      - convT1：北京 2026-09-01 00:00 整（= UTC 2026-08-31 16:00，左界含）
      - convT2：北京 2026-09-01 23:30（= UTC 2026-09-01 15:30，界内），knowledge_refusal
      - convT3：北京 2026-09-02 00:30（= UTC 2026-09-01 16:30，右界外），knowledge_refusal
    """
    db = satisfaction_session

    def terminal(thread_id, created_at, disposition=None):
        conversation = Conversation(
            thread_id=thread_id,
            uid="user-1",
            agent_id="agent-a",
            title=thread_id,
            status="active",
            created_at=created_at,
            updated_at=created_at,
        )
        extra = {"knowledge_disposition": {"type": disposition}} if disposition else None
        conversation.messages = [
            Message(role="user", content="Q", created_at=created_at),
            Message(role="assistant", content="A", created_at=created_at, extra_metadata=extra),
        ]
        return conversation

    t1 = dt.datetime(2026, 8, 31, 16, 0)
    t2 = dt.datetime(2026, 9, 1, 15, 30)
    t3 = dt.datetime(2026, 9, 1, 16, 30)
    db.add_all(
        [
            terminal("thread-t1", t1),
            terminal("thread-t2", t2, "knowledge_refusal"),
            terminal("thread-t3", t3, "knowledge_refusal"),
        ]
    )
    await db.commit()

    start_at = dt.datetime(2026, 8, 31, 16, 0)
    end_at = dt.datetime(2026, 9, 1, 16, 0)
    # fixture 既有数据 created_at=now（晚于固定日期），界内只剩新增三条终答中的 T1、T2
    assert await count_evaluable_answers(db=db, start_at=start_at, end_at=end_at) == 2
    assert await count_refusal_answers(db=db, start_at=start_at, end_at=end_at) == 1
    assert await count_knowledge_gap_answers(db=db, start_at=start_at, end_at=end_at) == 1

    # 仅下界：fixture 三条（now ≥ start_at）与 T1/T2/T3 全部计入
    assert await count_evaluable_answers(db=db, start_at=start_at) == 6
    # 仅上界：T1、T2 计入
    assert await count_evaluable_answers(db=db, end_at=end_at) == 2
    # 无时间界：fixture 3 条 + 新增 3 条
    assert await count_evaluable_answers(db=db) == 6
