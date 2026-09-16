from types import SimpleNamespace

import pytest

from yuxi.knowledge.base import KnowledgeBase


class FakeKnowledgeBase(KnowledgeBase):
    @property
    def kb_type(self) -> str:
        return "fake"

    async def _create_kb_instance(self, kb_id: str, config: dict):
        return None

    async def _initialize_kb_instance(self, instance) -> None:
        pass

    async def index_file(self, kb_id: str, file_id: str, operator_id: str | None = None) -> dict:
        return {}

    async def update_content(self, kb_id: str, file_ids: list[str], params: dict | None = None) -> list[dict]:
        return []

    async def aquery(self, query_text: str, kb_id: str, **kwargs) -> list[dict]:
        return []

    def get_query_params_config(self, kb_id: str, **kwargs) -> dict:
        return {"options": []}

    async def delete_file(self, kb_id: str, file_id: str) -> None:
        pass

    async def get_file_basic_info(self, kb_id: str, file_id: str) -> dict:
        return {}

    async def get_file_content(self, kb_id: str, file_id: str) -> dict:
        return {}

    async def get_file_info(self, kb_id: str, file_id: str) -> dict:
        return {}


class FakeFileRepo:
    """记录 update_fields_if_status 调用；可配置拒绝状态回退。"""

    def __init__(self, *, records_by_status=None, claim_result=None):
        self.status_claims = []
        self.records_by_status = records_by_status or {}
        self.claim_result = claim_result

    async def update_fields_if_status(self, *, kb_id, file_id, allowed_statuses, data):
        self.status_claims.append((kb_id, file_id, allowed_statuses, data))
        return self.claim_result

    async def get_by_file_id(self, file_id: str):
        return self.records_by_status.get(file_id)


def make_file_record(**overrides):
    data = {
        "file_id": "file-1",
        "kb_id": "db",
        "parent_id": None,
        "logical_document_id": "logical-1",
        "document_version": 1,
        "is_current": True,
        "supersedes_file_id": None,
        "activated_at": None,
        "filename": "demo.xlsx",
        "file_type": "xlsx",
        "path": "minio://knowledgebases/db/upload/demo.xlsx",
        "markdown_file": "minio://knowledgebases/db/parsed/file-1.md",
        "status": "indexed",
        "content_hash": "hash",
        "file_size": 123,
        "chunk_count": 0,
        "token_count": 0,
        "content_type": "file",
        "processing_params": {"ocr_engine": "disable"},
        "is_folder": False,
        "error_message": None,
        "created_by": "user",
        "updated_by": None,
        "created_at": None,
        "updated_at": None,
        "original_filename": None,
        "minio_url": None,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


@pytest.mark.asyncio
async def test_reparse_file_resets_status_then_parses(monkeypatch, tmp_path):
    kb = FakeKnowledgeBase(str(tmp_path))
    repo = FakeFileRepo(claim_result=make_file_record(status="uploaded"))

    parse_calls = []

    async def fake_parse_file(kb_id, file_id, operator_id=None):
        parse_calls.append((kb_id, file_id, operator_id))
        return {"file_id": file_id, "status": "parsed"}

    monkeypatch.setattr("yuxi.repositories.knowledge_file_repository.KnowledgeFileRepository", lambda: repo)
    monkeypatch.setattr(kb, "parse_file", fake_parse_file)

    result = await kb.reparse_file("db", "file-1", operator_id="user-2")

    assert result["status"] == "parsed"
    assert len(repo.status_claims) == 1
    kb_id, file_id, allowed_statuses, reset_data = repo.status_claims[0]
    assert (kb_id, file_id) == ("db", "file-1")
    assert allowed_statuses == {"parsed", "indexed", "error_indexing", "done"}
    assert reset_data["status"] == "uploaded"
    assert reset_data["markdown_file"] is None
    assert reset_data["updated_by"] == "user-2"
    assert parse_calls == [("db", "file-1", "user-2")]


@pytest.mark.asyncio
async def test_reparse_file_rejects_in_progress_status(monkeypatch, tmp_path):
    kb = FakeKnowledgeBase(str(tmp_path))
    repo = FakeFileRepo(
        claim_result=None,
        records_by_status={"file-1": make_file_record(status="parsing")},
    )

    async def fail_parse(*args, **kwargs):
        raise AssertionError("parse_file must not run when status reset is rejected")

    monkeypatch.setattr("yuxi.repositories.knowledge_file_repository.KnowledgeFileRepository", lambda: repo)
    monkeypatch.setattr(kb, "parse_file", fail_parse)

    with pytest.raises(ValueError, match="Cannot reparse file with status 'parsing'"):
        await kb.reparse_file("db", "file-1")
