from inspect import signature
from io import BytesIO
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException, UploadFile
from httpx import ASGITransport, AsyncClient

from server.routers import knowledge_router

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def allow_kb_permissions(monkeypatch):
    async def allow(*_args, **_kwargs):
        return None

    monkeypatch.setattr(knowledge_router, "_require_kb_permission", allow)


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
        current_user=SimpleNamespace(uid="user_1"),
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
        return SimpleNamespace(uid="user_1")

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
            current_user=SimpleNamespace(uid="user_1"),
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "filename is required"


async def test_upload_file_rejects_jsonl_uploads(monkeypatch):
    async def allow_permission(*_args, **_kwargs):
        return None

    monkeypatch.setattr(knowledge_router, "_require_kb_permission", allow_permission)
    monkeypatch.setattr(knowledge_router, "_ensure_database_supports_documents", allow_permission)
    upload = UploadFile(filename="dataset.jsonl", file=BytesIO(b'{"query":"hello"}\n'))

    with pytest.raises(HTTPException) as exc_info:
        await knowledge_router.upload_file(
            upload,
            kb_id="kb_1",
            duplicate_strategy="prompt",
            replace_file_id=None,
            current_user=SimpleNamespace(uid="user_1"),
        )

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

    async def allow_permission(*_args, **_kwargs):
        return None

    monkeypatch.setattr(knowledge_router, "_require_kb_permission", allow_permission)

    upload = UploadFile(filename="demo.txt", file=BytesIO(b"123456"))

    with pytest.raises(HTTPException) as exc_info:
        await knowledge_router.upload_file(
            upload,
            kb_id="kb_1",
            duplicate_strategy="prompt",
            replace_file_id=None,
            current_user=SimpleNamespace(uid="user_1"),
        )

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

    async def allow_permission(*_args, **_kwargs):
        return None

    monkeypatch.setattr(knowledge_router, "_require_kb_permission", allow_permission)

    upload = UploadFile(filename="demo.txt", file=BytesIO(b"demo"))

    with pytest.raises(HTTPException) as exc_info:
        await knowledge_router.upload_file(
            upload,
            kb_id="missing",
            duplicate_strategy="prompt",
            replace_file_id=None,
            current_user=SimpleNamespace(uid="user_1"),
        )

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

    async def allow_permission(*_args, **_kwargs):
        return None

    monkeypatch.setattr(knowledge_router, "_require_kb_permission", allow_permission)

    upload = UploadFile(filename="demo.txt", file=BytesIO(b"demo"))

    with pytest.raises(HTTPException) as exc_info:
        await knowledge_router.upload_file(
            upload,
            kb_id="readonly",
            duplicate_strategy="prompt",
            replace_file_id=None,
            current_user=SimpleNamespace(uid="user_1"),
        )

    assert exc_info.value.status_code == 400
    assert calls == {"read": 0, "upload": 0}


async def test_upload_replace_requires_upload_and_manage_permissions_before_read(monkeypatch):
    calls = {"permissions": [], "read": 0}

    async def fake_require(_user, kb_id: str, action: str) -> None:
        calls["permissions"].append((kb_id, action))
        if action == "can_manage":
            raise HTTPException(status_code=403, detail="知识库权限不足")

    async def fake_read(*_args, **_kwargs):
        calls["read"] += 1
        return b"content"

    monkeypatch.setattr(knowledge_router, "_require_kb_permission", fake_require)
    monkeypatch.setattr(knowledge_router, "read_upload_with_limit", fake_read)

    upload = UploadFile(filename="demo.txt", file=BytesIO(b"content"))
    with pytest.raises(HTTPException) as exc_info:
        await knowledge_router.upload_file(
            upload,
            kb_id="kb_1",
            duplicate_strategy="REPLACE",
            replace_file_id="file_1",
            current_user=SimpleNamespace(uid="user_1"),
        )

    assert exc_info.value.status_code == 403
    assert calls == {
        "permissions": [("kb_1", "can_upload"), ("kb_1", "can_manage")],
        "read": 0,
    }


