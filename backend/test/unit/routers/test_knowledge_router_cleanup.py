from inspect import signature
from io import BytesIO
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException, UploadFile
from httpx import ASGITransport, AsyncClient

from server.routers import knowledge_router

pytestmark = pytest.mark.asyncio


class FakeTaskContext:
    def __init__(self):
        self.result = None

    async def set_message(self, message: str) -> None:
        return None

    async def set_progress(self, progress: float, message: str | None = None) -> None:
        return None

    async def set_result(self, result: dict) -> None:
        self.result = result

    async def raise_if_cancelled(self) -> None:
        return None


def user(uid: str):
    return SimpleNamespace(uid=uid, role="admin", department_id=1)


@pytest.fixture(autouse=True)
def allow_knowledge_permissions(monkeypatch):
    class FakePermissionService:
        async def has_permission(self, _user, _kb_id, _action):
            return True

    monkeypatch.setattr(knowledge_router, "KnowledgePermissionService", FakePermissionService)


async def test_upload_file_does_not_expose_legacy_allow_jsonl_query():
    assert "allow_jsonl" not in signature(knowledge_router.upload_file).parameters


async def test_document_file_exists_returns_boolean_for_relative_path(monkeypatch):
    captured = {}

    async def fake_ensure_database_supports_documents(kb_id: str, operation: str) -> None:
        captured["ensure"] = (kb_id, operation)

    async def fake_document_file_exists(kb_id: str, filename: str) -> bool:
        captured["exists"] = (kb_id, filename)
        return True

    monkeypatch.setattr(
        knowledge_router,
        "_ensure_database_supports_documents",
        fake_ensure_database_supports_documents,
    )
    monkeypatch.setattr(knowledge_router.knowledge_base, "document_file_exists", fake_document_file_exists)

    result = await knowledge_router.document_file_exists(
        "kb_1",
        filename=" google_drive/shared_drives/engineering/playbook.txt ",
        current_user=user("user_1"),
    )

    assert result == {
        "kb_id": "kb_1",
        "filename": "google_drive/shared_drives/engineering/playbook.txt",
        "exists": True,
    }
    assert captured == {
        "ensure": ("kb_1", "文档存在性检查"),
        "exists": ("kb_1", "google_drive/shared_drives/engineering/playbook.txt"),
    }


async def test_document_file_exists_route_accepts_filename_with_slashes(monkeypatch):
    async def fake_admin_user():
        return user("user_1")

    async def fake_ensure_database_supports_documents(kb_id: str, operation: str) -> None:
        return None

    async def fake_document_file_exists(kb_id: str, filename: str) -> bool:
        assert kb_id == "kb_1"
        assert filename == "google_drive/shared_drives/engineering/playbook.txt"
        return True

    monkeypatch.setattr(
        knowledge_router,
        "_ensure_database_supports_documents",
        fake_ensure_database_supports_documents,
    )
    monkeypatch.setattr(knowledge_router.knowledge_base, "document_file_exists", fake_document_file_exists)

    app = FastAPI()
    app.include_router(knowledge_router.knowledge, prefix="/api")
    app.dependency_overrides[knowledge_router.get_required_user] = fake_admin_user

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/api/knowledge/databases/kb_1/documents/exists",
            params={"filename": "google_drive/shared_drives/engineering/playbook.txt"},
        )

    assert response.status_code == 200, response.text
    assert response.json() == {
        "kb_id": "kb_1",
        "filename": "google_drive/shared_drives/engineering/playbook.txt",
        "exists": True,
    }


