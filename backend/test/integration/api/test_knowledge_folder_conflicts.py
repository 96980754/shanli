from __future__ import annotations

import asyncio
import uuid

import pytest

from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository


pytestmark = pytest.mark.asyncio


async def _create_folder(client, headers, kb_id: str, name: str, parent_id: str | None = None):
    return await client.post(
        f"/api/knowledge/databases/{kb_id}/folders",
        json={"folder_name": name, "parent_id": parent_id},
        headers=headers,
    )


def _assert_name_conflict(response) -> None:
    assert response.status_code == 409, response.text
    assert response.json()["detail"] == {
        "code": "folder_name_conflict",
        "message": "同一目录下已存在同名文件夹",
    }


async def test_folder_names_are_unique_within_parent_scope(test_client, admin_headers, knowledge_database):
    kb_id = knowledge_database["kb_id"]

    root = await _create_folder(test_client, admin_headers, kb_id, "  Ｔｅｓｔ  ")
    assert root.status_code == 200, root.text
    assert root.json()["filename"] == "Test"
    _assert_name_conflict(await _create_folder(test_client, admin_headers, kb_id, "Test"))

    parent_a = await _create_folder(test_client, admin_headers, kb_id, "parent-a")
    parent_b = await _create_folder(test_client, admin_headers, kb_id, "parent-b")
    assert parent_a.status_code == parent_b.status_code == 200

    child_a = await _create_folder(test_client, admin_headers, kb_id, "shared", parent_a.json()["file_id"])
    child_b = await _create_folder(test_client, admin_headers, kb_id, "shared", parent_b.json()["file_id"])
    assert child_a.status_code == child_b.status_code == 200
    _assert_name_conflict(
        await _create_folder(test_client, admin_headers, kb_id, " shared ", parent_a.json()["file_id"])
    )

    upper = await _create_folder(test_client, admin_headers, kb_id, "Case")
    lower = await _create_folder(test_client, admin_headers, kb_id, "case")
    assert upper.status_code == lower.status_code == 200


async def test_invalid_folder_names_and_hard_delete_reuse(test_client, admin_headers, knowledge_database):
    kb_id = knowledge_database["kb_id"]
    for invalid_name in ("", "   ", ".", "..", "a/b", "a\\b", "bad\nname"):
        response = await _create_folder(test_client, admin_headers, kb_id, invalid_name)
        assert response.status_code == 422, (invalid_name, response.text)
        assert response.json()["detail"]["code"] == "invalid_folder_name"

    created = await _create_folder(test_client, admin_headers, kb_id, "reusable")
    assert created.status_code == 200
    deleted = await test_client.delete(
        f"/api/knowledge/databases/{kb_id}/documents/{created.json()['file_id']}",
        headers=admin_headers,
    )
    assert deleted.status_code == 200, deleted.text
    recreated = await _create_folder(test_client, admin_headers, kb_id, "reusable")
    assert recreated.status_code == 200, recreated.text


async def test_folder_and_file_may_share_name(test_client, admin_headers, knowledge_database):
    kb_id = knowledge_database["kb_id"]
    await KnowledgeFileRepository().upsert(
        f"file_{uuid.uuid4().hex[:12]}",
        {
            "kb_id": kb_id,
            "filename": "shared-name",
            "path": "shared-name",
            "status": "uploaded",
            "is_folder": False,
        },
    )

    response = await _create_folder(test_client, admin_headers, kb_id, "shared-name")
    assert response.status_code == 200, response.text


async def test_folder_create_enforces_permissions_and_resource_scope(
    test_client,
    admin_headers,
    view_only_user,
    knowledge_database,
):
    kb_id = knowledge_database["kb_id"]
    readable = await test_client.get(
        f"/api/knowledge/databases/{kb_id}/documents",
        headers=view_only_user["headers"],
    )
    assert readable.status_code == 200, readable.text

    forbidden = await _create_folder(test_client, view_only_user["headers"], kb_id, "forbidden")
    assert forbidden.status_code == 403

    missing_kb = await _create_folder(test_client, admin_headers, f"kb_{uuid.uuid4().hex}", "missing")
    assert missing_kb.status_code == 404

    missing_parent = await _create_folder(test_client, admin_headers, kb_id, "child", f"folder-{uuid.uuid4()}")
    assert missing_parent.status_code == 404


async def test_concurrent_same_name_folder_creation_is_serialized(test_client, admin_headers, knowledge_database):
    kb_id = knowledge_database["kb_id"]

    for round_index in range(3):
        name = f"concurrent-{round_index}-{uuid.uuid4().hex[:8]}"
        responses = await asyncio.gather(
            _create_folder(test_client, admin_headers, kb_id, name),
            _create_folder(test_client, admin_headers, kb_id, name),
        )
        assert sorted(response.status_code for response in responses) == [200, 409]
        conflict = next(response for response in responses if response.status_code == 409)
        _assert_name_conflict(conflict)

        records = await KnowledgeFileRepository().list_children(kb_id=kb_id, parent_id=None)
        active_matches = [record for record in records if record.is_folder and record.filename == name]
        assert len(active_matches) == 1