async def test_delete_document_requires_manage_permission_before_read(monkeypatch):
    calls = {"permissions": [], "read": 0}

    async def deny_manage(_user, kb_id: str, action: str) -> None:
        calls["permissions"].append((kb_id, action))
        raise HTTPException(status_code=403, detail="知识库权限不足")

    async def fake_get_file_basic_info(*_args, **_kwargs):
        calls["read"] += 1
        return {"meta": {}}

    monkeypatch.setattr(knowledge_router, "_require_kb_permission", deny_manage)
    monkeypatch.setattr(knowledge_router.knowledge_base, "get_file_basic_info", fake_get_file_basic_info)

    with pytest.raises(HTTPException) as exc_info:
        await knowledge_router.delete_document(
            "kb_1",
            "file_1",
            current_user=SimpleNamespace(uid="user_1"),
        )

    assert exc_info.value.status_code == 403
    assert calls == {"permissions": [("kb_1", "can_manage")], "read": 0}


async def test_upload_skip_returns_structured_success_without_storage_write(monkeypatch):
    calls = {"upload": 0}

    async def allow(*_args, **_kwargs):
        return None

    async def fake_check(self, **_kwargs):
        return SimpleNamespace(action="skipped", existing_file_id="file_existing", conflicts=())

    async def fake_upload(*_args, **_kwargs):
        calls["upload"] += 1
        return "minio://should-not-be-used"

    monkeypatch.setattr(knowledge_router, "_require_kb_permission", allow)
    monkeypatch.setattr(knowledge_router, "_ensure_database_supports_documents", allow)
    monkeypatch.setattr(knowledge_router.DocumentIngestionService, "check_upload_conflict", fake_check)
    monkeypatch.setattr(knowledge_router, "aupload_file_to_minio", fake_upload)

    result = await knowledge_router.upload_file(
        UploadFile(filename="demo.txt", file=BytesIO(b"content")),
        kb_id="kb_1",
        duplicate_strategy="skip",
        replace_file_id=None,
        current_user=SimpleNamespace(uid="user_1"),
    )

    assert result == {
        "message": "Upload skipped because a conflicting document already exists",
        "uploaded": False,
        "action": "skipped",
        "existing_file_id": "file_existing",
        "kb_id": "kb_1",
    }
    assert calls["upload"] == 0


async def test_markdown_endpoint_rejects_oversized_file(monkeypatch):
    monkeypatch.setattr(knowledge_router, "MAX_UPLOAD_SIZE_BYTES", 5)
    upload = UploadFile(filename="demo.txt", file=BytesIO(b"123456"))

    with pytest.raises(HTTPException) as exc_info:
        await knowledge_router.mark_it_down(upload, current_user=SimpleNamespace(uid="user_1"))

    assert exc_info.value.status_code == 400
    assert "100 MB" in exc_info.value.detail


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
        current_user=SimpleNamespace(id="numeric-id", uid="uid-user"),
    )

    assert result["status"] == "queued"
    assert captured["operator_id"] == "uid-user"


