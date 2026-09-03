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


class _QueueResult:
    """fake result：支持 scalar_one_or_none / scalars().all() / all() 三种取值。"""

    def __init__(self, *, scalar=None, rows=None):
        self._scalar = scalar
        self._rows = rows

    def scalar_one_or_none(self):
        return self._scalar

    def scalars(self):
        return self

    def all(self):
        return self._rows if self._rows is not None else []


class _DeleteCandidatesResult(_QueueResult):
    """兼容既有同名检查测试的位置参数调用（原实现直接接收 rows 列表）。"""

    def __init__(self, rows=None):
        super().__init__(rows=rows)


class _DeleteSession:
    """fake session：按 execute 调用顺序依次吐出预设结果，记录 delete 调用与编译 SQL。"""

    def __init__(self, results):
        self._results = list(results)
        self._execute_count = 0
        self.deleted: list = []
        self.compiled: list[str] = []

    async def execute(self, statement, *args, **kwargs):
        self.compiled.append(str(statement.compile(dialect=postgresql.dialect())))
        result = self._results[self._execute_count]
        self._execute_count += 1
        return result

    async def delete(self, record):
        self.deleted.append(record)


class _CreateGuardSession(_DeleteSession):
    """create_document_with_duplicate_guard 专用 fake：兜住末尾的 session.add / flush。"""

    def __init__(self, results):
        super().__init__(results)
        self.added = None

    def add(self, record):
        self.added = record

    async def flush(self):
        pass


@pytest.mark.asyncio
async def test_delete_cascades_whole_version_family(monkeypatch):
    """删除当前文档（family=True）应连同同 logical_document_id 的整族行一并删除。"""
    main = SimpleNamespace(
        file_id="file-main",
        kb_id="kb-1",
        is_current=True,
        logical_document_id="logical-1",
        filename="doc.md",
    )
    version_candidate = SimpleNamespace(file_id="file-cand-v2", filename="doc.md")
    archived_old_version = SimpleNamespace(file_id="file-v1", filename="doc.md")
    replacement_candidate = SimpleNamespace(file_id="file-cand-rep", filename="doc.md")
    session = _DeleteSession(
        [
            _QueueResult(scalar=main),  # resolve_delete_file_ids: 目标行
            _QueueResult(  # resolve_delete_file_ids: 命中 id 集
                rows=[
                    ("file-cand-v2",),
                    ("file-v1",),
                    ("file-cand-rep",),
                    ("file-main",),
                ]
            ),
            _QueueResult(  # delete: 取整族行对象
                rows=[version_candidate, archived_old_version, replacement_candidate, main]
            ),
        ]
    )

    @asynccontextmanager
    async def fake_session_context():
        yield session

    monkeypatch.setattr(repo_module.pg_manager, "get_async_session_context", fake_session_context)

    await KnowledgeFileRepository().delete(file_id="file-main")

    # 归档旧版本 + 未激活候选与当前行整族删除，避免留下孤儿行导致重传同名旧内容误判重复
    assert session.deleted == [version_candidate, archived_old_version, replacement_candidate, main]
    resolve_sql = session.compiled[1].lower()
    assert "logical_document_id" in resolve_sql, "删除当前文档应按 logical_document_id 级联同族归档行"


@pytest.mark.asyncio
async def test_delete_without_version_family_only_deletes_main(monkeypatch):
    """无版本链的文档（logical_document_id 为空）删除时仅清理目标行本身。"""
    main = SimpleNamespace(
        file_id="file-main",
        kb_id="kb-1",
        is_current=True,
        logical_document_id=None,
        filename="doc.md",
    )
    session = _DeleteSession(
        [
            _QueueResult(scalar=main),  # resolve: 目标行
            _QueueResult(rows=[("file-main",)]),  # resolve: 只有自身
            _QueueResult(rows=[main]),  # delete
        ]
    )

    @asynccontextmanager
    async def fake_session_context():
        yield session

    monkeypatch.setattr(repo_module.pg_manager, "get_async_session_context", fake_session_context)

    await KnowledgeFileRepository().delete(file_id="file-main")

    assert session.deleted == [main]
    assert "is_current is false" not in session.compiled[1].lower()


@pytest.mark.asyncio
async def test_list_pending_candidate_file_ids_returns_matching_rows(monkeypatch):
    rows = [("file-cand-v2",), ("file-cand-rep",)]
    session = _DeleteSession([_QueueResult(rows=rows)])

    @asynccontextmanager
    async def fake_session_context():
        yield session

    monkeypatch.setattr(repo_module.pg_manager, "get_async_session_context", fake_session_context)

    candidate_ids = await KnowledgeFileRepository().list_pending_candidate_file_ids(file_id="file-main")

    assert candidate_ids == ["file-cand-v2", "file-cand-rep"]


@pytest.mark.asyncio
async def test_create_duplicate_guard_content_query_ignores_archived_rows(monkeypatch):
    """内容重复守卫只匹配当前可见文档行。

    回归 bug1：多版本文档删除后再传旧版本内容，若守卫 SQL 仍按旧条件
    （is_active OR replacement_target_file_id is null OR status not in 失败态）判定，
    归档旧版本/删除遗留孤儿行会被误判为"内容相同"而拒绝入库。守卫的 content_hash
    查询必须只看 is_current & is_active，二者缺一不可。
    """
    # 执行序：2 条 advisory lock → content_hash 精确查询 → 同名查询 → 落库
    session = _CreateGuardSession([_QueueResult(), _QueueResult(), _QueueResult(rows=[]), _QueueResult(rows=[])])

    @asynccontextmanager
    async def fake_session_context():
        yield session

    monkeypatch.setattr(repo_module.pg_manager, "get_async_session_context", fake_session_context)

    outcome = await KnowledgeFileRepository().create_document_with_duplicate_guard(
        file_id="file-new",
        data={
            "kb_id": "kb-1",
            "filename": "doc.md",
            "content_hash": "hash-x",
            "path": "kb-1/upload/doc.md",
        },
        duplicate_strategy="prompt",
    )

    assert outcome.action == "created"
    guard_sql = next(sql for sql in session.compiled if "content_hash" in sql.lower())
    where_clause = guard_sql.lower().rpartition("where")[2]
    assert "is_current is true" in where_clause, "内容重复守卫应只匹配 is_current=true 的当前行"
    assert "is_active is true" in where_clause, "内容重复守卫应同时要求 is_active=true，排除归档行"
    assert "replacement_target_file_id" not in where_clause, "守卫不应再按旧条件放行归档/孤儿行"


@pytest.mark.asyncio
async def test_directory_listing_carries_logical_document_id(monkeypatch):
    """目录/根视图列表投影需带出 logical_document_id。

    回归 bug3：文件列表（默认 status=all）走 _list_directory_documents 的裁剪投影，
    若缺 logical_document_id 列，list_document_files 无法按家族统计待审核候选，
    所有行的 version_review_pending 恒为 False，冲突/待审核变更在表格不可见。
    """
    session = _RecordingSession()

    @asynccontextmanager
    async def fake_session_context():
        yield session

    monkeypatch.setattr(repo_module.pg_manager, "get_async_session_context", fake_session_context)

    await KnowledgeFileRepository().list_documents(kb_id="kb-1")

    joined_sql = " ".join(sql.lower() for sql in session.compiled)
    assert "logical_document_id" in joined_sql, "目录列表投影应包含 logical_document_id（供文件行待审核提示）"
