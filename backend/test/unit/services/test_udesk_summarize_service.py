"""Udesk 客服记录 LLM 结构化（筛选/校验/幂等入库/打标）单元测试。"""

from __future__ import annotations

import json

from sqlalchemy import Insert, Select, Update
from sqlalchemy.dialects import postgresql
from sqlalchemy.sql.elements import TextClause

from yuxi.repositories.curated_qa_repository import hash_qa_question, normalize_qa_question
from yuxi.services.udesk.summarize_service import (
    UnparsableModelOutputError,
    UdeskSummarizeService,
    build_prompt,
    extract_candidates,
    has_substantive_qa,
    validate_candidate,
)

DOMAINS = ["kefu", "terminal"]
PG = postgresql.dialect()

SUBSTANTIVE_MESSAGES = [
    {"role": "customer", "content": "请问终端 F10-M 的质保期是多久？"},
    {"role": "agent", "content": "F10-M 整机质保 2 年，附件质保 1 年。"},
]


# ----------------------------------------------------------------- 纯函数
def test_has_substantive_qa_filters_greetings_and_unanswered():
    assert has_substantive_qa(SUBSTANTIVE_MESSAGES) is True
    # 纯寒暄：双方消息都过短
    greeting = [{"role": "customer", "content": "你好"}, {"role": "agent", "content": "您好"}]
    assert has_substantive_qa(greeting) is False
    # 未答复：客户有实质提问、客服无实质答复
    unanswered = [{"role": "customer", "content": "如何开发票？"}, {"role": "agent", "content": "好的"}]
    assert has_substantive_qa(unanswered) is False


def test_extract_candidates_tolerates_fences_and_rejects_garbage():
    plain = '{"candidates": [{"question": "q"}]}'
    fenced = "```json\n" + plain + "\n```"
    assert extract_candidates(fenced) == [{"question": "q"}]
    assert extract_candidates('[{"question": "q"}]') == [{"question": "q"}]  # 容忍裸数组
    assert extract_candidates('{"candidates": ["x", {"question": "q"}]}') == [{"question": "q"}]  # 非法项剔除

    # 异常类型是契约：run_batch 靠它把「确定性失败」与「瞬时失败」分开——
    # 前者打标跳过，后者下轮重试。退化成普通 ValueError 会静默恢复成无限重试。
    for garbage in ("不是 JSON", "{}", '{"candidates": 1}', ""):
        try:
            extract_candidates(garbage)
        except UnparsableModelOutputError:
            continue
        raise AssertionError(f"应拒绝无效输出: {garbage!r}")


def test_build_prompt_requires_chinese_output_but_verbatim_evidence():
    """语言规则必须钉住：prompt 不提语言时模型会跟随会话原文，英文会话就产出英文候选。

    evidence_quote 是刻意的例外——校验层做的是「逐字子串匹配」，译文会被静默判为不合规，
    整条候选无声丢弃，现象和「模型不产出」一模一样，极难排查。两条规则必须同在。
    """
    system = build_prompt(
        {"conversation_id": "c1"}, SUBSTANTIVE_MESSAGES, known_domains=DOMAINS, max_pairs=5
    )[0]["content"]

    assert "简体中文" in system
    assert "不得翻译" in system
    assert "逐字摘抄" in system


def _candidate(**overrides):
    item = {
        "question": "F10-M 的质保期是多久？",
        "answer": "F10-M 整机质保 2 年，附件质保 1 年。",
        "evidence_quote": "F10-M 整机质保 2 年，附件质保 1 年。",
        "domain": "terminal",
        "confidence": 0.9,
        "ambiguity_note": None,
    }
    item.update(overrides)
    return item


def test_validate_candidate_happy_path_computes_hash():
    row = validate_candidate(_candidate(), SUBSTANTIVE_MESSAGES, known_domains=DOMAINS, conversation_id="c1")

    assert row is not None
    assert row["question_hash"] == hash_qa_question(normalize_qa_question(row["question"]))
    assert row["domain"] == "terminal"
    assert row["confidence"] == 0.9
    # 来源会话是候选表的 NOT NULL 列，必须由校验层带上；漏掉它每条候选都插不进去
    assert row["source_conversation_id"] == "c1"