async def test_parse_documents_rejects_oversized_direct_batch():
    file_ids = [f"file_{index}" for index in range(knowledge_router.MAX_DIRECT_DOCUMENT_ACTION_FILE_IDS + 1)]

    with pytest.raises(HTTPException) as exc_info:
        await knowledge_router.parse_documents(
            "kb_1",
            file_ids,
            current_user=SimpleNamespace(uid="uid-user"),
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
        current_user=SimpleNamespace(uid="uid-user"),
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
        current_user=SimpleNamespace(uid="uid-user"),
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


async def test_add_documents_defaults_to_auto_index_and_returns_one_final_result_per_item(monkeypatch):
    context = FakeTaskContext()
    item = "minio://knowledgebases/kb_1/upload/demo.txt"

    async def fake_ensure_database_supports_documents(kb_id: str, operation: str) -> None:
        return None

    async def fake_get_database_info(kb_id: str) -> dict:
        return {"name": "测试知识库"}

    async def fake_create_uploaded_document(self, **_kwargs):
        return SimpleNamespace(
            action="created",
            file_meta={"file_id": "file_1", "status": "indexing"},
            existing_file_id=None,
        )

    async def fake_parse_file(kb_id: str, file_id: str, operator_id: str | None = None):
        return {"file_id": file_id, "status": "parsed"}

    async def fake_update_file_params(kb_id: str, file_id: str, params: dict, operator_id: str | None = None):
        return None

    async def fake_index_file(kb_id: str, file_id: str, operator_id: str | None = None, params: dict | None = None):
        return {"file_id": file_id, "status": "indexed"}

    async def fake_generate_draft(self, **kwargs):
        return {
            "file_id": kwargs["file_id"],
            "status": "waiting_confirmation",
            "cleaning_version": 1,
        }

    async def fake_confirm(self, **kwargs):
        return {"file_id": kwargs["file_id"], "status": "indexed"}

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
        knowledge_router.DocumentIngestionService,
        "create_uploaded_document",
        fake_create_uploaded_document,
    )
    monkeypatch.setattr(knowledge_router.knowledge_base, "parse_file", fake_parse_file)
    monkeypatch.setattr(knowledge_router.knowledge_base, "update_file_params", fake_update_file_params)
    monkeypatch.setattr(knowledge_router.knowledge_base, "index_file", fake_index_file)
    monkeypatch.setattr(knowledge_router.DocumentCleaningService, "generate_draft", fake_generate_draft)
    monkeypatch.setattr(knowledge_router.DocumentCleaningService, "confirm", fake_confirm)
    monkeypatch.setattr(knowledge_router.config, "document_cleaning_auto_confirm", True)
    monkeypatch.setattr(knowledge_router.tasker, "enqueue", fake_enqueue)

    result = await knowledge_router.add_documents(
        "kb_1",
        [item],
        params={"content_type": "file", "content_hashes": {item: "hash_1"}},
        current_user=SimpleNamespace(uid="uid-user"),
    )

    assert result["status"] == "queued"
    assert context.result["submitted"] == 1
    assert context.result["failed"] == 0
    assert context.result["items"] == [{"file_id": "file_1", "status": "indexed"}]


async def test_add_documents_explicit_auto_index_false_waits_for_cleaning_confirmation(monkeypatch):
    context = FakeTaskContext()
    item = "minio://knowledgebases/kb_1/upload/demo.txt"
    index_calls = []

    async def allow(*_args, **_kwargs):
        return None

    async def fake_get_database_info(_kb_id: str) -> dict:
        return {"name": "测试知识库"}

    async def fake_create_uploaded_document(self, **_kwargs):
        return SimpleNamespace(
            action="created",
            file_meta={"file_id": "file_1", "status": "uploaded"},
            existing_file_id=None,
        )

    async def fake_parse_file(kb_id: str, file_id: str, operator_id: str | None = None):
        return {"file_id": file_id, "status": "parsed"}

    async def fail_index(*args, **kwargs):
        index_calls.append((args, kwargs))
        raise AssertionError("explicit auto_index=false must not index")

    async def fake_generate_draft(self, **kwargs):
        return {
            "file_id": kwargs["file_id"],
            "status": "waiting_confirmation",
            "cleaning_version": 1,
        }

    async def fail_confirm(*_args, **_kwargs):
        raise AssertionError("explicit auto_index=false must not confirm or index")

    async def fake_enqueue(name: str, task_type: str, payload: dict, coroutine):
        await coroutine(context)
        return SimpleNamespace(id="task_1")

    monkeypatch.setattr(knowledge_router, "_ensure_database_supports_documents", allow)
    monkeypatch.setattr(knowledge_router.knowledge_base, "get_database_info", fake_get_database_info)
    monkeypatch.setattr(
        knowledge_router.DocumentIngestionService,
        "create_uploaded_document",
        fake_create_uploaded_document,
    )
    monkeypatch.setattr(knowledge_router.knowledge_base, "parse_file", fake_parse_file)
    monkeypatch.setattr(knowledge_router.knowledge_base, "index_file", fail_index)
    monkeypatch.setattr(knowledge_router.DocumentCleaningService, "generate_draft", fake_generate_draft)
    monkeypatch.setattr(knowledge_router.DocumentCleaningService, "confirm", fail_confirm)
    monkeypatch.setattr(knowledge_router.tasker, "enqueue", fake_enqueue)

    result = await knowledge_router.add_documents(
        "kb_1",
        [item],
        params={
            "content_type": "file",
            "auto_index": False,
            "content_hashes": {item: "hash_1"},
        },
        current_user=SimpleNamespace(uid="uid-user"),
    )

    assert result["status"] == "queued"
    assert context.result["items"] == [
        {
            "file_id": "file_1",
            "status": "waiting_confirmation",
            "cleaning_version": 1,
        }
    ]
    assert index_calls == []


