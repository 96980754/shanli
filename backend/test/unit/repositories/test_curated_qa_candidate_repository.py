"""客服记录候选知识仓储单元测试（审核页筛选与状态流转）。"""

from __future__ import annotations

from yuxi.repositories.curated_qa_candidate_repository import CuratedQACandidateRepository


class FakeResult:
    def __init__(self, rows=None, scalar=None, rowcount=None):
        self._rows = rows if rows is not None else []
        self._scalar = scalar
        self._rowcount = rowcount

    def scalar(self):
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

    async def execute(self, statement):
        self.executed.append(statement)
        responder = self.responders.pop(0) if self.responders else FakeResult()
        return responder() if callable(responder) else responder


async def test_list_all_pushes_filters_to_count_and_page_query():
    session = FakeSession([FakeResult(scalar=2), FakeResult(rows=[])])
    repo = CuratedQACandidateRepository(session)

    total, items = await repo.list_all(
        review_status="pending",
        dedup_status="unique",
        domain="terminal",
        keyword="质保",
        limit=20,
        offset=20,
    )

    assert (total, items) == (2, [])
    for statement in session.executed:
        sql = str(statement)
        assert "review_status" in sql and "dedup_status" in sql and "domain" in sql
        assert "LIKE" in sql and "question" in sql and "answer" in sql
    page_sql = str(session.executed[1])
    assert "ORDER BY" in page_sql and "LIMIT" in page_sql and "OFFSET" in page_sql


async def test_list_all_clamps_limit_and_offset():
    session = FakeSession([FakeResult(scalar=0), FakeResult(rows=[])])
    repo = CuratedQACandidateRepository(session)

    await repo.list_all(limit=99999, offset=-5)

    page_stmt = session.executed[1]
    assert page_stmt._limit == 200
    assert page_stmt._offset == 0


async def test_set_review_updates_status_with_operator_and_note():
    session = FakeSession([FakeResult(rowcount=1)])
    repo = CuratedQACandidateRepository(session)

    updated = await repo.set_review([5], review_status="rejected", reviewed_by="admin", note="答案含糊")

    assert updated == 1
    sql = str(session.executed[0])
    assert sql.startswith("UPDATE curated_qa_candidates")
    assert "review_status" in sql and "reviewed_by" in sql and "review_note" in sql


async def test_existing_question_hashes_queries_curated_pairs():
    session = FakeSession([FakeResult(rows=[("abc",)])])
    repo = CuratedQACandidateRepository(session)

    found = await repo.existing_question_hashes(["abc", "def"])

    assert found == {"abc"}
    sql = str(session.executed[0])
    assert "question_hash" in sql and "curated_qa_pairs" in sql