def test_validate_candidate_drops_evidence_not_in_transcript():
    # evidence 必须逐字摘自会话记录（忽略空白差异），编造即丢弃
    assert (
        validate_candidate(
            _candidate(evidence_quote="F10-M 整机质保 3 年", answer="整机质保 3 年。"),
            SUBSTANTIVE_MESSAGES,
            known_domains=DOMAINS,
            conversation_id="c1",
        )
        is None
    )
    assert (
        validate_candidate(
            _candidate(evidence_quote="  "), SUBSTANTIVE_MESSAGES, known_domains=DOMAINS, conversation_id="c1"
        )
        is None
    )
    # 空白差异不影响命中（客服原话带换行）
    row = validate_candidate(
        _candidate(evidence_quote="F10-M 整机质保 2 年，\n附件质保 1 年。"),
        SUBSTANTIVE_MESSAGES,
        known_domains=DOMAINS,
        conversation_id="c1",
    )
    assert row is not None


def test_validate_candidate_drops_invented_facts():
    # 答案中的数字/链接必须能在会话记录中找到原文（C7：不得改写事实）
    assert (
        validate_candidate(
            _candidate(answer="整机质保 3 年。"), SUBSTANTIVE_MESSAGES, known_domains=DOMAINS, conversation_id="c1"
        )
        is None
    )
    assert (
        validate_candidate(
            _candidate(answer="详见 https://example.com/warranty"),
            SUBSTANTIVE_MESSAGES,
            known_domains=DOMAINS,
            conversation_id="c1",
        )
        is None
    )


def test_validate_candidate_drops_overlength_and_normalizes_optional_fields():
    assert (
        validate_candidate(
            _candidate(question="长" * 301), SUBSTANTIVE_MESSAGES, known_domains=DOMAINS, conversation_id="c1"
        )
        is None
    )
    assert (
        validate_candidate(_candidate(answer=""), SUBSTANTIVE_MESSAGES, known_domains=DOMAINS, conversation_id="c1")
        is None
    )

    row = validate_candidate(
        _candidate(domain="不存在的域", confidence="high"),
        SUBSTANTIVE_MESSAGES,
        known_domains=DOMAINS,
        conversation_id="c1",
    )
    # 未知业务域/非法置信度归 None，候选保留
    assert row is not None and row["domain"] is None and row["confidence"] is None


# ----------------------------------------------------------------- 服务流程
class FakeResult:
    def __init__(self, rows=None, rowcount=None):
        self._rows = rows if rows is not None else []
        self._rowcount = rowcount

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)

    @property
    def rowcount(self):
        return self._rowcount


