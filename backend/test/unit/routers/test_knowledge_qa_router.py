from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from server.routers import knowledge_router
from yuxi.services.document_qa_service import QANotFound

pytestmark = pytest.mark.asyncio


async def _allow(*_args, **_kwargs):
    return None


async def test_qa_view_uses_can_view_and_readonly_uses_can_manage(monkeypatch):
    checks = []

    async def require_permission(_user, kb_id, action):
        checks.append(("required", kb_id, action))

    async def has_permission(_user, kb_id, action):
        checks.append(("optional", kb_id, action))
        return False

    class FakeService:
        async def list(self, *, kb_id, file_id):
            return {"kb_id": kb_id, "file_id": file_id, "items": []}

    monkeypatch.setattr(knowledge_router, "_require_kb_permission", require_permission)
    monkeypatch.setattr(knowledge_router, "_has_kb_permission", has_permission)
    monkeypatch.setattr(knowledge_router, "_ensure_database_supports_documents", _allow)
    monkeypatch.setattr(knowledge_router, "DocumentQAService", FakeService)

    result = await knowledge_router.list_document_qa(
        "kb-1",
        "file-1",
        current_user=SimpleNamespace(uid="viewer"),
    )

    assert result["readonly"] is True
    assert checks == [
        ("required", "kb-1", "can_view"),
        ("optional", "kb-1", "can_manage"),
    ]


async def test_qa_generate_edit_and_confirm_require_can_manage(monkeypatch):
    checks = []

    async def require_permission(_user, kb_id, action):
        checks.append((kb_id, action))

    async def enqueue(**kwargs):
        assert kwargs["selected_chunk_ids"] == ["chunk-1"]
        return "task-1", True

    class FakeService:
        async def update(self, **kwargs):
            return {"qa_id": kwargs["qa_id"], "version": kwargs["expected_version"] + 1}

        async def confirm(self, **kwargs):
            return {"qa_id": kwargs["qa_id"], "status": "confirmed"}

    monkeypatch.setattr(knowledge_router, "_require_kb_permission", require_permission)
    monkeypatch.setattr(knowledge_router, "_ensure_database_supports_documents", _allow)
    monkeypatch.setattr(knowledge_router, "enqueue_document_qa_generation", enqueue)
    monkeypatch.setattr(knowledge_router, "DocumentQAService", FakeService)

    generated = await knowledge_router.generate_document_qa(
        "kb-1",
        "file-1",
        knowledge_router.QAGenerateRequest(source_chunk_ids=["chunk-1"]),
        current_user=SimpleNamespace(uid="editor"),
    )
    write_request = knowledge_router.QAWriteRequest(
        question="问题",
        answer="答案说明",
        source_chunk_ids=["chunk-1"],
        evidence=[{"chunk_id": "chunk-1", "text": "答案说明"}],
        version=1,
    )
    updated = await knowledge_router.update_document_qa(
        "kb-1",
        "file-1",
        "qa-1",
        write_request,
        current_user=SimpleNamespace(uid="editor"),
    )
    confirmed = await knowledge_router.confirm_document_qa(
        "kb-1",
        "file-1",
        "qa-1",
        knowledge_router.QAVersionRequest(version=2),
        current_user=SimpleNamespace(uid="editor"),
    )

    assert generated["task_id"] == "task-1"
    assert updated["version"] == 2
    assert confirmed["status"] == "confirmed"
    assert checks == [("kb-1", "can_manage")] * 3


async def test_cross_knowledge_base_qa_returns_404(monkeypatch):
    class FakeService:
        async def list(self, **_kwargs):
            raise QANotFound("文档不存在")

    async def has_permission(*_args, **_kwargs):
        return True

    monkeypatch.setattr(knowledge_router, "_require_kb_permission", _allow)
    monkeypatch.setattr(knowledge_router, "_has_kb_permission", has_permission)
    monkeypatch.setattr(knowledge_router, "_ensure_database_supports_documents", _allow)
    monkeypatch.setattr(knowledge_router, "DocumentQAService", FakeService)

    with pytest.raises(HTTPException) as exc_info:
        await knowledge_router.list_document_qa(
            "kb-other",
            "file-1",
            current_user=SimpleNamespace(uid="viewer"),
        )

    assert exc_info.value.status_code == 404


async def test_qa_task_status_is_scoped_to_document_and_uses_can_view(monkeypatch):
    checks = []

    async def require_permission(_user, kb_id, action):
        checks.append((kb_id, action))

    class FakeTasker:
        async def get_task(self, _task_id):
            return {
                "type": "document_qa_generation",
                "status": "running",
                "progress": 50,
                "message": "正在生成",
                "error": None,
                "payload": {"kb_id": "kb-1", "file_id": "file-1"},
            }

    monkeypatch.setattr(knowledge_router, "_require_kb_permission", require_permission)
    monkeypatch.setattr(knowledge_router, "_ensure_database_supports_documents", _allow)
    monkeypatch.setattr(knowledge_router, "tasker", FakeTasker())

    result = await knowledge_router.get_document_qa_generation_task(
        "kb-1",
        "file-1",
        "task-1",
        current_user=SimpleNamespace(uid="viewer"),
    )

    assert result["status"] == "running"
    assert result["progress"] == 50
    assert checks == [("kb-1", "can_view")]

    with pytest.raises(HTTPException) as exc_info:
        await knowledge_router.get_document_qa_generation_task(
            "kb-1",
            "other-file",
            "task-1",
            current_user=SimpleNamespace(uid="viewer"),
        )
    assert exc_info.value.status_code == 404