async def test_document_file_exists_rejects_blank_filename(monkeypatch):
    async def fake_ensure_database_supports_documents(kb_id: str, operation: str) -> None:
        return None

    monkeypatch.setattr(
        knowledge_router,
        "_ensure_database_supports_documents",
        fake_ensure_database_supports_documents,
    )

    with pytest.raises(HTTPException) as exc_info:
        await knowledge_router.document_file_exists(
            "kb_1",
            filename="   ",
            current_user=user("user_1"),
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "filename is required"


async def test_upload_file_rejects_jsonl_uploads():
    upload = UploadFile(filename="dataset.jsonl", file=BytesIO(b'{"query":"hello"}\n'))

    with pytest.raises(HTTPException) as exc_info:
        await knowledge_router.upload_file(upload, kb_id=None, duplicate_strategy="prompt", current_user=user("user_1"))

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Unsupported file type: .jsonl"


async def test_upload_file_rejects_oversized_file(monkeypatch):
    monkeypatch.setattr(knowledge_router, "MAX_UPLOAD_SIZE_BYTES", 5)

    async def fake_ensure_database_supports_documents(kb_id: str, operation: str) -> None:
        return None

    monkeypatch.setattr(
        knowledge_router,
        "_ensure_database_supports_documents",
        fake_ensure_database_supports_documents,
    )

    upload = UploadFile(filename="demo.txt", file=BytesIO(b"123456"))

    with pytest.raises(HTTPException) as exc_info:
        await knowledge_router.upload_file(upload, kb_id="kb_1", duplicate_strategy="prompt", current_user=user("user_1"))

    assert exc_info.value.status_code == 400
    assert "100 MB" in exc_info.value.detail


async def test_upload_file_invalid_kb_fails_before_read_or_minio(monkeypatch):
    calls = {"read": 0, "upload": 0}

    async def fake_ensure_database_supports_documents(kb_id: str, operation: str) -> None:
        raise HTTPException(status_code=404, detail=f"知识库 {kb_id} 不存在")

    async def fake_read_upload_with_limit(*_args, **_kwargs) -> bytes:
        calls["read"] += 1
        return b"demo"

    async def fake_upload_to_minio(*_args, **_kwargs) -> str:
        calls["upload"] += 1
        return "minio://knowledgebases/kb_1/upload/demo.txt"

    monkeypatch.setattr(
        knowledge_router,
        "_ensure_database_supports_documents",
        fake_ensure_database_supports_documents,
    )
    monkeypatch.setattr(knowledge_router, "read_upload_with_limit", fake_read_upload_with_limit)
    monkeypatch.setattr(knowledge_router, "aupload_file_to_minio", fake_upload_to_minio)

    upload = UploadFile(filename="demo.txt", file=BytesIO(b"demo"))

    with pytest.raises(HTTPException) as exc_info:
        await knowledge_router.upload_file(upload, kb_id="missing", duplicate_strategy="prompt", current_user=user("user_1"))

    assert exc_info.value.status_code == 404
    assert calls == {"read": 0, "upload": 0}


async def test_upload_file_read_only_kb_fails_before_read_or_minio(monkeypatch):
    calls = {"read": 0, "upload": 0}

    async def fake_ensure_database_supports_documents(kb_id: str, operation: str) -> None:
        raise HTTPException(status_code=400, detail="只支持检索，不支持文档上传")

    async def fake_read_upload_with_limit(*_args, **_kwargs) -> bytes:
        calls["read"] += 1
        return b"demo"

    async def fake_upload_to_minio(*_args, **_kwargs) -> str:
        calls["upload"] += 1
        return "minio://knowledgebases/kb_1/upload/demo.txt"

    monkeypatch.setattr(
        knowledge_router,
        "_ensure_database_supports_documents",
        fake_ensure_database_supports_documents,
    )
    monkeypatch.setattr(knowledge_router, "read_upload_with_limit", fake_read_upload_with_limit)
    monkeypatch.setattr(knowledge_router, "aupload_file_to_minio", fake_upload_to_minio)

    upload = UploadFile(filename="demo.txt", file=BytesIO(b"demo"))

    with pytest.raises(HTTPException) as exc_info:
        await knowledge_router.upload_file(upload, kb_id="readonly", duplicate_strategy="prompt", current_user=user("user_1"))

    assert exc_info.value.status_code == 400
    assert calls == {"read": 0, "upload": 0}


async def test_markdown_endpoint_rejects_oversized_file(monkeypatch):
    monkeypatch.setattr(knowledge_router, "MAX_UPLOAD_SIZE_BYTES", 5)
    upload = UploadFile(filename="demo.txt", file=BytesIO(b"123456"))

    with pytest.raises(HTTPException) as exc_info:
        await knowledge_router.mark_it_down(upload, current_user=user("user_1"))

    assert exc_info.value.status_code == 400
    assert "100 MB" in exc_info.value.detail


async def test_save_edited_document_serializes_and_replaces(monkeypatch):
    class FakePermissionService:
        async def has_permission(self, user, kb_id, action):
            assert action == "can_manage"
            return True

    captured = {}

    def fake_serialize(content_type, payload):
        captured["content_type"] = content_type
        return b"docx-bytes"

    async def fake_replace(kb_id, doc_id, content_bytes, filename, operator_id=None, params=None):
        captured["doc_id"] = doc_id
        captured["bytes"] = content_bytes
        captured["filename"] = filename
        return "file_new"

    monkeypatch.setattr(knowledge_router, "KnowledgePermissionService", FakePermissionService)
    monkeypatch.setattr(knowledge_router, "serialize_edited_content", fake_serialize)
    monkeypatch.setattr(knowledge_router.knowledge_base, "replace_document_content", fake_replace)

    result = await knowledge_router.save_edited_document(
        "kb-1",
        "file_old",
        knowledge_router.SaveEditedDocumentRequest(
            content_type="docx",
            blocks=[{"kind": "para", "text": "编辑后"}],
            filename="edited.docx",
        ),
        current_user=user("user-1"),
    )

    assert captured["doc_id"] == "file_old"
    assert captured["bytes"] == b"docx-bytes"
    assert captured["filename"] == "edited.docx"
    assert result == {"message": "文档已更新并重新入库", "file_id": "file_new"}


async def test_save_edited_document_rejects_bad_type(monkeypatch):
    class FakePermissionService:
        async def has_permission(self, user, kb_id, action):
            return True

    monkeypatch.setattr(knowledge_router, "KnowledgePermissionService", FakePermissionService)

    with pytest.raises(HTTPException) as exc_info:
        await knowledge_router.save_edited_document(
            "kb-1",
            "file_old",
            knowledge_router.SaveEditedDocumentRequest(
                content_type="pdf",
                blocks=[],
                filename="x.pdf",
            ),
            current_user=user("user-1"),
        )

    assert exc_info.value.status_code == 400


async def test_office_extract_route_uses_file_path(monkeypatch):
    class FakePermissionService:
        async def has_permission(self, user, kb_id, action):
            assert action == "can_view"
            return True

    captured = {}

    async def fake_extract(kb_id, file_path, filename):
        captured["file_path"] = file_path
        captured["filename"] = filename
        return {"type": "docx", "blocks": [{"kind": "para", "text": "x"}]}

    monkeypatch.setattr(knowledge_router, "KnowledgePermissionService", FakePermissionService)
    monkeypatch.setattr(knowledge_router, "extract_office_content", fake_extract)

    result = await knowledge_router.extract_office_content_upload(
        "kb-1",
        knowledge_router.OfficeExtractRequest(file_path="minio://kb/x.docx", filename="x.docx"),
        current_user=user("user-1"),
    )

    assert captured["file_path"] == "minio://kb/x.docx"
    assert result["type"] == "docx"


async def test_office_writeback_uploads_and_returns_file_path(monkeypatch):
    class FakePermissionService:
        async def has_permission(self, user, kb_id, action):
            assert action == "can_upload"
            return True

    captured = {}

    def fake_serialize(content_type, payload):
        captured["content_type"] = content_type
        return b"docx-bytes"

    async def fake_upload(kb_id, content_bytes, filename):
        captured["bytes"] = content_bytes
        return "minio://kb/new.docx"

    monkeypatch.setattr(knowledge_router, "KnowledgePermissionService", FakePermissionService)
    monkeypatch.setattr(knowledge_router, "serialize_edited_content", fake_serialize)
    monkeypatch.setattr(knowledge_router.knowledge_base, "upload_office_bytes", fake_upload)

    result = await knowledge_router.office_writeback(
        "kb-1",
        knowledge_router.OfficeWritebackRequest(
            content_type="docx",
            blocks=[{"kind": "para", "text": "编辑后"}],
            filename="x.docx",
        ),
        current_user=user("user-1"),
    )

    assert captured["bytes"] == b"docx-bytes"
    assert result["file_path"] == "minio://kb/new.docx"
    assert result["content_hash"]


async def test_clean_document_route_requires_manage_and_returns_cleaned(monkeypatch):
    async def fake_clean(raw: str) -> dict:
        return {"cleaned_markdown": "# 清洗结果", "warnings": []}

    class FakePermissionService:
        async def has_permission(self, user, kb_id, action):
            assert action == "can_manage"
            return True

    monkeypatch.setattr(knowledge_router, "KnowledgePermissionService", FakePermissionService)
    monkeypatch.setattr(knowledge_router, "clean_document_markdown", fake_clean)

    result = await knowledge_router.clean_document_markdown_route(
        "kb-1",
        knowledge_router.DocumentCleanRequest(markdown="混乱文本", filename="demo.txt"),
        current_user=user("user-1"),
    )

    assert result == {
        "cleaned_markdown": "# 清洗结果",
        "filename": "demo.txt",
        "warnings": [],
    }


async def test_clean_document_batch_route_parallel_with_failure_isolation(monkeypatch):
    class FakePermissionService:
        async def has_permission(self, user, kb_id, action):
            assert action == "can_manage"
            return True

    async def fake_clean_file(kb_id, file_path, filename=None):
        if file_path == "bad":
            raise RuntimeError("boom")
        return {"cleaned_markdown": f"cleaned:{file_path}", "warnings": []}

    monkeypatch.setattr(knowledge_router, "KnowledgePermissionService", FakePermissionService)
    monkeypatch.setattr(knowledge_router, "clean_document_file", fake_clean_file)

    result = await knowledge_router.clean_document_batch_route(
        "kb-1",
        knowledge_router.DocumentCleanBatchRequest(
            items=[
                knowledge_router.DocumentCleanBatchItem(file_path="a", filename="a.txt"),
                knowledge_router.DocumentCleanBatchItem(file_path="bad", filename="bad.txt"),
                knowledge_router.DocumentCleanBatchItem(file_path="c", filename="c.txt"),
            ]
        ),
        current_user=user("user-1"),
    )

    assert result["results"][0]["cleaned_markdown"] == "cleaned:a"
    assert result["results"][0]["error"] is None
    assert result["results"][1]["file_path"] == "bad"
    assert result["results"][1]["cleaned_markdown"] == ""
    assert result["results"][1]["error"] is not None
    assert result["results"][2]["cleaned_markdown"] == "cleaned:c"


async def test_clean_document_batch_route_rejects_without_permission(monkeypatch):
    class FakePermissionService:
        async def has_permission(self, user, kb_id, action):
            return action != "can_manage"

    monkeypatch.setattr(knowledge_router, "KnowledgePermissionService", FakePermissionService)

    with pytest.raises(HTTPException) as exc_info:
        await knowledge_router.clean_document_batch_route(
            "kb-1",
            knowledge_router.DocumentCleanBatchRequest(
                items=[knowledge_router.DocumentCleanBatchItem(file_path="a")]
            ),
            current_user=user("user-1"),
        )

    assert exc_info.value.status_code == 403


async def test_clean_document_route_rejects_without_manage_permission(monkeypatch):
    class FakePermissionService:
        async def has_permission(self, user, kb_id, action):
            return action != "can_manage"

    monkeypatch.setattr(knowledge_router, "KnowledgePermissionService", FakePermissionService)

    with pytest.raises(HTTPException) as exc_info:
        await knowledge_router.clean_document_markdown_route(
            "kb-1",
            knowledge_router.DocumentCleanRequest(markdown="x", filename=None),
            current_user=user("user-1"),
        )

    assert exc_info.value.status_code == 403


async def test_index_documents_uses_uid_for_operator(monkeypatch):
    captured = {}

    async def fake_ensure_database_supports_documents(kb_id: str, operation: str) -> None:
        return None

    async def fake_get_database_info(kb_id: str) -> dict:
        return {"name": "测试知识库"}

    async def fake_index_file(kb_id: str, file_id: str, operator_id: str | None = None, params: dict | None = None):
        captured["operator_id"] = operator_id
        return {"file_id": file_id, "status": "indexed"}

    async def fake_enqueue(name: str, task_type: str, payload: dict, coroutine):
        await coroutine(FakeTaskContext())
        return SimpleNamespace(id="task_1")

    monkeypatch.setattr(
        knowledge_router,
        "_ensure_database_supports_documents",
        fake_ensure_database_supports_documents,
    )
    monkeypatch.setattr(knowledge_router.knowledge_base, "get_database_info", fake_get_database_info)
    monkeypatch.setattr(knowledge_router.knowledge_base, "index_file", fake_index_file)
    monkeypatch.setattr(knowledge_router.tasker, "enqueue", fake_enqueue)

    result = await knowledge_router.index_documents(
        "kb_1",
        ["file_1"],
        params={},
        current_user=SimpleNamespace(id="numeric-id", **vars(user("uid-user"))),
    )

    assert result["status"] == "queued"
    assert captured["operator_id"] == "uid-user"


async def test_parse_documents_rejects_oversized_direct_batch():
    file_ids = [f"file_{index}" for index in range(knowledge_router.MAX_DIRECT_DOCUMENT_ACTION_FILE_IDS + 1)]

    with pytest.raises(HTTPException) as exc_info:
        await knowledge_router.parse_documents(
            "kb_1",
            file_ids,
            current_user=user("uid-user"),
        )

    assert exc_info.value.status_code == 400
    assert str(knowledge_router.MAX_DIRECT_DOCUMENT_ACTION_FILE_IDS) in exc_info.value.detail


async def test_parse_pending_documents_enqueues_status_scoped_task(monkeypatch):
    captured = {"list_calls": [], "parsed": []}

    async def fake_ensure_database_supports_documents(kb_id: str, operation: str) -> None:
        captured["ensure"] = (kb_id, operation)

    async def fake_get_database_info(kb_id: str) -> dict:
        return {"name": "测试知识库", "stats": {"pending_parse_count": 2}}

    async def fake_list_document_file_ids_by_statuses(kb_id: str, *, statuses, after_file_id, limit):
        captured["list_calls"].append(
            {"kb_id": kb_id, "statuses": statuses, "after_file_id": after_file_id, "limit": limit}
        )
        return ["file_1", "file_2"] if after_file_id is None else []

    async def fake_parse_file(kb_id: str, file_id: str, operator_id: str | None = None):
        captured["parsed"].append({"kb_id": kb_id, "file_id": file_id, "operator_id": operator_id})
        return {"file_id": file_id, "status": "parsed"}

    async def fake_enqueue_unique_by_payload(**kwargs):
        captured["payload"] = kwargs["payload"]
        captured["payload_match"] = kwargs["payload_match"]
        captured["statuses"] = kwargs["statuses"]
        await kwargs["coroutine"](FakeTaskContext())
        return SimpleNamespace(id="task_1"), True

    monkeypatch.setattr(
        knowledge_router,
        "_ensure_database_supports_documents",
        fake_ensure_database_supports_documents,
    )
    monkeypatch.setattr(knowledge_router.knowledge_base, "get_database_info", fake_get_database_info)
    monkeypatch.setattr(
        knowledge_router.knowledge_base,
        "list_document_file_ids_by_statuses",
        fake_list_document_file_ids_by_statuses,
    )
    monkeypatch.setattr(knowledge_router.knowledge_base, "parse_file", fake_parse_file)
    monkeypatch.setattr(knowledge_router.tasker, "enqueue_unique_by_payload", fake_enqueue_unique_by_payload)

    result = await knowledge_router.parse_pending_documents(
        "kb_1",
        current_user=user("uid-user"),
    )

    assert result["status"] == "queued"
    assert result["task_id"] == "task_1"
    assert captured["ensure"] == ("kb_1", "文档解析")
    assert captured["payload_match"] == {"kb_id": "kb_1", "scope": "pending", "action": "parse"}
    assert captured["statuses"] == knowledge_router.ACTIVE_DOCUMENT_ACTION_TASK_STATUSES
    assert captured["payload"]["statuses"] == knowledge_router.PENDING_PARSE_STATUSES
    assert captured["list_calls"] == [
        {
            "kb_id": "kb_1",
            "statuses": knowledge_router.PENDING_PARSE_STATUSES,
            "after_file_id": None,
            "limit": knowledge_router.DOCUMENT_ACTION_BATCH_SIZE,
        },
        {
            "kb_id": "kb_1",
            "statuses": knowledge_router.PENDING_PARSE_STATUSES,
            "after_file_id": "file_2",
            "limit": knowledge_router.DOCUMENT_ACTION_BATCH_SIZE,
        },
    ]
    assert captured["parsed"] == [
        {"kb_id": "kb_1", "file_id": "file_1", "operator_id": "uid-user"},
        {"kb_id": "kb_1", "file_id": "file_2", "operator_id": "uid-user"},
    ]


async def test_index_pending_documents_uses_pending_statuses_and_params(monkeypatch):
    captured = {"list_calls": [], "updated": [], "indexed": []}

    async def fake_ensure_database_supports_documents(kb_id: str, operation: str) -> None:
        captured["ensure"] = (kb_id, operation)

    async def fake_get_database_info(kb_id: str) -> dict:
        return {"name": "测试知识库", "stats": {"pending_index_count": 2}}

    async def fake_list_document_file_ids_by_statuses(kb_id: str, *, statuses, after_file_id, limit):
        captured["list_calls"].append(
            {"kb_id": kb_id, "statuses": statuses, "after_file_id": after_file_id, "limit": limit}
        )
        return ["file_1", "file_2"] if after_file_id is None else []

    async def fake_update_file_params(kb_id: str, file_id: str, params: dict, operator_id: str | None = None):
        captured["updated"].append({"kb_id": kb_id, "file_id": file_id, "params": params, "operator_id": operator_id})

    async def fake_index_file(kb_id: str, file_id: str, operator_id: str | None = None, params: dict | None = None):
        captured["indexed"].append({"kb_id": kb_id, "file_id": file_id, "operator_id": operator_id, "params": params})
        return {"file_id": file_id, "status": "indexed"}

    async def fake_enqueue_unique_by_payload(**kwargs):
        captured["payload"] = kwargs["payload"]
        captured["payload_match"] = kwargs["payload_match"]
        await kwargs["coroutine"](FakeTaskContext())
        return SimpleNamespace(id="task_1"), True

    monkeypatch.setattr(
        knowledge_router,
        "_ensure_database_supports_documents",
        fake_ensure_database_supports_documents,
    )
    monkeypatch.setattr(knowledge_router.knowledge_base, "get_database_info", fake_get_database_info)
    monkeypatch.setattr(
        knowledge_router.knowledge_base,
        "list_document_file_ids_by_statuses",
        fake_list_document_file_ids_by_statuses,
    )
    monkeypatch.setattr(knowledge_router.knowledge_base, "update_file_params", fake_update_file_params)
    monkeypatch.setattr(knowledge_router.knowledge_base, "index_file", fake_index_file)
    monkeypatch.setattr(knowledge_router.tasker, "enqueue_unique_by_payload", fake_enqueue_unique_by_payload)

    params = {"chunk_preset_id": "general"}
    result = await knowledge_router.index_pending_documents(
        "kb_1",
        payload=knowledge_router.PendingIndexDocumentsRequest(params=params),
        current_user=user("uid-user"),
    )

    assert result["status"] == "queued"
    assert captured["ensure"] == ("kb_1", "文档入库")
    assert captured["payload_match"] == {"kb_id": "kb_1", "scope": "pending", "action": "index"}
    assert captured["payload"]["statuses"] == knowledge_router.PENDING_INDEX_STATUSES
    assert captured["payload"]["params"] == params
    assert captured["list_calls"][0]["statuses"] == knowledge_router.PENDING_INDEX_STATUSES
    assert captured["updated"] == [
        {"kb_id": "kb_1", "file_id": "file_1", "params": params, "operator_id": "uid-user"},
        {"kb_id": "kb_1", "file_id": "file_2", "params": params, "operator_id": "uid-user"},
    ]
    assert captured["indexed"] == [
        {"kb_id": "kb_1", "file_id": "file_1", "operator_id": "uid-user", "params": params},
        {"kb_id": "kb_1", "file_id": "file_2", "operator_id": "uid-user", "params": params},
    ]


async def test_add_documents_auto_index_returns_one_final_result_per_item(monkeypatch):
    context = FakeTaskContext()
    item = "minio://knowledgebases/kb_1/upload/demo.txt"

    async def fake_ensure_database_supports_documents(kb_id: str, operation: str) -> None:
        return None

    async def fake_get_database_info(kb_id: str) -> dict:
        return {"name": "测试知识库"}

    async def fake_create_uploaded_document(self, *, kb_id, item, params, operator_id):
        return SimpleNamespace(
            action="created",
            file_meta={"file_id": "file_1", "status": "uploaded"},
            existing_file_id=None,
            cleanup_pending=False,
        )

    async def fake_parse_file(kb_id: str, file_id: str, operator_id: str | None = None):
        return {"file_id": file_id, "status": "parsed"}

    async def fake_update_file_params(kb_id: str, file_id: str, params: dict, operator_id: str | None = None):
        return None

    async def fake_index_file(kb_id: str, file_id: str, operator_id: str | None = None, params: dict | None = None):
        return {"file_id": file_id, "status": "indexed"}

    async def fake_enqueue(name: str, task_type: str, payload: dict, coroutine):
        await coroutine(context)
        return SimpleNamespace(id="task_1")

    monkeypatch.setattr(
        knowledge_router,
        "_ensure_database_supports_documents",
        fake_ensure_database_supports_documents,
    )
    monkeypatch.setattr(knowledge_router.knowledge_base, "get_database_info", fake_get_database_info)
    monkeypatch.setattr(
        "yuxi.services.document_ingestion_service.DocumentIngestionService.create_uploaded_document",
        fake_create_uploaded_document,
    )
    monkeypatch.setattr(knowledge_router.knowledge_base, "parse_file", fake_parse_file)
    monkeypatch.setattr(knowledge_router.knowledge_base, "update_file_params", fake_update_file_params)
    monkeypatch.setattr(knowledge_router.knowledge_base, "index_file", fake_index_file)
    monkeypatch.setattr(knowledge_router.tasker, "enqueue", fake_enqueue)

    result = await knowledge_router.add_documents(
        "kb_1",
        [item],
        params={"content_type": "file", "auto_index": True, "content_hashes": {item: "hash_1"}},
        current_user=user("uid-user"),
    )

    assert result["status"] == "queued"
    assert context.result["submitted"] == 1
    assert context.result["failed"] == 0
    assert context.result["items"] == [{"file_id": "file_1", "status": "indexed"}]


async def test_add_documents_auto_index_treats_error_none_as_success(monkeypatch):
    """成功入库的文件元数据会携带 error=None，不应被统计为失败 (#793)。"""
    context = FakeTaskContext()
    item = "minio://knowledgebases/kb_1/upload/demo.txt"

    async def fake_ensure_database_supports_documents(kb_id: str, operation: str) -> None:
        return None

    async def fake_get_database_info(kb_id: str) -> dict:
        return {"name": "测试知识库"}

    async def fake_create_uploaded_document(self, *, kb_id, item, params, operator_id):
        return SimpleNamespace(
            action="created",
            file_meta={"file_id": "file_1", "status": "uploaded"},
            existing_file_id=None,
            cleanup_pending=False,
        )

    async def fake_parse_file(kb_id: str, file_id: str, operator_id: str | None = None):
        return {"file_id": file_id, "status": "parsed", "error": None}

    async def fake_update_file_params(kb_id: str, file_id: str, params: dict, operator_id: str | None = None):
        return None

    async def fake_index_file(kb_id: str, file_id: str, operator_id: str | None = None, params: dict | None = None):
        return {"file_id": file_id, "status": "indexed", "error": None}

    async def fake_enqueue(name: str, task_type: str, payload: dict, coroutine):
        await coroutine(context)
        return SimpleNamespace(id="task_1")

    monkeypatch.setattr(
        knowledge_router,
        "_ensure_database_supports_documents",
        fake_ensure_database_supports_documents,
    )
    monkeypatch.setattr(knowledge_router.knowledge_base, "get_database_info", fake_get_database_info)
    monkeypatch.setattr(
        "yuxi.services.document_ingestion_service.DocumentIngestionService.create_uploaded_document",
        fake_create_uploaded_document,
    )
    monkeypatch.setattr(knowledge_router.knowledge_base, "parse_file", fake_parse_file)
    monkeypatch.setattr(knowledge_router.knowledge_base, "update_file_params", fake_update_file_params)
    monkeypatch.setattr(knowledge_router.knowledge_base, "index_file", fake_index_file)
    monkeypatch.setattr(knowledge_router.tasker, "enqueue", fake_enqueue)

    result = await knowledge_router.add_documents(
        "kb_1",
        [item],
        params={"content_type": "file", "auto_index": True, "content_hashes": {item: "hash_1"}},
        current_user=user("uid-user"),
    )

    assert result["status"] == "queued"
    assert context.result["submitted"] == 1
    assert context.result["failed"] == 0
    assert context.result["items"] == [{"file_id": "file_1", "status": "indexed", "error": None}]


async def test_add_uploaded_documents_rejects_empty_items(monkeypatch):
    async def fake_ensure_database_supports_documents(kb_id: str, operation: str) -> None:
        return None

    monkeypatch.setattr(
        knowledge_router,
        "_ensure_database_supports_documents",
        fake_ensure_database_supports_documents,
    )

    with pytest.raises(HTTPException) as exc_info:
        await knowledge_router.add_uploaded_documents(
            "kb_1",
            knowledge_router.AddUploadedDocumentsRequest(items=[], params={}),
            current_user=user("uid-user"),
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "items must not be empty"


async def test_add_uploaded_documents_rejects_non_minio_url(monkeypatch):
    async def fake_ensure_database_supports_documents(kb_id: str, operation: str) -> None:
        return None

    monkeypatch.setattr(
        knowledge_router,
        "_ensure_database_supports_documents",
        fake_ensure_database_supports_documents,
    )

    with pytest.raises(HTTPException) as exc_info:
        await knowledge_router.add_uploaded_documents(
            "kb_1",
            knowledge_router.AddUploadedDocumentsRequest(
                items=["https://example.com/demo.txt"],
                params={"content_hashes": {"https://example.com/demo.txt": "hash_1"}},
            ),
            current_user=user("uid-user"),
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "File source must be a MinIO URL"


async def test_add_uploaded_documents_allows_missing_content_hash(monkeypatch):
    """缺 content_hash 不再报错：由 create_uploaded_document 服务端重算可信哈希（PR12 吸收）。"""
    item = "minio://knowledgebases/kb_1/upload/demo.txt"

    async def fake_ensure_database_supports_documents(kb_id: str, operation: str) -> None:
        return None

    async def fake_create_uploaded_document(self, *, kb_id, item, params, operator_id):
        return SimpleNamespace(
            action="created",
            file_meta={"file_id": "file_1", "status": "uploaded"},
            existing_file_id=None,
            cleanup_pending=False,
        )

    monkeypatch.setattr(
        knowledge_router,
        "_ensure_database_supports_documents",
        fake_ensure_database_supports_documents,
    )
    monkeypatch.setattr(
        "yuxi.services.document_ingestion_service.DocumentIngestionService.create_uploaded_document",
        fake_create_uploaded_document,
    )

    result = await knowledge_router.add_uploaded_documents(
        "kb_1",
        knowledge_router.AddUploadedDocumentsRequest(items=[item], params={}),
        current_user=user("uid-user"),
    )

    assert result["status"] == "success"
    assert result["added"] == 1


async def test_download_document_uses_shared_original_file_reader(monkeypatch):
    captured = {}

    async def fake_ensure_database_supports_documents(kb_id: str, operation: str) -> None:
        captured["ensure"] = (kb_id, operation)

    async def fake_get_file_download(kb_id: str, file_id: str, variant: str):
        captured["download"] = (kb_id, file_id, variant)
        return {
            "filename": "1.png",
            "content": b"image-content",
            "media_type": "image/png",
        }

    monkeypatch.setattr(
        knowledge_router,
        "_ensure_database_supports_documents",
        fake_ensure_database_supports_documents,
    )
    monkeypatch.setattr(knowledge_router.knowledge_base, "get_file_download", fake_get_file_download)

    response = await knowledge_router.download_document(
        "kb_1",
        "file_1",
        current_user=user("admin-user"),
    )

    assert response.media_type == "image/png"
    assert response.headers["content-disposition"] == "attachment; filename*=UTF-8''1.png"
    assert captured == {
        "ensure": ("kb_1", "文档下载"),
        "download": ("kb_1", "file_1", "original"),
    }


async def test_create_folder_passes_parent_to_knowledge_base(monkeypatch):
    captured = {}

    async def fake_ensure_database_supports_documents(kb_id: str, operation: str) -> None:
        captured["ensure"] = (kb_id, operation)

    async def fake_create_folder(kb_id: str, folder_name: str, parent_id: str | None):
        captured["create"] = (kb_id, folder_name, parent_id)
        return {"file_id": "folder-1", "filename": folder_name, "parent_id": parent_id}

    monkeypatch.setattr(
        knowledge_router,
        "_ensure_database_supports_documents",
        fake_ensure_database_supports_documents,
    )
    monkeypatch.setattr(knowledge_router.knowledge_base, "create_folder", fake_create_folder)

    result = await knowledge_router.create_folder(
        "kb_1",
        folder_name="图片",
        parent_id="folder-parent",
        current_user=user("admin-user"),
    )

    assert result["file_id"] == "folder-1"
    assert captured == {
        "ensure": ("kb_1", "文件夹创建"),
        "create": ("kb_1", "图片", "folder-parent"),
    }


async def test_add_uploaded_documents_creates_records_without_task(monkeypatch):
    item = "minio://knowledgebases/kb_1/upload/demo.txt"
    captured = {}

    async def fake_ensure_database_supports_documents(kb_id: str, operation: str) -> None:
        return None

    async def fake_create_uploaded_document(self, *, kb_id, item, params, operator_id):
        captured["kb_id"] = kb_id
        captured["item"] = item
        captured["params"] = params
        captured["operator_id"] = operator_id
        return SimpleNamespace(
            action="created",
            file_meta={"file_id": "file_1", "status": "uploaded", "filename": "demo.txt"},
            existing_file_id=None,
            cleanup_pending=False,
        )

    async def fail_enqueue(*_args, **_kwargs):
        raise AssertionError("documents/add must not enqueue tasker work")

    monkeypatch.setattr(
        knowledge_router,
        "_ensure_database_supports_documents",
        fake_ensure_database_supports_documents,
    )
    monkeypatch.setattr(
        "yuxi.services.document_ingestion_service.DocumentIngestionService.create_uploaded_document",
        fake_create_uploaded_document,
    )
    monkeypatch.setattr(knowledge_router.tasker, "enqueue", fail_enqueue)

    result = await knowledge_router.add_uploaded_documents(
        "kb_1",
        knowledge_router.AddUploadedDocumentsRequest(
            items=[item],
            params={
                "content_hashes": {item: "hash_1"},
                "file_sizes": {item: 4},
                "source_paths": {item: "docs/demo.txt"},
            },
        ),
        current_user=user("uid-user"),
    )

    assert result["status"] == "success"
    assert result["added"] == 1
    assert result["failed"] == 0
    assert result["items"][0]["file_id"] == "file_1"
    assert captured == {
        "kb_id": "kb_1",
        "item": item,
        "params": {
            "content_hashes": {item: "hash_1"},
            "file_sizes": {item: 4},
            "source_path": "docs/demo.txt",
        },
        "operator_id": "uid-user",
    }