async def test_add_documents_keeps_processing_siblings_when_one_parse_fails(monkeypatch):
    context = FakeTaskContext()
    items = [
        "minio://knowledgebases/kb_1/upload/bad.txt",
        "minio://knowledgebases/kb_1/upload/good.txt",
    ]
    created_index = 0

    async def allow(*_args, **_kwargs):
        return None

    async def fake_get_database_info(_kb_id: str) -> dict:
        return {"name": "测试知识库"}

    async def fake_create_uploaded_document(self, **_kwargs):
        nonlocal created_index
        created_index += 1
        return SimpleNamespace(
            action="created",
            file_meta={"file_id": f"file_{created_index}", "status": "uploaded"},
            existing_file_id=None,
        )

    async def fake_parse_file(kb_id: str, file_id: str, operator_id: str | None = None):
        if file_id == "file_1":
            raise ValueError("文档未提取到有效文本")
        return {"file_id": file_id, "status": "parsed"}

    async def fake_update_file_params(*_args, **_kwargs):
        return None

    async def fake_index_file(kb_id: str, file_id: str, operator_id: str | None = None, params=None):
        return {"file_id": file_id, "status": "indexed"}

    async def fake_generate_draft(self, **kwargs):
        return {
            "file_id": kwargs["file_id"],
            "status": "waiting_confirmation",
            "cleaning_version": 1,
        }

    async def fake_confirm(self, **kwargs):
        return {"file_id": kwargs["file_id"], "status": "indexed"}

    async def fake_enqueue(name: str, task_type: str, payload: dict, coroutine):
        await coroutine(context)
        return SimpleNamespace(id="task_1")

    monkeypatch.setattr(knowledge_router, "_ensure_database_supports_documents", allow)
    monkeypatch.setattr(knowledge_router.knowledge_base, "get_database_info", fake_get_database_info)
    monkeypatch.setattr(
        knowledge_router.DocumentIngestionService,
        "create_uploaded_document",
        fake_create_uploaded_document,
    )
    monkeypatch.setattr(knowledge_router.knowledge_base, "parse_file", fake_parse_file)
    monkeypatch.setattr(knowledge_router.knowledge_base, "update_file_params", fake_update_file_params)
    monkeypatch.setattr(knowledge_router.knowledge_base, "index_file", fake_index_file)
    monkeypatch.setattr(knowledge_router.DocumentCleaningService, "generate_draft", fake_generate_draft)
    monkeypatch.setattr(knowledge_router.DocumentCleaningService, "confirm", fake_confirm)
    monkeypatch.setattr(knowledge_router.tasker, "enqueue", fake_enqueue)

    with pytest.raises(HTTPException) as exc_info:
        await knowledge_router.add_documents(
            "kb_1",
            items,
            params={"content_type": "file"},
            current_user=SimpleNamespace(uid="uid-user"),
        )

    assert exc_info.value.status_code == 500
    assert context.result["items"][0]["error_type"] == "parse_failed"
    assert context.result["items"][1] == {"file_id": "file_2", "status": "indexed"}
    assert context.result["failed"] == 1
    assert context.result["submitted"] == 2


