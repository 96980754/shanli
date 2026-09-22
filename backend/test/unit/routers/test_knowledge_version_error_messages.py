"""版本链路的业务错误码必须带可读说明。

`detail.code` 给前端做本地化，`detail.message` 是给非界面调用方的兜底——先前两者都是
`UPDATE_IN_PROGRESS` 这样的裸码，用户界面上直接显示出这串大写英文。
"""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from server.routers import knowledge_router

pytestmark = [pytest.mark.asyncio, pytest.mark.unit]

CREATE_CODES = ["SAME_CONTENT", "VERSION_NOT_NEWER", "VERSION_CHANGED", "UPDATE_IN_PROGRESS"]
ACTIVATE_CODES = ["VERSION_CHANGED", "CONFLICT_REVIEW_REQUIRED"]
DETACH_CODES = ["VERSION_NOT_FOUND", "CANNOT_DETACH_CURRENT_VERSION", "VERSION_FAMILY_HAS_NO_CURRENT"]


async def _allow(*_args, **_kwargs):
    return None


def _create_request():
    return knowledge_router.DocumentVersionCreateRequest(
        file_path="minio://bucket/kb/file.pdf",
        content_hash="hash-new",
        filename="说明书 V2.pdf",
    )


def _activate_request():
    return knowledge_router.DocumentVersionActivateRequest(expected_current_file_id="file-v1")


def _failing_service(code: str):
    class FakeService:
        async def create_candidate(self, **_kwargs):
            raise ValueError(code)

        async def activate_candidate(self, **_kwargs):
            raise ValueError(code)

    return FakeService


def _patch_create_path(monkeypatch, code: str):
    monkeypatch.setattr(knowledge_router, "_require_kb_permission", _allow)
    monkeypatch.setattr(knowledge_router, "_ensure_database_supports_documents", _allow)
    monkeypatch.setattr(knowledge_router, "DocumentVersionService", _failing_service(code))


@pytest.mark.parametrize("code", CREATE_CODES)
async def test_create_version_reports_readable_message(monkeypatch, code):
    _patch_create_path(monkeypatch, code)

    with pytest.raises(HTTPException) as excinfo:
        await knowledge_router.create_document_version(
            "kb-1",
            "file-v1",
            _create_request(),
            current_user=SimpleNamespace(uid="operator"),
        )

    assert excinfo.value.status_code == 409
    assert excinfo.value.detail == {"code": code, "message": knowledge_router.VERSION_ERROR_MESSAGES[code]}


@pytest.mark.parametrize("code", ACTIVATE_CODES)
async def test_activate_version_reports_readable_message(monkeypatch, code):
    monkeypatch.setattr(knowledge_router, "_require_kb_permission", _allow)
    monkeypatch.setattr(knowledge_router, "DocumentVersionService", _failing_service(code))

    with pytest.raises(HTTPException) as excinfo:
        await knowledge_router.activate_document_version(
            "kb-1",
            "file-v2",
            _activate_request(),
            current_user=SimpleNamespace(uid="operator"),
        )

    assert excinfo.value.status_code == 409
    assert excinfo.value.detail == {"code": code, "message": knowledge_router.VERSION_ERROR_MESSAGES[code]}


@pytest.mark.parametrize("code", DETACH_CODES)
async def test_detach_version_reports_readable_message(monkeypatch, code):
    class FakeRepository:
        async def detach_history_version(self, **_kwargs):
            raise ValueError(code)

    monkeypatch.setattr(knowledge_router, "_require_kb_permission", _allow)
    monkeypatch.setattr(knowledge_router, "KnowledgeFileRepository", FakeRepository)

    with pytest.raises(HTTPException) as excinfo:
        await knowledge_router.detach_document_version(
            "kb-1",
            "file-v2",
            current_user=SimpleNamespace(uid="operator"),
        )

    assert excinfo.value.detail == {"code": code, "message": knowledge_router.VERSION_ERROR_MESSAGES[code]}


async def test_unregistered_version_error_stays_a_plain_400(monkeypatch):
    """没有码的失败照旧原样返回，不套码也不换成通用文案。"""
    _patch_create_path(monkeypatch, "当前文档不存在")

    with pytest.raises(HTTPException) as excinfo:
        await knowledge_router.create_document_version(
            "kb-1",
            "file-v1",
            _create_request(),
            current_user=SimpleNamespace(uid="operator"),
        )

    assert excinfo.value.status_code == 400
    assert excinfo.value.detail == "当前文档不存在"


async def test_every_version_code_carries_a_real_message():
    for code, message in knowledge_router.VERSION_ERROR_MESSAGES.items():
        assert message and message != code, f"{code} 的说明就是码本身"
        assert code not in message, f"{code} 的说明里不该再出现码"
