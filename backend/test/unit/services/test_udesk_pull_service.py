"""Udesk 增量拉取服务单测（规范 G5：全部 Mock，不依赖真实凭证与真实库）。

覆盖：脱敏/时区/白名单行构造/信封解析/消息正文抽取（纯函数），以及租约、游标推进、
重叠窗、翻页、分窗、按结束时刻的第二遍、日志窗口夹取、尾巴回补、对账回补
（FakeClient + FakeSession 的 SQL 与请求参数断言）。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from yuxi.services.udesk.client import UdeskResponse
from yuxi.services.udesk.pull_service import (
    API_HISTORY_DAYS,
    LEASE_MINUTES,
    LOGS_PATH,
    PAGE_SIZE,
    SESSIONS_PATH,
    UdeskPullError,
    UdeskPullService,
    desensitize_text,
    extract_page,
    parse_beijing_string,
    to_beijing_string,
    to_conversation_row,
    to_message_rows,
)
from yuxi.utils.datetime_utils import UTC

NOW = datetime(2026, 9, 14, 12, 0, 0, tzinfo=UTC)


def _ok(data):
    # 对话记录接口族：成功码是 status=0，响应里没有 code 字段
    return UdeskResponse(ok=True, code=0, message="成功", family="im_sessions", business_state=None, data=data)


def _fail(message="业务失败"):
    return UdeskResponse(ok=False, code=2000, message=message, family="im_sessions", business_state=None, data=None)


def _page(items=(), *, total=None, total_pages=None):
    """search 与 log 共用的响应信封。"""
    rows = list(items)
    return _ok(
        {
            "status": 0,
            "message": "成功",
            "item": rows,
            "size": len(rows),
            "total": len(rows) if total is None else total,
            "total_pages": (1 if rows else 0) if total_pages is None else total_pages,
        }
    )


def _session(session_id=99, created="2026-09-14 20:00:00", **extra):
    return {"session_id": session_id, "created_at": created, **extra}


def _log_item(
    message_id="m1", sender="customer", text="正文", created="2026-09-14 20:00:00", *, kind="message", welcome=False
):
    """聊天记录项：content 实测是 JSON 信封（101/101），正文在内层 data.content。"""
    envelope = {"type": kind, "data": {"content": text, "event_text": "", "richContent": ""}, "platform": "web"}
    if welcome:
        envelope["is_welcome"] = True
    return {
        "message_id": message_id,
        "sender": sender,
        "content": json.dumps(envelope, ensure_ascii=False),
        "created_at": created,
    }


# --------------------------------------------------------------- 纯函数
def test_desensitize_text_masks_phone_email_and_ipv4():
    masked = desensitize_text("联系 13812345678 或 zhang.san@example.com，源 IP 192.168.1.100")
    assert "13812345678" not in masked
    assert "zhang.san@example.com" not in masked
    assert "192.168.1.100" not in masked
    assert "138****78" in masked and "z***@example.com" in masked and "192.168.*.*" in masked


def test_desensitize_text_masks_phone_with_country_prefix():
    """回归：`+86` 紧贴号码的写法此前整串逃逸。

    旧正则 `(?<!\\d)1[3-9]\\d{9}(?!\\d)` 的前视断言在 `+8617712340018` 上被那个 `6` 挡住，
    号码原样入库、还被 LLM 抄进候选答案，最终经采纳进到 curated_qa_pairs。
    """
    for raw, expected in (
        ("add my whatsapp +8617712340018", "add my whatsapp +86177****18"),
        ("add my whatsapp +86 17712340018", "add my whatsapp +86 177****18"),
        ("联系 86-17712340018", "联系 86-177****18"),
        ("裸号 17712340018", "裸号 177****18"),  # 无前缀行为不变
    ):
        masked = desensitize_text(raw)
        assert masked == expected, raw
        assert "17712340018" not in masked


def test_desensitize_text_passthrough_empty():
    assert desensitize_text(None) is None
    assert desensitize_text("") == ""


def test_beijing_string_roundtrip_and_second_precision():
    assert to_beijing_string(NOW) == "2026-09-14 20:00:00"
    # 接口可能带毫秒/时区后缀，解析只取前 19 位
    assert parse_beijing_string("2026-09-14 20:00:00.123456") == NOW
    assert parse_beijing_string(None) is None


def test_extract_page_reads_envelope_and_fails_loud_on_missing_shape():
    items, total, pages = extract_page(
        {"status": 0, "item": [{"session_id": 1}], "size": 1, "total": 3, "total_pages": 2}, what="会话列表"
    )
    assert items == [{"session_id": 1}] and total == 3 and pages == 2
    with pytest.raises(UdeskPullError):
        extract_page({"status": 0, "total": 0, "total_pages": 0}, what="会话列表")  # 缺 item 数组
    with pytest.raises(UdeskPullError):
        extract_page({"status": 0, "item": []}, what="会话列表")  # 缺 total / total_pages
    with pytest.raises(UdeskPullError):
        extract_page({"status": 0, "item": [], "total": "3", "total_pages": 1}, what="会话列表")


# --------------------------------------------------- 白名单行构造（C1/C3）
def test_to_conversation_row_whitelist_desensitize_and_hash_only():
    session = {
        "session_id": 99,
        "created_at": "2026-09-14 20:00:00",
        "closed_at": "2026-09-14 20:05:00",
        "customer_msg_num": 3,
        "queue_seconds": "12",  # 实测为字符串：旧实现的 isinstance(int) 过滤会静默丢字段
        "transfer_to_agent": False,
        "manual_summary": "客户来电 13812345678 询问发票",
        "agent_nick_name": "小王",
        # 以下均为客户 PII：白名单外，必须整体挡下
        "customer_name": "张三",
        "customer_cell_phone": "13812345678",
        "customer_email": "zhang.san@example.com",
        "ip_loc": "192.168.1.100",
        "customer_token": "raw-token-abc",
        "note_content": "内部备注",
    }
    row = to_conversation_row(session)

    assert row["conversation_id"] == "99"
    assert row["started_at"] == NOW and row["ended_at"] == NOW + timedelta(minutes=5)
    # 客户标识只存哈希（C3）
    assert row["customer_token_hash"] != "raw-token-abc"
    assert len(row["customer_token_hash"]) == 64

    stats = json.loads(row["stats_json"])
    assert stats["customer_msg_num"] == 3
    assert stats["queue_seconds"] == "12"
    assert stats["transfer_to_agent"] is False
    assert stats["agent_nick_name"] == "小王"
    assert "138****78" in stats["manual_summary"]  # 自由文本必须过脱敏
    leaked_fields = (
        "customer_name",
        "customer_cell_phone",
        "customer_email",
        "ip_loc",
        "note_content",
        "customer_token",
    )
    for leaked in leaked_fields:
        assert leaked not in stats
    for leaked_value in ("张三", "raw-token-abc", "zhang.san@example.com", "192.168.1.100", "13812345678"):
        assert leaked_value not in row["stats_json"]


def test_to_conversation_row_hashes_customer_id_when_token_absent():
    """customer_token 本租户实测为空，回退到 customer_id；两者皆无才落 NULL。"""
    assert (
        to_conversation_row(_session(1, customer_id=77777))["customer_token_hash"]
        == (to_conversation_row(_session(1, customer_id=77777))["customer_token_hash"])
    )
    assert to_conversation_row(_session(1))["customer_token_hash"] is None


def test_to_message_rows_extracts_inner_content_and_maps_roles():
    logs = [
        _log_item("m1", "customer", "如何开发票？", "2026-09-14 20:01:00"),
        _log_item("m2", "agent", "您把手机号 13812345678 发我", "2026-09-14 20:02:00"),
        # 控制报文：没有真人说话人，落 system，否则会被 has_substantive_qa 当成客服答复
        _log_item("m3", "agent", "会话已结束", "2026-09-14 20:03:00", kind="close"),
        # 系统欢迎语同理
        _log_item("m4", "customer", "您好，很高兴为您服务", "2026-09-14 20:00:00", welcome=True),
    ]
    rows = to_message_rows("c1", logs)

    assert [row["role"] for row in rows] == ["customer", "agent", "system", "system"]
    assert rows[0]["content"] == "如何开发票？"  # 取内层 data.content，而非外层 JSON 信封
    assert "{" not in rows[0]["content"]
    assert "138****78" in rows[1]["content"]  # 正文脱敏
    assert rows[2]["content"] == "会话已结束"
    assert rows[0]["content_type"] == "message" and rows[2]["content_type"] == "close"
    assert rows[0]["sent_at"] == NOW + timedelta(minutes=1)


def test_to_message_rows_synthesizes_stable_id_and_keeps_broken_envelope():
    logs = [
        {"sender": "customer", "content": "纯文本正文", "created_at": "2026-09-14 20:00:00"},
        {"sender": "agent", "content": "{坏 JSON", "created_at": "2026-09-14 20:00:01"},
    ]
    rows = to_message_rows("c1", logs)

    assert rows[0]["message_id"] == "c1-2026-09-14 20:00:00-0"  # 无服务端 ID 时合成稳定 ID
    assert to_message_rows("c1", logs)[0]["message_id"] == rows[0]["message_id"]  # 重放稳定
    # 说话人取日志外层的 sender（与 content 兄弟字段），信封解析失败也不该丢话路身份
    assert rows[0]["role"] == "customer" and rows[0]["content"] == "纯文本正文"
    assert rows[0]["content_type"] is None
    assert rows[1]["role"] == "agent" and rows[1]["content"] == "{坏 JSON"


# --------------------------------------------------------------- 服务流程
class FakeResult:
    def __init__(self, scalar=None, rowcount=None, rows=()):
        self._scalar = scalar
        self._rowcount = rowcount
        self._rows = rows

    def scalar(self):
        return self._scalar

    def all(self):
        return self._rows

    @property
    def rowcount(self):
        return self._rowcount


class FakeSession:
    """按语句种类分派的测试替身。

    进度上报是纯记账、返回值没有用例依赖，故就地返回、不占用 results 队列——
    否则每加一处记账，所有用例排好的结果队列都要跟着重排。results 只留给
    返回值被断言的语句（SELECT / INSERT / 抢租约）。
    """

    def __init__(self, results):
        self.results = list(results)
        self.executed: list[tuple[str, dict | None]] = []
        self.commits = 0
        self.rollbacks = 0

    async def execute(self, statement, params=None):
        sql = str(statement)
        self.executed.append((sql, params))
        if "progress_done = progress_done" in sql:  # 进度上报
            return FakeResult(rowcount=1)
        result = self.results.pop(0) if self.results else FakeResult()
        return result() if callable(result) else result

    async def commit(self):
        self.commits += 1
        self.executed.append(("<COMMIT>", None))

    async def rollback(self):
        self.rollbacks += 1
        self.executed.append(("<ROLLBACK>", None))

    def sqls(self, needle):
        return [(sql, params) for sql, params in self.executed if needle in sql]


class _SessionContext:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *exc):
        return False


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: list[tuple[str, str, dict | None]] = []

    async def get(self, path, *, query=None):
        self.calls.append(("GET", path, query))
        return self._next()

    def _next(self):
        item = self.responses.pop(0)
        return item() if callable(item) else item

    def gets(self, path=None):
        return [(p, q) for method, p, q in self.calls if method == "GET" and (path is None or p == path)]


def make_service(client, session, **kwargs):
    factory = lambda: _SessionContext(session)  # noqa: E731
    return UdeskPullService(client, factory, now=lambda: NOW, **kwargs)


async def test_run_once_success_upserts_and_advances_watermark():
    session = FakeSession(
        [
            FakeResult(rowcount=1),  # 抢租约
            FakeResult(scalar=None),  # 无游标 → 首次拉取
            FakeResult(scalar=True),  # upsert 会话：真新增
            FakeResult(rows=[("m1",)]),  # 消息新增 1 条（RETURNING 的真实命中数）
            FakeResult(),  # 推进游标
        ]
    )
    client = FakeClient(
        [
            _page([_session(99, "2026-09-14 19:00:00")]),
            _page([_log_item("m1", "customer", "如何开发票？", "2026-09-14 19:01:00")]),
            _page(),  # 按结束时刻的第二遍：本窗无新结束的会话
        ]
    )
    result = await make_service(client, session, backfill_start_days=1).run_once()

    assert result["status"] == "succeeded"
    assert result["conversations"] == 1 and result["messages"] == 1
    # D17：会话 upsert / 消息 DO NOTHING 都走 ON CONFLICT，禁止先查后插
    assert any("ON CONFLICT (conversation_id)" in sql for sql, _ in session.executed)
    assert any("ON CONFLICT (message_id) DO NOTHING" in sql for sql, _ in session.executed)
    # D13：成功后推进游标到窗口终点（now）
    advances = session.sqls("watermark = :until")
    assert len(advances) == 1 and advances[0][1]["until"] == NOW
    assert any("last_run_status" in sql for sql, _ in session.executed)  # 结果回写
    assert session.rollbacks == 0

    searches = [q for _, q in client.gets(SESSIONS_PATH)]
    assert searches[0]["start_time"] == to_beijing_string(NOW - timedelta(days=1))
    assert searches[0]["end_time"] == to_beijing_string(NOW)
    assert searches[0]["page_size"] == PAGE_SIZE and "status" not in searches[0]
    # 日志窗口独立于外层窗口：用会话自身的创建时刻起点，才拉得全（见模块 docstring 推论①）
    log_query = client.gets(LOGS_PATH)[0][1]
    assert log_query["session_id"] == "99"
    assert log_query["start_time"] == to_beijing_string(NOW - timedelta(hours=1))


async def test_run_once_skips_when_lease_held():
    session = FakeSession([FakeResult(rowcount=0)])  # 租约被占
    client = FakeClient([])

    result = await make_service(client, session).run_once()

    assert result == {"status": "skipped_lease"}
    assert client.calls == []  # 未发起任何接口调用
    # 被租约挡下也要留痕：点了「立即拉取」却什么都没发生，页面无从判断是没跑还是被挡了
    outcomes = session.sqls("last_run_status = 'skipped_lease'")
    assert len(outcomes) == 1
    # 但只在没人持租约时才写：抢不到租约恰恰说明正有一轮在跑，那行归它所有——
    # 覆盖成 skipped_lease 会让页面在拉取进行中显示成「跳过」，极端时序下还会盖掉
    # 它写下的成功结论。计数另有 COALESCE 保护，这里保的是状态不被篡改。
    assert "lease_expires_at IS NULL OR lease_expires_at <= :now" in outcomes[0][0]


async def test_run_once_failure_rolls_back_and_keeps_watermark():
    session = FakeSession(
        [
            FakeResult(rowcount=1),
            FakeResult(scalar=NOW - timedelta(hours=2)),  # 既有游标
        ]
    )
    client = FakeClient([_fail("接口不可用")])

    result = await make_service(client, session).run_once()

    assert result["status"] == "failed" and "接口不可用" in result["error"]
    assert session.rollbacks == 1
    # D8：失败绝不推进游标
    assert session.sqls("watermark = :until") == []
    # 失败结论也要落库，且租约最终释放
    assert any("last_run_status" in sql for sql, _ in session.executed)
    assert session.sqls("lease_expires_at = NULL")


async def test_run_once_starts_from_watermark_minus_overlap():
    watermark = NOW - timedelta(hours=2)
    session = FakeSession([FakeResult(rowcount=1), FakeResult(scalar=watermark)])
    client = FakeClient([_page(), _page()])

    result = await make_service(client, session, overlap_minutes=10).run_once()

    assert result["status"] == "succeeded"
    # D14：重叠窗起点 = 游标 - overlap，边界漏读交给唯一键吸收
    searches = [q for _, q in client.gets(SESSIONS_PATH)]
    assert searches[0]["start_time"] == to_beijing_string(watermark - timedelta(minutes=10))
    assert searches[0]["end_time"] == to_beijing_string(NOW)


async def test_close_pass_learns_closed_at_for_sessions_ended_in_window():
    """列表只按创建时刻归属，closed_at 只能从 status=close 的第二遍拿到。

    第二遍按结束时刻过滤，会带回创建于一个月前的会话（实测同窗 47 条 vs 42 条），
    这正是 ended_at 不至于长期为 NULL 的唯一入口。
    """
    session = FakeSession([FakeResult(rowcount=1), FakeResult(scalar=None)])
    client = FakeClient(
        [
            _page([_session(1, "2026-09-14 19:00:00")]),
            _page(),
            _page([_session(1, "2026-09-10 08:00:00", closed_at="2026-09-14 19:30:00")]),
            _page(),
        ]
    )

    result = await make_service(client, session, backfill_start_days=1).run_once()

    assert result["status"] == "succeeded"
    # 同一会话被再 upsert 一次，这次带上 closed_at
    upserts = session.sqls("INSERT INTO udesk_conversations")
    assert [params["ended_at"] for _, params in upserts] == [None, NOW - timedelta(minutes=30)]
    assert [q.get("status") for _, q in client.gets(SESSIONS_PATH)] == [None, "close"]


async def test_second_pass_does_not_double_count_conversations():
    """同一会话在本窗「创建」与「结束」两遍遍历里各出现一次，只能计一次新增。

    按处理次数计就会出现「本轮处理会话 89 条」而累计只有 58 条的假象——实测两遍
    分别 42 / 47 条，重叠 31 条，差额全在这里。会话的新增口径必须与「新增消息」一致，
    否则同一句话里的两个数字各说各话。
    """
    session = FakeSession(
        [
            FakeResult(rowcount=1),
            FakeResult(scalar=None),
            FakeResult(scalar=True),  # 第一遍：新插入
            FakeResult(scalar=False),  # 第二遍：同一会话，走 DO UPDATE
        ]
    )
    client = FakeClient(
        [
            _page([_session(1, "2026-09-14 19:00:00")]),
            _page(),
            _page([_session(1, "2026-09-14 19:00:00", closed_at="2026-09-14 19:30:00")]),
            _page(),
        ]
    )

    result = await make_service(client, session, backfill_start_days=1).run_once()

    assert result["conversations"] == 1  # 不是 2
    # 新增与否只能靠 RETURNING (xmax = 0) 判定：DO UPDATE 的 rowcount 把新增与更新都算 1
    upserts = session.sqls("INSERT INTO udesk_conversations")
    assert len(upserts) == 2 and "RETURNING (xmax = 0)" in upserts[0][0]


async def test_search_paginates_by_server_total_pages():
    """翻页判停以服务端 total_pages 为准：自算（page × page_size ≥ total）会因
    page_size 被静默截断而提前退出漏数据。"""
    session = FakeSession(
        [
            FakeResult(rowcount=1),
            FakeResult(scalar=NOW - timedelta(hours=1)),
            FakeResult(scalar=True),  # 会话 1 真新增
            FakeResult(scalar=True),  # 会话 2 真新增
        ]
    )
    client = FakeClient(
        [
            _page([_session(1, "2026-09-14 11:30:00")], total=150, total_pages=2),
            _page(),
            _page([_session(2, "2026-09-14 11:40:00")], total=150, total_pages=2),
            _page(),
            _page(),  # 按结束时刻的第二遍
        ]
    )

    result = await make_service(client, session).run_once()

    assert result["status"] == "succeeded" and result["conversations"] == 2
    created_pass = [q["page"] for _, q in client.gets(SESSIONS_PATH) if "status" not in q]
    assert created_pass == [1, 2]  # total=150 < page_size=1000，自算会在第 1 页停下


async def test_backfill_start_splits_into_monthly_windows():
    session = FakeSession([FakeResult(rowcount=1), FakeResult(scalar=None)])  # 首次拉取
    empty = _page()
    client = FakeClient([empty, empty, empty, empty])  # 两窗 × 两遍

    result = await make_service(client, session, backfill_start_days=40).run_once()

    assert result["status"] == "succeeded"
    # 单窗跨度一律受 API_HISTORY_DAYS 约束（与配置层的回灌夹取是两道独立防线）
    advances = [params["until"] for _, params in session.sqls("watermark = :until")]
    assert advances == [NOW - timedelta(days=10), NOW]
    starts = [q["start_time"] for _, q in client.gets(SESSIONS_PATH)]
    assert starts[0] == to_beijing_string(NOW - timedelta(days=40))
    assert starts[2] == to_beijing_string(NOW - timedelta(days=10))


async def test_pull_messages_sends_required_window_and_paginates_by_total_pages():
    session = FakeSession([FakeResult(rows=[("m1",), ("m2",)])])  # 两条都真落库
    client = FakeClient(
        [
            _page([_log_item("m1", "customer", "问", "2026-09-14 10:00:00")], total=2, total_pages=2),
            _page([_log_item("m2", "agent", "答", "2026-09-14 10:01:00")], total=2, total_pages=2),
        ]
    )
    service = make_service(client, session)

    count = await service._pull_messages(session, "c1", NOW - timedelta(days=2))

    assert count == 2
    queries = [q for _, q in client.gets(LOGS_PATH)]
    # 时间窗是必填的：缺窗会被静默过滤成空（total=0 且不报错），故必须显式带上
    assert [q["page"] for q in queries] == [1, 2]
    assert queries[0]["session_id"] == "c1"
    assert queries[0]["start_time"] == to_beijing_string(NOW - timedelta(days=2))
    assert queries[0]["end_time"] == to_beijing_string(NOW)
    assert queries[0]["page_size"] == PAGE_SIZE
    # 收齐所有页后一次批量写入（只有一条 INSERT 语句，却带回两条新增）
    assert len(session.sqls("INSERT INTO udesk_messages")) == 1


async def test_pull_messages_reports_real_inserts_not_attempts():
    """返回值必须是**真实新增**数：全是被唯一键吸收的重复时应为 0。

    这正是一轮「会话 0 条 / 消息 59 条」的来源——59 条尝试写入全被 message_id
    去重吸收，新增其实是 0。故写入必须 RETURNING（裸 text() 认不出 RETURNING，
    去掉 RETURNING 后 asyncpg 对 executemany 的 rowcount 一律给 -1）。
    """
    session = FakeSession([FakeResult(rows=[]), FakeResult(rows=[("m3",)])])
    client = FakeClient(
        [
            _page([_log_item("m1", "customer", "重复的旧消息", "2026-09-14 10:00:00")]),
            _page([_log_item("m3", "agent", "本轮的新消息", "2026-09-14 10:01:00")]),
        ]
    )
    service = make_service(client, session)

    first = await service._pull_messages(session, "c1", NOW - timedelta(days=2))
    second = await service._pull_messages(session, "c1", NOW - timedelta(days=2))

    assert first == 0  # 全部被唯一键吸收
    assert second == 1  # 只有真正落库的那条算新增
    insert_sql = session.sqls("INSERT INTO udesk_messages")[0][0]
    assert "ON CONFLICT (message_id) DO NOTHING" in insert_sql and "RETURNING" in insert_sql


async def test_pull_messages_clamps_window_start_to_api_visible_history():
    """日志接口与列表同样只提供一个月内的数据。按结束时刻的第二遍会带回创建于一个月前的
    会话，其 created_at→now 跨度超限会让请求报 status=2000 并中断整轮，故起点必须夹取。"""
    session = FakeSession([])
    client = FakeClient([_page()])
    service = make_service(client, session)

    await service._pull_messages(session, "c1", NOW - timedelta(days=45))

    assert client.gets(LOGS_PATH)[0][1]["start_time"] == to_beijing_string(NOW - timedelta(days=API_HISTORY_DAYS))


async def test_run_once_repulls_tail_of_unclosed_recent_conversations():
    """列表不再带回已发现的会话，未结束会话在发现之后产生的消息只能靠尾巴回补补齐。"""
    session = FakeSession(
        [
            FakeResult(rowcount=1),
            FakeResult(scalar=None),
            FakeResult(scalar=True),  # upsert 会话：真新增
            FakeResult(rows=[("m1",)]),  # 首拉消息：新增 1 条
            FakeResult(),  # 推进游标
            FakeResult(rows=[("c1", NOW - timedelta(days=3))]),  # 尾巴回补候选
            FakeResult(rows=[("m2",)]),  # 尾巴补上的新消息
        ]
    )
    client = FakeClient(
        [
            _page([_session("c1", "2026-09-14 09:00:00")]),
            _page([_log_item("m1", "customer", "问", "2026-09-14 09:00:00")]),
            _page(),
            _page([_log_item("m2", "agent", "答", "2026-09-14 11:30:00")]),  # 尾巴补上的新消息
        ]
    )

    result = await make_service(client, session, backfill_start_days=1).run_once()

    assert result["status"] == "succeeded" and result["messages"] == 2
    tail = session.sqls("ended_at IS NULL")
    assert len(tail) == 1
    # 追踪窗口与接口数据视野一致（一个月），会话静默后自然停止追踪
    assert tail[0][1]["cutoff"] == NOW - timedelta(days=API_HISTORY_DAYS)
    # 同一会话被拉了两次日志：发现时一次、尾巴回补一次；幂等由 message_id 唯一键吸收
    assert [q["session_id"] for _, q in client.gets(LOGS_PATH)] == ["c1", "c1"]


async def test_acquire_lease_sets_ttl_and_running_status():
    session = FakeSession([FakeResult(rowcount=1), FakeResult(scalar=None)])
    client = FakeClient([_page(), _page()])

    result = await make_service(client, session, backfill_start_days=1).run_once()

    assert result["status"] == "succeeded"
    acquire_sqls = session.sqls("last_run_status = 'running'")
    assert len(acquire_sqls) == 1
    assert acquire_sqls[0][1]["lease_until"] == NOW + timedelta(minutes=LEASE_MINUTES)
    # 抢到租约即把上一轮进度清零，否则新的一轮会从旧进度接着涨
    assert "progress_done = 0" in acquire_sqls[0][0] and "progress_total = 0" in acquire_sqls[0][0]


async def test_run_once_reports_progress_per_conversation():
    """一轮拉取长达数分钟，进度必须逐会话落库并提交，页面才看得到在动。

    提交是必须的：未提交的 UPDATE 对并发读不可见，状态接口照样只看见 running。
    """
    session = FakeSession(
        [
            FakeResult(rowcount=1),
            FakeResult(scalar=None),
            FakeResult(scalar=True),  # upsert 会话 1：真新增
            FakeResult(rows=[("m1",)]),  # 会话 1 的消息真实新增 1 条
            FakeResult(scalar=True),  # upsert 会话 2：真新增
            FakeResult(),  # 推进游标
        ]
    )
    client = FakeClient(
        [
            _page([_session(1, "2026-09-14 19:00:00"), _session(2, "2026-09-14 19:10:00")]),
            _page([_log_item("m1", "customer", "问题一", "2026-09-14 19:01:00")]),
            _page(),  # 会话 2 无消息
            _page(),  # 按结束时刻的第二遍：本窗无新结束的会话
        ]
    )

    result = await make_service(client, session, backfill_start_days=1).run_once()

    assert result["status"] == "succeeded"
    reports = session.sqls("progress_done = progress_done")
    # 两个会话各上报一次，且每次上报都紧跟着一次提交
    assert len(reports) == 2
    assert [params["done_delta"] for _, params in reports] == [1, 1]
    assert [params["total_delta"] for _, params in reports] == [1, 1]
    executed = [sql for sql, _ in session.executed]
    for index, sql in enumerate(executed):
        if "progress_done = progress_done" in sql:
            assert executed[index + 1] == "<COMMIT>"


async def test_failure_keeps_previous_counts_instead_of_zeroing_them():
    """失败回写状态与原因，但不能把上一轮已提交的成果抹成 0——
    否则页面在出错时显示得比成功时还「干净」，反而看不出到底跑没跑过。
    """
    session = FakeSession([FakeResult(rowcount=1), FakeResult(scalar=NOW - timedelta(hours=2))])
    client = FakeClient([_fail("接口不可用")])

    result = await make_service(client, session).run_once()

    assert result["status"] == "failed"
    outcome = session.sqls("last_run_status = :status")[0]
    assert outcome[1]["status"] == "failed" and "接口不可用" in outcome[1]["error"]
    # 计数位传 None，交给 SQL 里的 COALESCE 保留原值
    assert outcome[1]["conversations"] is None and outcome[1]["messages"] is None
    assert "COALESCE" in outcome[0]


async def test_lease_committed_before_any_pull_work():
    # 未提交的租约对并发不可见：抢到后必须先 COMMIT 再开始拉取
    session = FakeSession([FakeResult(rowcount=1), FakeResult(scalar=None)])
    client = FakeClient([_page(), _page()])

    result = await make_service(client, session, backfill_start_days=1).run_once()

    assert result["status"] == "succeeded"
    sqls = [sql for sql, _ in session.executed]
    assert "lease_expires_at = :lease_until" in sqls[0]
    assert sqls[1] == "<COMMIT>"


async def test_reconcile_repulls_gap_window():
    # 远端当日 2 条、本地 1 条 → 判缺口并按当日窗口回补重拉
    session = FakeSession([FakeResult(scalar=1)])  # 本地 count(*) = 1
    client = FakeClient(
        [
            _page(total=2),  # 当日 total
            _page([_session(7, "2026-09-14 08:00:00")], total=2, total_pages=1),
            _page(),
        ]
    )

    result = await make_service(client, session).reconcile_recent(days=1)

    assert result == {"checked_days": 1, "gaps": 1}
    assert any("count(*) FROM udesk_conversations" in sql for sql, _ in session.executed)
    assert any("ON CONFLICT (conversation_id)" in sql for sql, _ in session.executed)  # 已回补入库
    # 对账日界按北京日切：NOW=北京 20:00 → 当日起点为北京 00:00
    day_start_beijing = "2026-09-14 00:00:00"
    queries = [q for _, q in client.gets(SESSIONS_PATH)]
    assert queries[0]["start_time"] == day_start_beijing
    assert queries[1]["start_time"] == day_start_beijing  # 回补窗口同为当日