class FakeSession:
    """按语句类型分发脚本结果；候选 INSERT 记录编译参数供断言。

    待总结会话以 (主键, conversation_id) 元组下发——服务侧刻意不取 ORM 实体，
    rollback 会让 session 内的 ORM 对象全部过期，之后读任何属性都会抛
    MissingGreenlet（见 test_run_batch_continues_after_one_conversation_fails）。
    """

    def __init__(self, *, conversations=(), messages=None, existing_hashes=(), insert_rowcount=1, lease_free=True):
        self.conversations = list(conversations)
        self.messages = dict(messages or {})
        self.existing_hashes = list(existing_hashes)
        self.insert_rowcount = insert_rowcount
        self.lease_free = lease_free
        self.executed: list[tuple[str, dict]] = []
        self.insert_params: dict | None = None

    async def execute(self, statement, params=None):
        compiled = statement.compile(dialect=PG)
        # 记录语句原文（而非 dialect 编译结果）：编译结果会把 :param 渲染成
        # %(param)s，按原名断言就全落空。compile 只用来取编译后的绑定参数名。
        sql = str(statement)
        # text() 的编译参数只有占位符、没有运行时取值，故优先记录调用方传入的 params
        self.executed.append((sql, dict(params) if params else dict(compiled.params)))
        if isinstance(statement, TextClause):
            if "summarize_lease_expires_at = :lease_until" in sql:  # 抢租约
                return FakeResult(rowcount=1 if self.lease_free else 0)
            if "summarize_lease_expires_at = NULL" in sql:  # 释放租约
                return FakeResult(rowcount=1)
            if "summarize_status = :status" in sql:  # 回写结论
                return FakeResult(rowcount=1)
            return FakeResult(rows=[(h,) for h in self.existing_hashes])  # 已入库问答对哈希
        if isinstance(statement, Update):  # summarized_at 打标
            return FakeResult(rowcount=1)
        if isinstance(statement, Insert):  # 候选幂等写入
            self.insert_params = dict(compiled.params)
            return FakeResult(rowcount=self.insert_rowcount)
        if isinstance(statement, Select):
            if "FROM udesk_conversations" in sql:
                return FakeResult(rows=self.conversations)
            if "FROM udesk_messages" in sql:
                bound = [v for k, v in compiled.params.items() if k.startswith("conversation_id")]
                return FakeResult(rows=self.messages.get(bound[0], []))
        raise AssertionError(f"未预期的语句: {sql[:200]}")

    async def commit(self):
        self.executed.append(("<COMMIT>", {}))

    async def rollback(self):
        self.executed.append(("<ROLLBACK>", {}))

    def sqls(self, needle):
        return [sql for sql, _ in self.executed if needle in sql]


def outcome_params(session):
    """回写状态表那条 UPDATE 的绑定参数。"""
    return next(params for sql, params in session.executed if "summarize_status = :status" in sql)


class _SessionContext:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *exc):
        return False


def make_generate(responses):
    calls: list[list[dict]] = []

    async def generate(prompt):
        calls.append(prompt)
        item = responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    generate.calls = calls  # type: ignore[attr-defined]
    return generate


def make_service(session, generate, **kwargs):
    return UdeskSummarizeService(
        lambda: _SessionContext(session),
        generate=generate,
        known_domains=DOMAINS,
        **kwargs,
    )


def conversation(conv_id="c1", pk=1):
    """待总结会话：(主键, conversation_id)。服务侧刻意不取 ORM 实体，见 FakeSession。"""
    return (pk, conv_id)


def message(role, content):
    """会话消息：(role, content)。同样只取这两列，不取实体。"""
    return (role, content)


async def test_run_batch_marks_filtered_conversation_without_llm():
    session = FakeSession(
        conversations=[conversation()],
        messages={"c1": [message("customer", "你好"), message("agent", "您好，请问有什么可以帮您？")]},
    )
    # 第二条客服消息 14 字 ≥5，但客户侧无实质提问 → 整体筛掉
    generate = make_generate([])

    totals = await make_service(session, generate).run_batch(limit=10)

    assert totals == {"status": "succeeded", "processed": 1, "candidates": 0, "skipped_filtered": 1, "failed": 0}
    assert generate.calls == []  # 未送 LLM
    assert session.sqls("UPDATE udesk_conversations")
    assert session.sqls("<COMMIT>")
    # 0 与「没跑过」在页面上是两句话，跑到但没产出必须落 0 而不是 NULL
    assert outcome_params(session)["candidates"] == 0


