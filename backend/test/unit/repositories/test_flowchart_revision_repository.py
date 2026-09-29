from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from yuxi.repositories import knowledge_file_repository as repository_module
from yuxi.repositories.knowledge_file_repository import (
    FlowchartRevisionConflict,
    KnowledgeFileRepository,
)

pytestmark = pytest.mark.asyncio


class ScalarResult:
    def __init__(self, record):
        self.record = record

    def scalar_one_or_none(self):
        return self.record


class FakeSession:
    def __init__(self, record):
        self.record = record
        self.used_for_update = False
        self.flush_count = 0

    async def execute(self, statement):
        self.used_for_update = statement._for_update_arg is not None
        return ScalarResult(self.record)

    async def flush(self):
        self.flush_count += 1


class FakePgManager:
    def __init__(self, session):
        self.session = session

    @asynccontextmanager
    async def get_async_session_context(self):
        yield self.session


def make_record():
    return SimpleNamespace(
        file_id="file-flow",
        kb_id="kb-1",
        is_folder=False,
        processing_params={"ingestion_type": "flowchart"},
        parse_metadata={"flowchart": {"revision": 3}},
        status="flowchart_waiting_confirmation",
        confirmed_at=None,
        updated_at=None,
        markdown_file="old.md",
    )


async def test_repository_flowchart_cas_uses_row_lock_and_increments_revision(monkeypatch):
    record = make_record()
    session = FakeSession(record)
    monkeypatch.setattr(repository_module, "pg_manager", FakePgManager(session))

    result = await KnowledgeFileRepository().update_flowchart_with_revision(
        kb_id="kb-1",
        file_id="file-flow",
        expected_revision=3,
        allowed_statuses={"flowchart_waiting_confirmation"},
        data={"markdown_file": "new.md"},
        metadata_updates={"manually_edited": True},
    )

    assert session.used_for_update is True
    assert session.flush_count == 1
    assert result.record.markdown_file == "new.md"
    assert result.record.parse_metadata["flowchart"] == {"revision": 4, "manually_edited": True}


async def test_repository_flowchart_cas_rejects_stale_revision_without_write(monkeypatch):
    record = make_record()
    session = FakeSession(record)
    monkeypatch.setattr(repository_module, "pg_manager", FakePgManager(session))

    with pytest.raises(FlowchartRevisionConflict):
        await KnowledgeFileRepository().update_flowchart_with_revision(
            kb_id="kb-1",
            file_id="file-flow",
            expected_revision=2,
            data={"markdown_file": "new.md"},
        )

    assert session.used_for_update is True
    assert session.flush_count == 0
    assert record.markdown_file == "old.md"
    assert record.parse_metadata["flowchart"]["revision"] == 3
