from types import SimpleNamespace

import pytest

from yuxi.knowledge.manager import KnowledgeBaseManager
from yuxi.services.document_cleaning_service import DocumentCleaningError, DocumentCleaningService
from yuxi.utils.datetime_utils import utc_now_naive

pytestmark = pytest.mark.asyncio


class FakeFileRepository:
    record = None

    async def get_by_file_id(self, _file_id):
        return self.record


class FakeKnowledgeBase:
    def __init__(self):
        self.calls = []

    async def parse_file(self, *args):
        self.calls.append(("parse", args))
        return {"status": "parsed"}

    async def reparse_file(self, *args):
        self.calls.append(("reparse", args))
        return {"status": "parsed"}

    async def index_file(self, *args, **kwargs):
        self.calls.append(("index", args, kwargs))
        return {"status": "indexed"}


def record(*, flowchart: bool, confirmed: bool = False):
    return SimpleNamespace(
        file_id="file-1",
        kb_id="kb-1",
        processing_params={"ingestion_type": "flowchart"} if flowchart else {},
        confirmed_at=utc_now_naive() if confirmed else None,
    )


async def test_unconfirmed_flowchart_cannot_use_ordinary_index(monkeypatch, tmp_path):
    from yuxi.repositories import knowledge_file_repository as repository_module

    FakeFileRepository.record = record(flowchart=True)
    monkeypatch.setattr(repository_module, "KnowledgeFileRepository", FakeFileRepository)
    manager = KnowledgeBaseManager(str(tmp_path))
    backend = FakeKnowledgeBase()
    monkeypatch.setattr(manager, "_get_kb_for_database", lambda _kb_id: _async_value(backend))

    with pytest.raises(ValueError, match="尚未确认"):
        await manager.index_file("kb-1", "file-1", operator_id="user-1")
    assert backend.calls == []


async def test_flowchart_cannot_use_ordinary_parse_or_reparse(monkeypatch, tmp_path):
    from yuxi.repositories import knowledge_file_repository as repository_module

    FakeFileRepository.record = record(flowchart=True)
    monkeypatch.setattr(repository_module, "KnowledgeFileRepository", FakeFileRepository)
    manager = KnowledgeBaseManager(str(tmp_path))
    backend = FakeKnowledgeBase()
    monkeypatch.setattr(manager, "_get_kb_for_database", lambda _kb_id: _async_value(backend))

    with pytest.raises(ValueError, match="普通文档解析"):
        await manager.parse_file("kb-1", "file-1")
    with pytest.raises(ValueError, match="普通文档重新解析"):
        await manager.reparse_file("kb-1", "file-1")
    assert backend.calls == []


async def test_ordinary_document_behavior_is_unchanged(monkeypatch, tmp_path):
    from yuxi.repositories import knowledge_file_repository as repository_module

    FakeFileRepository.record = record(flowchart=False)
    monkeypatch.setattr(repository_module, "KnowledgeFileRepository", FakeFileRepository)
    manager = KnowledgeBaseManager(str(tmp_path))
    backend = FakeKnowledgeBase()
    monkeypatch.setattr(manager, "_get_kb_for_database", lambda _kb_id: _async_value(backend))

    assert (await manager.parse_file("kb-1", "file-1"))["status"] == "parsed"
    assert (await manager.reparse_file("kb-1", "file-1"))["status"] == "parsed"
    assert (await manager.index_file("kb-1", "file-1"))["status"] == "indexed"
    assert [call[0] for call in backend.calls] == ["parse", "reparse", "index"]


async def test_confirmed_flowchart_can_retry_existing_index_entry(monkeypatch, tmp_path):
    from yuxi.repositories import knowledge_file_repository as repository_module

    FakeFileRepository.record = record(flowchart=True, confirmed=True)
    monkeypatch.setattr(repository_module, "KnowledgeFileRepository", FakeFileRepository)
    manager = KnowledgeBaseManager(str(tmp_path))
    backend = FakeKnowledgeBase()
    monkeypatch.setattr(manager, "_get_kb_for_database", lambda _kb_id: _async_value(backend))

    result = await manager.index_file("kb-1", "file-1", operator_id="admin")

    assert result["status"] == "indexed"
    assert backend.calls[0][0] == "index"


async def test_flowchart_cannot_use_ordinary_cleaning_entry():
    with pytest.raises(DocumentCleaningError, match="普通文档清洗"):
        DocumentCleaningService._reject_flowchart_cleaning(record(flowchart=True, confirmed=True))


async def test_confirmed_flowchart_cannot_use_ordinary_edit_entry(monkeypatch, tmp_path):
    from yuxi.repositories import knowledge_file_repository as repository_module

    FakeFileRepository.record = record(flowchart=True, confirmed=True)
    monkeypatch.setattr(repository_module, "KnowledgeFileRepository", FakeFileRepository)
    manager = KnowledgeBaseManager(str(tmp_path))

    with pytest.raises(ValueError, match="普通编辑入口"):
        await manager.replace_document_content(
            "kb-1",
            "file-1",
            b"content",
            "edited.docx",
            operator_id="admin",
        )


async def _async_value(value):
    return value
