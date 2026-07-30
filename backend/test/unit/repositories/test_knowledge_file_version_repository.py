from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

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