async def test_add_documents_auto_index_treats_error_none_as_success(monkeypatch):
    """成功入库的文件元数据会携带 error=None，不应被统计为失败 (#793)。"""
    context = FakeTaskContext()
    item = "minio://knowledgebases/kb_1/upload/demo.txt"

    async def fake_ensure_database_supports_documents(kb_id: str, operation: str) -> None:
        return None

    async def fake_get_database_info(kb_id: str) -> dict:
        return {"name": "测试知识库"}

    async def fake_create_uploaded_document(self, **_kwargs):
        return SimpleNamespace(
            action="created",
            file_meta={"file_id": "file_1", "status": "indexing"},
            existing_file_id=None,
        )

    async def fake_parse_file(kb_id: str, file_id: str, operator_id: str | None = None):
        return {"file_id": file_id, "status": "parsed", "error": None}

    async def fake_update_file_params(kb_id: str, file_id: str, params: dict, operator_id: str | None = None):
        return None

    async def fake_index_file(kb_id: str, file_id: str, operator_id: str | None = None, params: dict | None = None):
        return {"file_id": file_id, "status": "indexed", "error": None}

    async def fake_generate_draft(self, **kwargs):
        return {
            "file_id": kwargs["file_id"],
            "status": "waiting_confirmation",
            "cleaning_version": 1,
        }

    async def fake_confirm(self, **kwargs):
        return {"file_id": kwargs["file_id"], "status": "indexed", "error": None}

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
        knowledge_router.DocumentIngestionService,
        "create_uploaded_document",
        fake_create_uploaded_document,
    )
    monkeypatch.setattr(knowledge_router.knowledge_base, "parse_file", fake_parse_file)
    monkeypatch.setattr(knowledge_router.knowledge_base, "update_file_params", fake_update_file_params)
    monkeypatch.setattr(knowledge_router.knowledge_base, "index_file", fake_index_file)
    monkeypatch.setattr(knowledge_router.DocumentCleaningService, "generate_draft", fake_generate_draft)
    monkeypatch.setattr(knowledge_router.DocumentCleaningService, "confirm", fake_confirm)
    monkeypatch.setattr(knowledge_router.tasker, "enqueue", fake_enqueue)

    result = await knowledge_router.add_documents(
        "kb_1",
        [item],
        params={"content_type": "file", "auto_index": True, "content_hashes": {item: "hash_1"}},
        current_user=SimpleNamespace(uid="uid-user"),
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
            current_user=SimpleNamespace(uid="uid-user"),
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "items must not be empty"


async def test_add_uploaded_replace_rechecks_upload_and_manage_permissions(monkeypatch):
    item = "minio://knowledgebases/kb_1/upload/demo.txt"
    checked_actions = []

    async def fake_require(_user, kb_id: str, action: str) -> None:
        checked_actions.append((kb_id, action))
        if action == "can_manage":
            raise HTTPException(status_code=403, detail="知识库权限不足")

    async def fake_ensure_database_supports_documents(_kb_id: str, _operation: str) -> None:
        return None

    monkeypatch.setattr(knowledge_router, "_require_kb_permission", fake_require)
    monkeypatch.setattr(
        knowledge_router,
        "_ensure_database_supports_documents",
        fake_ensure_database_supports_documents,
    )

    with pytest.raises(HTTPException) as exc_info:
        await knowledge_router.add_uploaded_documents(
            "kb_1",
            knowledge_router.AddUploadedDocumentsRequest(
                items=[item],
                params={
                    "duplicate_strategies": {item: "replace"},
                    "replace_file_ids": {item: "file_1"},
                },
            ),
            current_user=SimpleNamespace(uid="uid-user"),
        )

    assert exc_info.value.status_code == 403
    assert checked_actions == [("kb_1", "can_upload"), ("kb_1", "can_manage")]


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
            current_user=SimpleNamespace(uid="uid-user"),
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "File source must be a MinIO URL"


async def test_add_uploaded_documents_does_not_require_client_content_hash(monkeypatch):
    item = "minio://knowledgebases/kb_1/upload/demo.txt"
    captured = {}

    async def fake_ensure_database_supports_documents(kb_id: str, operation: str) -> None:
        return None

    async def fake_create_uploaded_document(self, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            action="created",
            file_meta={"file_id": "file_1", "status": "uploaded"},
            existing_file_id=None,
        )

    monkeypatch.setattr(
        knowledge_router,
        "_ensure_database_supports_documents",
        fake_ensure_database_supports_documents,
    )
    monkeypatch.setattr(
        knowledge_router.DocumentIngestionService,
        "create_uploaded_document",
        fake_create_uploaded_document,
    )

    result = await knowledge_router.add_uploaded_documents(
        "kb_1",
        knowledge_router.AddUploadedDocumentsRequest(items=[item], params={}),
        current_user=SimpleNamespace(uid="uid-user"),
    )

    assert result["status"] == "success"
    assert captured["item"] == item
    assert "content_hash" not in captured["params"]


async def test_add_uploaded_documents_failure_does_not_echo_staged_url(monkeypatch):
    staged_url = "http://private-minio:9000/knowledgebases/kb_1/upload/private.txt"

    async def allow(*_args, **_kwargs):
        return None

    class FailingIngestionService:
        async def create_uploaded_document(self, **_kwargs):
            raise RuntimeError(f"failed to read {staged_url}")

    monkeypatch.setattr(knowledge_router, "_require_kb_permission", allow)
    monkeypatch.setattr(knowledge_router, "_ensure_database_supports_documents", allow)
    monkeypatch.setattr(knowledge_router, "DocumentIngestionService", FailingIngestionService)

    result = await knowledge_router.add_uploaded_documents(
        "kb_1",
        knowledge_router.AddUploadedDocumentsRequest(items=[staged_url], params={}),
        current_user=SimpleNamespace(uid="uid-user"),
    )

    assert result["failed"] == 1
    assert "item" not in result["failed_items"][0]
    assert staged_url not in str(result)
    assert "private-minio" not in result["failed_items"][0]["error"]


async def test_add_uploaded_documents_creates_records_without_task(monkeypatch):
    item = "minio://knowledgebases/kb_1/upload/demo.txt"
    captured = {}

    async def fake_ensure_database_supports_documents(kb_id: str, operation: str) -> None:
        return None

    async def fake_create_uploaded_document(self, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            action="created",
            file_meta={"file_id": "file_1", "status": "uploaded", "filename": "demo.txt"},
            existing_file_id=None,
        )

    async def fail_enqueue(*_args, **_kwargs):
        raise AssertionError("documents/add must not enqueue tasker work")

    monkeypatch.setattr(
        knowledge_router,
        "_ensure_database_supports_documents",
        fake_ensure_database_supports_documents,
    )
    monkeypatch.setattr(
        knowledge_router.DocumentIngestionService,
        "create_uploaded_document",
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
        current_user=SimpleNamespace(uid="uid-user"),
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


async def test_retry_replacement_cleanup_accepts_stale_pending_task(monkeypatch):
    record = SimpleNamespace(
        file_id="new",
        kb_id="kb_1",
        status="indexed",
        is_active=True,
        previous_version_id="old",
        replacement_target_file_id="old",
        processing_stage="replacement_cleanup",
        processing_task_id="replacement-cleanup:new:1",
    )
    calls = []

    class FakeRepository:
        async def get_by_file_id(self, file_id):
            assert file_id == "new"
            return record

    class FakeService:
        file_repository = FakeRepository()

        async def enqueue_replacement_cleanup(self, **kwargs):
            calls.append(kwargs)
            return "replacement-cleanup:new:2"

    async def allow(*_args, **_kwargs):
        return None

    monkeypatch.setattr(knowledge_router, "_require_kb_permission", allow)
    monkeypatch.setattr(knowledge_router, "_ensure_database_supports_documents", allow)
    monkeypatch.setattr(knowledge_router, "DocumentIngestionService", FakeService)

    result = await knowledge_router.retry_replacement_cleanup(
        "kb_1",
        "new",
        current_user=SimpleNamespace(uid="user_1"),
    )

    assert result["task_id"] == "replacement-cleanup:new:2"
    assert calls == [
        {
            "kb_id": "kb_1",
            "new_file_id": "new",
            "old_file_id": "old",
            "force_reclaim": False,
        }
    ]
