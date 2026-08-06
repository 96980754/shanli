from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from yuxi.repositories import knowledge_file_repository as repo_module
from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository


class ScalarResult:
    def __init__(self, *, scalar=None, scalars=None):
        self._scalar = scalar
        self._scalars = scalars

    def scalar_one_or_none(self):
        return self._scalar

    def scalars(self):
        return SimpleNamespace(all=lambda: self._scalars)


class CandidateSession:
    def __init__(self, current, *, existing_candidate=None, latest_version=2):
        self.current = current
        self.existing_candidate = existing_candidate
        self.latest_version = latest_version
        self.scalar_statements = []
        self.added = []
        self.flush = AsyncMock()

    async def execute(self, _statement):
        return ScalarResult(scalar=self.current)

    async def scalar(self, statement):
        self.scalar_statements.append(statement)
        if len(self.scalar_statements) == 1:
            return self.existing_candidate
        return self.latest_version

    def add(self, record):
        self.added.append(record)


@pytest.mark.asyncio
async def test_create_third_version_ignores_archived_first_version():
    current = SimpleNamespace(
        file_id="file-v2",
        logical_document_id="logical-1",
        is_current=True,
        is_folder=False,
    )
    session = CandidateSession(current, existing_candidate=None, latest_version=2)

    candidate = await KnowledgeFileRepository().create_candidate_version(
        kb_id="kb-1",
        current_file_id=current.file_id,
        data={"file_id": "file-v3", "filename": "v3.docx", "status": "uploaded"},
        session=session,
    )

    candidate_query = str(session.scalar_statements[0])
    assert "knowledge_files.supersedes_file_id" in candidate_query
    assert candidate.document_version == 3
    assert candidate.supersedes_file_id == "file-v2"
    assert candidate.is_current is False
    assert session.added == [candidate]
    session.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_direct_candidate_still_blocks_another_version():
    current = SimpleNamespace(
        file_id="file-v2",
        logical_document_id="logical-1",
        is_current=True,
        is_folder=False,
    )
    session = CandidateSession(current, existing_candidate="file-v3")

    with pytest.raises(ValueError, match="UPDATE_IN_PROGRESS"):
        await KnowledgeFileRepository().create_candidate_version(
            kb_id="kb-1",
            current_file_id=current.file_id,
            data={"file_id": "file-v4", "filename": "v4.docx", "status": "uploaded"},
            session=session,
        )

    assert "knowledge_files.supersedes_file_id" in str(session.scalar_statements[0])
    assert session.added == []


@pytest.mark.asyncio
async def test_failed_validation_candidate_does_not_block_retry():
    current = SimpleNamespace(
        file_id="file-v1",
        logical_document_id="logical-1",
        is_current=True,
        is_folder=False,
    )
    session = CandidateSession(current, existing_candidate=None, latest_version=2)

    candidate = await KnowledgeFileRepository().create_candidate_version(
        kb_id="kb-1",
        current_file_id=current.file_id,
        data={"file_id": "file-v3", "filename": "v3.docx", "status": "uploaded"},
        session=session,
    )

    candidate_query = str(session.scalar_statements[0])
    assert "validation_failed" not in candidate_query
    assert candidate.document_version == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("candidate_status", ["conflict_clear", "conflict_review"])
async def test_activate_candidate_restores_done_status(candidate_status):
    current = SimpleNamespace(
        file_id="file-current",
        is_current=True,
        updated_by=None,
        updated_at=None,
    )
    candidate = SimpleNamespace(
        file_id="file-candidate",
        logical_document_id="logical-1",
        supersedes_file_id="file-current",
        is_current=False,
        status=candidate_status,
        error_message="temporary candidate error",
        activated_at=None,
        updated_by=None,
        updated_at=None,
    )
    session = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[
                ScalarResult(scalar=candidate),
                ScalarResult(scalars=[current, candidate]),
            ]
        ),
        flush=AsyncMock(),
    )

    archived, activated = await KnowledgeFileRepository().activate_candidate(
        kb_id="kb-1",
        candidate_file_id=candidate.file_id,
        expected_current_file_id=current.file_id,
        operator_id="admin",
        session=session,
    )

    assert archived is current
    assert activated is candidate
    assert current.is_current is False
    assert candidate.is_current is True
    assert candidate.status == "done"
    assert candidate.error_message is None
    assert candidate.activated_at is not None
    assert candidate.updated_by == "admin"
    session.flush.assert_awaited_once()


def test_normalize_document_base_name_strips_version_suffix():
    from yuxi.repositories.knowledge_file_repository import normalize_document_base_name

    assert normalize_document_base_name("sglang-v1.1.docx") == "sglang"
    assert normalize_document_base_name("sglang-v1.0.docx") == "sglang"
    assert normalize_document_base_name("sglang_v2.docx") == "sglang"
    assert normalize_document_base_name("report-2024.pdf") == "report"
    assert normalize_document_base_name("manual_3.xlsx") == "manual"
    # 无版本号的文件名保留原名
    assert normalize_document_base_name("plain.docx") == "plain"
    assert normalize_document_base_name("README.md") == "readme"
    # "测试1/测试2"是版本关系，剥离后基础名相同
    assert normalize_document_base_name("测试1.docx") == "测试"
    assert normalize_document_base_name("测试2.docx") == "测试"