async def test_run_batch_inserts_candidates_and_marks_summarized():
    session = FakeSession(
        conversations=[conversation()],
        messages={"c1": [message(m["role"], m["content"]) for m in SUBSTANTIVE_MESSAGES]},
        insert_rowcount=1,
    )
    raw = json.dumps(
        {
            "candidates": [
                {
                    "question": "F10-M 的质保期是多久？",
                    "answer": "整机质保 2 年。",
                    "evidence_quote": "整机质保 2 年",
                    "domain": "terminal",
                    "confidence": 0.9,
                }
            ]
        },
        ensure_ascii=False,
    )
    generate = make_generate([raw])

    totals = await make_service(session, generate).run_batch(limit=10)

    assert totals == {"status": "succeeded", "processed": 1, "candidates": 1, "skipped_filtered": 0, "failed": 0}
    insert_sql = session.sqls("curated_qa_candidates")[0]
    assert "ON CONFLICT" in insert_sql and "(source_conversation_id, question_hash)" in insert_sql
    assert session.insert_params is not None
    expected_hash = hash_qa_question(normalize_qa_question("F10-M 的质保期是多久？"))
    assert expected_hash in session.insert_params.values()  # 多行 insert 参数名带行号后缀，按值断言
    assert "unique" in session.insert_params.values()
    assert "c1" in session.insert_params.values()  # 来源会话随行落库，不再是 NULL
    assert session.sqls("UPDATE udesk_conversations") and session.sqls("<COMMIT>")
    # 本轮真实新增条数落状态表，页面才显示得出「新生成 N 条候选问答对」
    assert outcome_params(session)["candidates"] == 1
    # 提示词带业务域代码与消息内容
    system = generate.calls[0][0]["content"]
    user = generate.calls[0][1]["content"]
    assert "terminal" in system and "禁止改动事实" in system
    assert "F10-M" in user


async def test_run_batch_marks_duplicate_when_hash_exists():
    target_hash = hash_qa_question(normalize_qa_question("F10-M 的质保期是多久？"))
    session = FakeSession(
        conversations=[conversation()],
        messages={"c1": [message(m["role"], m["content"]) for m in SUBSTANTIVE_MESSAGES]},
        existing_hashes=[target_hash],
    )
    raw = json.dumps(
        {
            "candidates": [
                {
                    "question": "F10-M 的质保期是多久？",
                    "answer": "整机质保 2 年。",
                    "evidence_quote": "整机质保 2 年",
                }
            ]
        },
        ensure_ascii=False,
    )
    generate = make_generate([raw])

    totals = await make_service(session, generate).run_batch(limit=10)

    assert "duplicate" in session.insert_params.values()
    assert totals["candidates"] == 1


async def test_run_batch_llm_failure_leaves_unsummarized_for_retry():
    session = FakeSession(
        conversations=[conversation()],
        messages={"c1": [message(m["role"], m["content"]) for m in SUBSTANTIVE_MESSAGES]},
    )
    generate = make_generate([RuntimeError("model down")])

    totals = await make_service(session, generate).run_batch(limit=10)

    assert totals == {"status": "failed", "processed": 1, "candidates": 0, "skipped_filtered": 0, "failed": 1}
    assert not session.sqls("UPDATE udesk_conversations")  # 未打标，下轮重试
    assert not session.sqls("curated_qa_candidates")
    assert session.sqls("<ROLLBACK>")


async def test_run_batch_marks_conversation_when_model_output_unparsable():
    """输出解析不了是确定性失败，必须打标——与瞬时失败的处理相反。

    token 在模型答完的那一刻就花掉了，输出再解析不了等于白花。若照旧不打标，
    这条会话会按 started_at 排在队首，每小时 cron 拿同一份输入反复烧一遍，
    占满批次额度后新会话再也进不来（页面表现为「总结一直在跑但没有新候选」）。
    """
    session = FakeSession(
        conversations=[conversation()],
        messages={"c1": [message(m["role"], m["content"]) for m in SUBSTANTIVE_MESSAGES]},
    )
    # 模型带前后缀输出（真实里最常见的形态）：不是纯 JSON，解析必然失败
    generate = make_generate(['好的，整理结果如下：\n{"candidates": []}'])

    totals = await make_service(session, generate).run_batch(limit=10)

    assert totals == {"status": "failed", "processed": 1, "candidates": 0, "skipped_filtered": 0, "failed": 1}
    assert session.sqls("<ROLLBACK>")
    assert session.sqls("UPDATE udesk_conversations")  # 打标，不再重试
    assert session.sqls("<COMMIT>")


