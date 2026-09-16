"""客服会话只读浏览接口单测。

这一页的价值在于「这条会话为什么没出候选」，所以两个判据必须如实：
- `eligible` 必须与总结链路的确定性筛选同源（此处直接复用 `has_substantive_qa`），
  否则页面会给出与实际跑批不一致的「过筛」结论——正是之前「统计对不上」的那类问题；
- `has_candidates` 过滤的**方向**不能反（true 要 EXISTS、false 要 NOT EXISTS），
  反了会静默给出互补的结果集，界面上看不出错。
"""

from __future__ import annotations

import re

import pytest
from fastapi import HTTPException

from server.routers.curated_qa_router import (
    _load_conversation_page,
    get_udesk_conversation,
)
from yuxi.storage.postgres.models_udesk import UdeskConversation, UdeskMessage
from yuxi.utils.datetime_utils import utc_now

pytestmark = [pytest.mark.asyncio, pytest.mark.unit]


class _FakeScalars:
    def __init__(self, values):
        self._values = values

    def all(self):
        return list(self._values)


class _FakeResult:
    def __init__(self, *, rows=None, scalar=None, scalars=None):
        self._rows = rows or []
        self._scalar = scalar
        self._scalars = scalars or []

    def all(self):
        return list(self._rows)

    def scalar(self):
        return self._scalar

    def scalar_one_or_none(self):
        return self._scalar

    def scalars(self):
        return _FakeScalars(self._scalars)


class _FakeDb:
    """按调用顺序吐出结果：COUNT → 会话页 → 页内消息 → 页内候选数。"""

    def __init__(self, *results):
        self._results = list(results)
        self.executed: list[object] = []

    async def execute(self, statement):
        self.executed.append(statement)
        return self._results.pop(0)

    @property
    def pending(self) -> int:
        return len(self._results)


def _compiled(statement) -> str:
    """编译成字面量 SQL 并去掉空白与括号，便于断言 EXISTS / NOT EXISTS 的方向。"""
    sql = str(statement.compile(compile_kwargs={"literal_binds": True}))
    return re.sub(r"[\s()]", "", sql).upper()


async def test_list_computes_metrics_and_eligibility_from_page_messages():
    conversations = [
        UdeskConversation(conversation_id="c-eligible", started_at=utc_now()),
        UdeskConversation(conversation_id="c-filtered", started_at=utc_now()),
    ]
    db = _FakeDb(
        _FakeResult(scalar=2),
        _FakeResult(scalars=conversations),
        _FakeResult(
            rows=[
                ("c-eligible", "customer", "怎么开专票"),
                ("c-eligible", "agent", "在订单页点申请开票即可"),
                ("c-eligible", "customer", "好"),
                ("c-filtered", "system", "会话结束"),
            ]
        ),
        _FakeResult(rows=[("c-eligible", 2)]),
    )

    total, items = await _load_conversation_page(db, keyword=None, has_candidates=None, limit=20, offset=0)

    assert total == 2
    eligible, filtered = items
    # 「好」只有 1 字、system 报文只有 4 字，两侧各有 ≥5 字的实质问答才算过筛
    assert eligible["eligible"] is True
    assert eligible["message_count"] == 3 and eligible["customer_message_count"] == 2
    assert eligible["candidate_count"] == 2
    assert filtered["eligible"] is False
    assert filtered["message_count"] == 1 and filtered["customer_message_count"] == 0
    assert filtered["candidate_count"] == 0


async def test_list_skips_metric_queries_on_empty_page():
    """空页不能对空 id 列表发 IN 查询（那会多两条无谓 SQL）。"""
    db = _FakeDb(_FakeResult(scalar=0), _FakeResult(scalars=[]))

    total, items = await _load_conversation_page(db, keyword=None, has_candidates=False, limit=20, offset=0)

    assert (total, items) == (0, [])
    assert db.pending == 0


async def test_candidate_filter_direction_is_not_inverted():
    for wanted, expected in ((True, "EXISTS"), (False, "NOTEXISTS")):
        db = _FakeDb(_FakeResult(scalar=0), _FakeResult(scalars=[]))

        await _load_conversation_page(db, keyword="1355", has_candidates=wanted, limit=20, offset=0)

        sql = _compiled(db.executed[-1])
        assert expected in sql
        assert "LIKE" in sql  # 关键词过滤与候选过滤同时生效


async def test_detail_returns_messages():
    conversation = UdeskConversation(conversation_id="135506607")
    messages = [
        UdeskMessage(message_id="m1", conversation_id="135506607", role="customer", content="怎么开专票"),
        UdeskMessage(message_id="m2", conversation_id="135506607", role="agent", content="在订单页点申请开票即可"),
    ]
    db = _FakeDb(_FakeResult(scalar=conversation), _FakeResult(scalars=messages))

    payload = await get_udesk_conversation("135506607", db=db, current_user=None)

    assert payload["conversation"]["conversation_id"] == "135506607"
    assert [item["role"] for item in payload["messages"]] == ["customer", "agent"]
    assert payload["messages"][0]["content"] == "怎么开专票"


async def test_detail_raises_404_when_conversation_missing():
    db = _FakeDb(_FakeResult(scalar=None))

    with pytest.raises(HTTPException) as exc:
        await get_udesk_conversation("gone", db=db, current_user=None)

    assert exc.value.status_code == 404
