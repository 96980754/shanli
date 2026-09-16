from __future__ import annotations

from yuxi.repositories.curated_qa_repository import (
    CuratedQARepository,
    hash_qa_question,
    normalize_qa_question,
)


def test_normalize_qa_question_ignores_outer_and_repeated_whitespace():
    assert normalize_qa_question("  如何   重置密码？\n") == "如何 重置密码？"


def test_normalize_qa_question_casefolds_english():
    assert normalize_qa_question("How To RESET Password") == "how to reset password"


def test_hash_qa_question_is_stable_after_normalization():
    first = normalize_qa_question("  API   Key  ")
    second = normalize_qa_question("api key")
    assert first == second
    assert hash_qa_question(first) == hash_qa_question(second)


class FakeResult:
    def __init__(self, rows=None, scalar=None, rowcount=None):
        self._rows = rows if rows is not None else []
        self._scalar = scalar
        self._rowcount = rowcount

    def scalar(self):
        return self._scalar

    def scalar_one_or_none(self):
        return self._scalar

    def scalars(self):
        return self

    def all(self):
        return self._rows

    @property
    def rowcount(self):
        return self._rowcount


class FakeSession:
    def __init__(self, responders):
        self.responders = list(responders)
        self.executed: list = []
        self.added: list = []

    async def execute(self, statement):
        self.executed.append(statement)
        responder = self.responders.pop(0) if self.responders else FakeResult()
        return responder() if callable(responder) else responder

    def add(self, item):
        self.added.append(item)

    async def flush(self):
        pass


async def test_list_all_builds_count_then_page_query_with_filters():
    """两次 SELECT（计数 + 分页），筛选条件同时下推到两条语句"""
    session = FakeSession([FakeResult(scalar=1), FakeResult(rows=[])])
    repo = CuratedQARepository(session)

    total, items = await repo.list_all(
        agent_slug="kefu",
        source_type="udesk",
        enabled=False,
        keyword="发票",
        limit=20,
        offset=40,
    )

    assert (total, items) == (1, [])
    assert len(session.executed) == 2
    count_sql = str(session.executed[0])
    page_sql = str(session.executed[1])
    for sql in (count_sql, page_sql):
        assert "agent_slug" in sql and "source_type" in sql
        assert "enabled" in sql
        # ilike 渲染为 lower(col) LIKE lower(:param)，关键词同时匹配问题/答案
        assert "LIKE" in sql and "question" in sql and "answer" in sql
    assert "ORDER BY" in page_sql
    assert "LIMIT" in page_sql and "OFFSET" in page_sql


async def test_list_all_clamps_limit_and_offset():
    session = FakeSession([FakeResult(scalar=0), FakeResult(rows=[])])
    repo = CuratedQARepository(session)

    await repo.list_all(limit=99999, offset=-5)

    page_stmt = session.executed[1]
    assert page_stmt._limit == 200  # 上限 200
    assert page_stmt._offset == 0


async def test_set_enabled_updates_by_id_list_with_operator():
    session = FakeSession([FakeResult(rowcount=3)])
    repo = CuratedQARepository(session)

    updated = await repo.set_enabled([1, 2, 3], enabled=False, operator_uid="admin")

    assert updated == 3
    sql = str(session.executed[0])
    assert sql.startswith("UPDATE curated_qa_pairs")
    assert "id IN" in sql
    assert "enabled" in sql and "updated_by" in sql


async def test_set_enabled_noop_on_empty_ids():
    session = FakeSession([])
    repo = CuratedQARepository(session)

    assert await repo.set_enabled([], enabled=True, operator_uid="admin") == 0
    assert session.executed == []


async def test_delete_removes_by_id_list():
    session = FakeSession([FakeResult(rowcount=2)])
    repo = CuratedQARepository(session)

    deleted = await repo.delete([7, 8])

    assert deleted == 2
    sql = str(session.executed[0])
    assert sql.startswith("DELETE FROM curated_qa_pairs")
    assert "id IN" in sql


async def test_upsert_from_candidate_inserts_disabled_with_udesk_source():
    """C6：候选采纳入库 enabled=False，绝不自动启用；来源指向原始会话。"""
    from yuxi.storage.postgres.models_curated_qa import CuratedQAPair

    session = FakeSession([FakeResult(scalar=None)])  # get_exact 无同键
    repo = CuratedQARepository(session)

    item = await repo.upsert_from_candidate(
        agent_slug="kefu",
        question="F10-M 的质保期是多久？",
        answer="整机质保 2 年。",
        operator_uid="admin",
        source_conversation_id="conv-9",
    )

    assert session.added == [item]
    assert item.enabled is False
    assert item.source_type == "udesk"
    assert item.source_conversation_id == "conv-9"
    assert item.question_hash == hash_qa_question(normalize_qa_question("F10-M 的质保期是多久？"))