async def test_run_batch_invalid_candidates_still_marks_summarized():
    session = FakeSession(
        conversations=[conversation()],
        messages={"c1": [message(m["role"], m["content"]) for m in SUBSTANTIVE_MESSAGES]},
    )
    # evidence 全部编造 → 全部丢弃；会话已处理完成，正常打标避免反复送 LLM
    raw = '{"candidates": [{"question": "q1", "answer": "整机质保 3 年。", "evidence_quote": "不存在的原话"}]}'
    generate = make_generate([raw])

    totals = await make_service(session, generate).run_batch(limit=10)

    assert totals == {"status": "succeeded", "processed": 1, "candidates": 0, "skipped_filtered": 0, "failed": 0}
    assert not session.sqls("curated_qa_candidates")
    assert session.sqls("UPDATE udesk_conversations") and session.sqls("<COMMIT>")


async def test_run_batch_continues_after_one_conversation_fails():
    """单会话失败不得拖垮整批，失败原因必须留痕。

    现场是：except 里先 rollback，紧接着日志 f-string 又去读
    conversation.conversation_id——rollback 已让 session 内的 ORM 对象全部过期，
    这次读取触发同步惰性加载抛 MissingGreenlet，顶掉了真实原因，整批从下一个
    会话起全数死掉，于是「候选一条都没生成」。修法是循环里只经手普通元组，
    不在 rollback 之后再碰 ORM 实体。

    这里断言的是可观测结果：第二个会话照常产出候选、真实原因照常落库、租约照常释放。
    替身下发的会话本身就是元组，故若把实现改回取 ORM 实体，本用例会当场失败。
    """
    session = FakeSession(
        conversations=[conversation("c1", pk=1), conversation("c2", pk=2)],
        messages={
            "c1": [message(m["role"], m["content"]) for m in SUBSTANTIVE_MESSAGES],
            "c2": [message(m["role"], m["content"]) for m in SUBSTANTIVE_MESSAGES],
        },
    )
    raw = json.dumps(
        {
            "candidates": [
                {
                    "question": "F10-M 的质保期是多久？",
                    "answer": "整机质保 2 年。",
                    "evidence_quote": "整机质保 2 年",
                }
            ]
        },
        ensure_ascii=False,
    )
    generate = make_generate([RuntimeError("model down"), raw])

    totals = await make_service(session, generate).run_batch(limit=10)

    assert totals == {"status": "failed", "processed": 2, "candidates": 1, "skipped_filtered": 0, "failed": 1}
    # 失败的那个会话不打标（下轮重试），成功那个正常打标
    assert len(session.sqls("UPDATE udesk_conversations")) == 1
    assert len(session.sqls("curated_qa_candidates")) == 1
    # 真实原因写进状态表，而不是被二次异常顶掉后无声无息
    assert "model down" in outcome_params(session)["error"]
    # 租约无论成败都要释放，否则下一轮永远抢不到
    assert session.sqls("summarize_lease_expires_at = NULL")


async def test_run_batch_skips_when_summarize_lease_held():
    """手动触发与每小时 cron 是同一入口，租约防两者把同一批会话重复送 LLM。"""
    session = FakeSession(conversations=[conversation()], lease_free=False)
    generate = make_generate([])

    totals = await make_service(session, generate).run_batch(limit=10)

    assert totals == {"status": "skipped_lease", "processed": 0, "candidates": 0, "skipped_filtered": 0, "failed": 0}
    assert generate.calls == []  # 一条消息都没送 LLM
    assert not session.sqls("FROM udesk_conversations")  # 没读会话
    # 被租约挡下也留痕，页面才分得清「没跑」和「被挡了」
    assert any("summarize_status = :status" in sql for sql, _ in session.executed)
    assert not session.sqls("summarize_lease_expires_at = NULL")  # 不能释放别人的租约
    # 被挡下的不算一轮，candidates 传 NULL 让 COALESCE 留住上一轮的数字
    assert outcome_params(session)["candidates"] is None