@pytest.mark.asyncio
async def test_create_candidate_backfills_first_version_anchor():
    # 首版文档（无版本链，logical_document_id 为 None）升级为第二版
    current = SimpleNamespace(
        file_id="file-v1",
        logical_document_id=None,
        document_version=None,
        is_current=True,
        is_folder=False,
    )
    session = CandidateSession(current, existing_candidate=None, latest_version=None)

    candidate = await KnowledgeFileRepository().create_candidate_version(
        kb_id="kb-1",
        current_file_id=current.file_id,
        data={"file_id": "file-v2", "filename": "v2.docx", "status": "uploaded"},
        session=session,
    )

    # 建候选时回填旧版锚点，否则 list_versions(旧版) 查不到任何版本
    assert current.logical_document_id == "file-v1"
    assert current.document_version == 1
    assert candidate.logical_document_id == "file-v1"
    assert candidate.document_version == 2
    assert candidate.supersedes_file_id == "file-v1"


@pytest.mark.asyncio
async def test_create_candidate_keeps_existing_chain_anchor():
    # 已是版本链中间节点：锚点保持原 logical-1，不得被覆盖为自身 file_id
    current = SimpleNamespace(
        file_id="file-v2",
        logical_document_id="logical-1",
        document_version=2,
        is_current=True,
        is_folder=False,
    )
    session = CandidateSession(current, existing_candidate=None, latest_version=2)

    candidate = await KnowledgeFileRepository().create_candidate_version(
        kb_id="kb-1",
        current_file_id=current.file_id,
        data={"file_id": "file-v3", "filename": "v3.docx", "status": "uploaded"},
        session=session,
    )

    assert current.logical_document_id == "logical-1"
    assert current.document_version == 2
    assert candidate.logical_document_id == "logical-1"
    assert candidate.document_version == 3


@pytest.mark.asyncio
async def test_list_versions_returns_all_when_anchor_present(monkeypatch):
    v1 = SimpleNamespace(file_id="file-v1", document_version=1, is_current=True)
    v2 = SimpleNamespace(file_id="file-v2", document_version=2, is_current=False)
    session = SimpleNamespace(
        scalar=AsyncMock(return_value="file-v1"),
        execute=AsyncMock(return_value=ScalarResult(scalars=[v1, v2])),
    )

    @asynccontextmanager
    async def fake_session_context():
        yield session

    monkeypatch.setattr(repo_module.pg_manager, "get_async_session_context", fake_session_context)

    versions = await KnowledgeFileRepository().list_versions(kb_id="kb-1", file_id="file-v1")

    assert [version.file_id for version in versions] == ["file-v1", "file-v2"]


@pytest.mark.asyncio
async def test_list_versions_falls_back_to_legacy_anchor(monkeypatch):
    # 存量数据：首版自身缺 logical_document_id（锚点断裂），候选 supersedes 指向首版。
    # 兜底查询应恢复全部版本，否则版本历史接口返回 404/空列表。
    v1 = SimpleNamespace(file_id="file-v1", document_version=None, is_current=True)
    v2 = SimpleNamespace(file_id="file-v2", document_version=2, is_current=False)
    session = SimpleNamespace(
        scalar=AsyncMock(return_value=None),
        execute=AsyncMock(return_value=ScalarResult(scalars=[v1, v2])),
    )

    @asynccontextmanager
    async def fake_session_context():
        yield session

    monkeypatch.setattr(repo_module.pg_manager, "get_async_session_context", fake_session_context)

    versions = await KnowledgeFileRepository().list_versions(kb_id="kb-1", file_id="file-v1")

    assert [version.file_id for version in versions] == ["file-v1", "file-v2"]
    fallback_query = str(session.execute.call_args.args[0])
    assert "supersedes_file_id" in fallback_query
    assert "logical_document_id" in fallback_query


@pytest.mark.asyncio
async def test_activate_candidate_backfills_legacy_current_anchor():
    # 存量数据：旧当前文件缺 logical_document_id（锚点断裂），候选已指向它。
    # 若不补齐，activate_candidate 的版本链查询查不到旧版会误报 VERSION_CHANGED。
    current = SimpleNamespace(
        file_id="file-v1",
        is_current=True,
        updated_by=None,
        updated_at=None,
    )
    candidate = SimpleNamespace(
        file_id="file-v2",
        logical_document_id="file-v1",
        supersedes_file_id="file-v1",
        is_current=False,
        status="validation_review",
        error_message="temporary candidate error",
        activated_at=None,
        updated_by=None,
        updated_at=None,
    )
    session = SimpleNamespace(
        execute=AsyncMock(
            side_effect=[
                ScalarResult(scalar=candidate),
                ScalarResult(scalars=[current, candidate]),
            ]
        ),
        flush=AsyncMock(),
    )

    archived, activated = await KnowledgeFileRepository().activate_candidate(
        kb_id="kb-1",
        candidate_file_id=candidate.file_id,
        expected_current_file_id=current.file_id,
        operator_id="admin",
        session=session,
    )

    assert archived is current
    assert activated is candidate
    # 激活时补齐旧版锚点，激活后 list_versions 仍能拉全版本链
    assert current.logical_document_id == "file-v1"
    assert current.document_version == 1