async def test_upsert_from_candidate_refreshes_existing_but_keeps_enabled():
    from yuxi.storage.postgres.models_curated_qa import CuratedQAPair

    existing = CuratedQAPair(
        agent_slug="kefu",
        question="旧问法",
        answer="旧答案",
        enabled=True,
        source_type="feedback",
    )
    session = FakeSession([FakeResult(scalar=existing)])
    repo = CuratedQARepository(session)

    item = await repo.upsert_from_candidate(
        agent_slug="kefu",
        question="新问法",
        answer="新答案",
        operator_uid="admin",
        source_conversation_id="conv-9",
    )

    assert item is existing
    assert item.enabled is True  # 启用状态保持原值，由管理员单独管理
    assert item.answer == "新答案" and item.source_type == "udesk" and item.source_conversation_id == "conv-9"
    assert session.added == []


async def test_upsert_from_candidate_rejects_empty_content():
    repo = CuratedQARepository(FakeSession([FakeResult(scalar=None)]))
    for question, answer in (("", "有答案"), ("有问题", "  ")):
        try:
            await repo.upsert_from_candidate(
                agent_slug="kefu",
                question=question,
                answer=answer,
                operator_uid="admin",
                source_conversation_id="conv-9",
            )
        except ValueError:
            continue
        raise AssertionError(f"应拒绝空内容: {question!r}/{answer!r}")


async def test_update_content_recomputes_hash_and_invalidates_embedding():
    from yuxi.storage.postgres.models_curated_qa import CuratedQAPair

    item = CuratedQAPair(
        agent_slug="kefu",
        question="旧问题",
        normalized_question=normalize_qa_question("旧问题"),
        question_hash=hash_qa_question(normalize_qa_question("旧问题")),
        answer="旧答案",
        enabled=False,
        source_type="udesk",
        question_embedding=[0.1, 0.2],
    )
    # 两次 scalar_one_or_none：get(qa_id) → item；get_exact（查同键冲突）→ None
    session = FakeSession([FakeResult(scalar=item), FakeResult(scalar=None)])
    repo = CuratedQARepository(session)

    updated = await repo.update_content(1, question="新问题", answer="新答案", operator_uid="admin")

    assert updated is item
    assert updated.question == "新问题"
    assert updated.normalized_question == normalize_qa_question("新问题")
    assert updated.question_hash == hash_qa_question(normalize_qa_question("新问题"))
    assert updated.question_embedding is None  # 语义向量按新问题懒回填
    assert updated.answer == "新答案" and updated.updated_by == "admin"


async def test_update_content_only_answer_keeps_match_keys():
    from yuxi.storage.postgres.models_curated_qa import CuratedQAPair

    old_hash = hash_qa_question(normalize_qa_question("会员怎么开通"))
    item = CuratedQAPair(
        agent_slug="kefu",
        question="会员怎么开通",
        normalized_question=normalize_qa_question("会员怎么开通"),
        question_hash=old_hash,
        answer="旧答案",
    )
    # 只改答案：get(qa_id) → item，不再查冲突
    session = FakeSession([FakeResult(scalar=item)])
    repo = CuratedQARepository(session)

    await repo.update_content(1, question="会员怎么开通", answer="联系客服开通", operator_uid="admin")

    assert item.question_hash == old_hash  # 问题未变，精确匹配键不动
    assert item.answer == "联系客服开通"
    assert len(session.executed) == 1


async def test_update_content_rejects_duplicate_question_under_same_agent():
    from yuxi.storage.postgres.models_curated_qa import CuratedQAPair

    item = CuratedQAPair(
        agent_slug="kefu",
        question="问题A",
        normalized_question=normalize_qa_question("问题A"),
        question_hash=hash_qa_question(normalize_qa_question("问题A")),
        answer="答案A",
    )
    conflict = CuratedQAPair(agent_slug="kefu", question="问题B", answer="答案B")
    conflict.id = 42
    # get(qa_id) → item；get_exact（新问题已有同键）→ conflict
    session = FakeSession([FakeResult(scalar=item), FakeResult(scalar=conflict)])
    repo = CuratedQARepository(session)

    try:
        await repo.update_content(1, question="问题B", answer="改后答案", operator_uid="admin")
    except ValueError as exc:
        assert "相同问题" in str(exc)
    else:
        raise AssertionError("同 agent 同题应拒绝")


async def test_update_content_missing_returns_none_and_rejects_empty():
    repo = CuratedQARepository(FakeSession([FakeResult(scalar=None)]))
    assert await repo.update_content(1, question="q", answer="a", operator_uid="admin") is None

    for question, answer in (("", "有答案"), ("有问题", "  ")):
        try:
            await repo.update_content(1, question=question, answer=answer, operator_uid="admin")
        except ValueError:
            continue
        raise AssertionError(f"应拒绝空内容: {question!r}/{answer!r}")
