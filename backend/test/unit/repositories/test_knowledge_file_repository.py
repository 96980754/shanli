"""KnowledgeFileRepository 写字段白名单回归测试。

背景：`_writable_fields` 曾漏收 enrichment 系列列，`_sanitize_data` 据此把
`update_enrichment_fields_with_version` 写入的 `enrichment_status`/`enrichment_data` 等
全部静默丢弃，导致信息增强“生成成功”但内容永远不落库（version 靠 SQL 表达式递增、
其余字段全空）。此处直接锁定白名单，防止再次漏加。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import postgresql

from yuxi.repositories import knowledge_file_repository as repo_module
from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository


def test_sanitize_data_keeps_enrichment_fields():
    data = {
        "enrichment_status": "ready",
        "enrichment_version": 3,
        "enrichment_data": {"summary": {"text": "摘要"}},
        "enrichment_content_hash": "abc123",
        "enrichment_generated_at": "2026-08-14T00:00:00",
        "enrichment_error": None,
        "enrichment_possibly_outdated": False,
        "filename": "doc.md",
    }
    sanitized = KnowledgeFileRepository._sanitize_data(data)
    for key in (
        "enrichment_status",
        "enrichment_version",
        "enrichment_data",
        "enrichment_content_hash",
        "enrichment_generated_at",
        "enrichment_error",
        "enrichment_possibly_outdated",
    ):
        assert key in sanitized, f"{key} 被 _writable_fields 过滤，信息增强无法落库"
    assert sanitized["enrichment_status"] == "ready"
    assert sanitized["filename"] == "doc.md"


class _EmptyResult:
    """模拟空查询结果：仅支撑 search_documents 的 SQL 结构断言，无需真实行。"""

    def scalar_one(self):
        return 0

    def mappings(self):
        return self

    def all(self):
        return []


class _RecordingSession:
    """记录每次 execute 的编译 SQL，供结构断言。"""

    def __init__(self, result=None):
        self.compiled: list[str] = []
        self._result = result if result is not None else _EmptyResult()

    async def execute(self, statement, *args, **kwargs):
        self.compiled.append(str(statement.compile(dialect=postgresql.dialect())))
        return self._result


@pytest.mark.asyncio
async def test_search_documents_includes_folders(monkeypatch):
    session = _RecordingSession()

    @asynccontextmanager
    async def fake_session_context():
        yield session

    monkeypatch.setattr(repo_module.pg_manager, "get_async_session_context", fake_session_context)

    results, total = await KnowledgeFileRepository().search_documents(
        kb_ids=["kb-1"], keyword="证书", page=1, page_size=10
    )

    assert results == []
    assert total == 0
    assert len(session.compiled) == 2
    for sql in session.compiled:
        lowered = sql.lower()
        assert "is_folder is false" not in lowered, "search_documents 不应通过 WHERE 排除文件夹"
        assert "is_folder" in lowered, "SELECT 应包含 is_folder 以返回文件夹结果"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("search_type", "expect_chunk_join", "expect_folder_branch"),
    [
        ("filename", False, False),
        ("folder", False, True),
        ("content", True, False),
    ],
)
async def test_search_documents_search_type_modes(
    monkeypatch, search_type, expect_chunk_join, expect_folder_branch
):
    """三种搜索方式的 SQL 结构：filename 不 join chunk、folder 走目录匹配分支、content 才 join chunk。"""
    session = _RecordingSession()

    @asynccontextmanager
    async def fake_session_context():
        yield session

    monkeypatch.setattr(repo_module.pg_manager, "get_async_session_context", fake_session_context)

    results, total = await KnowledgeFileRepository().search_documents(
        kb_ids=["kb-1"], keyword="证书", search_type=search_type, page=1, page_size=10
    )

    assert results == []
    assert total == 0
    for sql in session.compiled:
        lowered = sql.lower()
        assert ("knowledge_chunks" in lowered) is expect_chunk_join, (
            f"search_type={search_type}: content 模式才应 join knowledge_chunks"
        )
        assert ("is_folder is true" in lowered) is expect_folder_branch, (
            f"search_type={search_type}: folder 模式才应匹配真实文件夹名"
        )


class _ScalarResult:
    def __init__(self, row):
        self._row = row

    def scalar_one_or_none(self):
        return self._row


class _ChainSession:
    """按查询中的 file_id 返回 canned 文件夹行的 fake session。"""

    def __init__(self, rows_by_id):
        self.rows_by_id = rows_by_id
        self.queried_ids: list[str] = []

    async def execute(self, statement, *args, **kwargs):
        params = statement.compile(dialect=postgresql.dialect()).params
        file_id = next((value for key, value in params.items() if "file_id" in key), None)
        self.queried_ids.append(file_id)
        return _ScalarResult(self.rows_by_id.get(file_id))


@pytest.mark.asyncio
async def test_get_folder_chain_returns_top_down_chain(monkeypatch):
    rows_by_id = {
        "folder-a": SimpleNamespace(file_id="folder-a", filename="A", parent_id=None),
        "folder-b": SimpleNamespace(file_id="folder-b", filename="B", parent_id="folder-a"),
        "folder-c": SimpleNamespace(file_id="folder-c", filename="C", parent_id="folder-b"),
    }
    session = _ChainSession(rows_by_id)

    @asynccontextmanager
    async def fake_session_context():
        yield session

    monkeypatch.setattr(repo_module.pg_manager, "get_async_session_context", fake_session_context)

    chain = await KnowledgeFileRepository().get_folder_chain(kb_id="kb-1", folder_id="folder-c")

    assert chain == [
        {"file_id": "folder-a", "filename": "A"},
        {"file_id": "folder-b", "filename": "B"},
        {"file_id": "folder-c", "filename": "C"},
    ]
    assert session.queried_ids == ["folder-c", "folder-b", "folder-a"]


@pytest.mark.asyncio
async def test_list_same_name_files_only_matches_current_version(monkeypatch):
    """同名检查只匹配正式当前版本（is_current），不匹配失败/待处理候选。

    失败或待处理的版本/替换候选（is_current=false 但 is_active=true）在文件列表不可见，
    若被同名检查匹配会像孤儿一样挡住同名文件重传；与 list_by_content_hash 的
    is_current 语义、版本候选逻辑保持一致。
    """
    session = _RecordingSession(result=_DeleteCandidatesResult([]))

    @asynccontextmanager
    async def fake_session_context():
        yield session

    monkeypatch.setattr(repo_module.pg_manager, "get_async_session_context", fake_session_context)

    await KnowledgeFileRepository().list_same_name_files(kb_id="kb-1", parent_id=None, filename="doc.md")

    assert len(session.compiled) == 1
    sql = session.compiled[0].lower()
    where_clause = sql.rpartition("where")[2]
    assert "is_current" in where_clause, "同名检查应过滤 is_current=true 的正式当前版本"
    assert "is_active" not in where_clause, "同名检查不应再按 is_active 匹配不可见的失败/待处理候选"


@pytest.mark.asyncio
async def test_get_folder_chain_returns_none_when_folder_missing(monkeypatch):
    session = _ChainSession({})

    @asynccontextmanager
    async def fake_session_context():
        yield session

    monkeypatch.setattr(repo_module.pg_manager, "get_async_session_context", fake_session_context)

    chain = await KnowledgeFileRepository().get_folder_chain(kb_id="kb-1", folder_id="missing")

    assert chain is None


@pytest.mark.asyncio
async def test_get_folder_chain_guards_against_cycle(monkeypatch):
    rows_by_id = {
        "folder-a": SimpleNamespace(file_id="folder-a", filename="A", parent_id="folder-b"),
        "folder-b": SimpleNamespace(file_id="folder-b", filename="B", parent_id="folder-a"),
    }
    session = _ChainSession(rows_by_id)

    @asynccontextmanager
    async def fake_session_context():
        yield session

    monkeypatch.setattr(repo_module.pg_manager, "get_async_session_context", fake_session_context)

    chain = await KnowledgeFileRepository().get_folder_chain(kb_id="kb-1", folder_id="folder-a")

    assert chain == [
        {"file_id": "folder-b", "filename": "B"},
        {"file_id": "folder-a", "filename": "A"},
    ]


class _DeleteCandidatesResult:
    """fake result：第一次 execute（候选查询）走 .scalars().all()。"""

    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows


class _DeleteMainResult:
    """fake result：第二次 execute（主记录查询）走 .scalar_one_or_none()。"""

    def __init__(self, row):
        self._row = row

    def scalar_one_or_none(self):
        return self._row


class _DeleteSession:
    """fake session：第一次 execute 返回候选列表、第二次返回主记录，记录 delete 调用与编译 SQL。"""

    def __init__(self, candidates, main):
        self._candidates = candidates
        self._main = main
        self._execute_count = 0
        self.deleted: list = []
        self.compiled: list[str] = []

    async def execute(self, statement, *args, **kwargs):
        self.compiled.append(str(statement.compile(dialect=postgresql.dialect())))
        self._execute_count += 1
        if self._execute_count == 1:
            return _DeleteCandidatesResult(self._candidates)
        return _DeleteMainResult(self._main)

    async def delete(self, record):
        self.deleted.append(record)


@pytest.mark.asyncio
async def test_delete_cascades_pending_version_and_replacement_candidates(monkeypatch):
    main = SimpleNamespace(file_id="file-main", filename="doc.md")
    version_candidate = SimpleNamespace(file_id="file-cand-v2", filename="doc.md")
    replacement_candidate = SimpleNamespace(file_id="file-cand-rep", filename="doc.md")
    session = _DeleteSession(candidates=[version_candidate, replacement_candidate], main=main)

    @asynccontextmanager
    async def fake_session_context():
        yield session

    monkeypatch.setattr(repo_module.pg_manager, "get_async_session_context", fake_session_context)

    await KnowledgeFileRepository().delete(file_id="file-main")

    # 候选与主记录都在同一事务内删除，避免留下不可见的孤儿记录
    assert session.deleted == [version_candidate, replacement_candidate, main]
    candidate_sql = session.compiled[0].lower()
    assert "supersedes_file_id" in candidate_sql, "版本候选应按 supersedes_file_id 关联清理"
    assert "replacement_target_file_id" in candidate_sql, "替换候选应按 replacement_target_file_id 关联清理"
    assert "is_current is false" in candidate_sql, "只应清理未激活候选，不影响正式版本"


@pytest.mark.asyncio
async def test_delete_without_candidates_only_deletes_main(monkeypatch):
    main = SimpleNamespace(file_id="file-main", filename="doc.md")
    session = _DeleteSession(candidates=[], main=main)

    @asynccontextmanager
    async def fake_session_context():
        yield session

    monkeypatch.setattr(repo_module.pg_manager, "get_async_session_context", fake_session_context)

    await KnowledgeFileRepository().delete(file_id="file-main")

    assert session.deleted == [main]


@pytest.mark.asyncio
async def test_list_pending_candidate_file_ids_returns_matching_rows(monkeypatch):
    rows = [("file-cand-v2",), ("file-cand-rep",)]
    session = _DeleteSession(candidates=rows, main=None)

    @asynccontextmanager
    async def fake_session_context():
        yield session

    monkeypatch.setattr(repo_module.pg_manager, "get_async_session_context", fake_session_context)

    candidate_ids = await KnowledgeFileRepository().list_pending_candidate_file_ids(file_id="file-main")

    assert candidate_ids == ["file-cand-v2", "file-cand-rep"]
