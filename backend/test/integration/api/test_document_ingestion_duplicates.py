from __future__ import annotations

import asyncio
import uuid
from contextlib import suppress

import pytest
from yuxi.knowledge.runtime import knowledge_base
from yuxi.knowledge.utils import parse_minio_url
from yuxi.repositories.knowledge_chunk_repository import KnowledgeChunkRepository
from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository
from yuxi.services.document_ingestion_service import (
    DocumentIngestionService,
    process_document_replacement_cleanup,
)
from yuxi.storage.minio import get_minio_client

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def _upload(
    client,
    headers,
    kb_id: str,
    filename: str,
    content: bytes,
    strategy: str = "prompt",
    replace_file_id: str | None = None,
    parent_id: str | None = None,
):
    params = {"kb_id": kb_id, "duplicate_strategy": strategy}
    if replace_file_id:
        params["replace_file_id"] = replace_file_id
    if parent_id:
        params["parent_id"] = parent_id
    return await client.post(
        "/api/knowledge/files/upload",
        params=params,
        files={"file": (filename, content, "text/plain")},
        headers=headers,
    )


async def _add_uploaded(
    client,
    headers,
    kb_id: str,
    upload_payload: dict,
    strategy: str,
    replace_file_id: str | None = None,
    parent_id: str | None = None,
):
    item = upload_payload["file_path"]
    params = {
        "duplicate_strategies": {item: strategy},
        "source_paths": {item: upload_payload["filename"]},
        "content_hashes": {item: "forged-client-hash"},
        "file_sizes": {item: 1},
    }
    if replace_file_id:
        params["replace_file_ids"] = {item: replace_file_id}
    if parent_id:
        params["parent_ids"] = {item: parent_id}
    return await client.post(
        f"/api/knowledge/databases/{kb_id}/documents/add",
        json={
            "items": [item],
            "params": params,
        },
        headers=headers,
    )


def _result_file_ids(results: list[dict]) -> set[str]:
    return {str(item.get("metadata", {}).get("file_id") or item.get("file_id") or "") for item in results}


async def _milvus_file_rows(collection, file_id: str) -> list[dict]:
    await asyncio.to_thread(collection.flush)
    return await asyncio.to_thread(
        collection.query,
        expr=f'file_id == "{file_id}"',
        output_fields=["file_id", "chunk_id"],
        limit=100,
    )


async def _staged_object_size(upload_payload: dict) -> int | None:
    bucket_name, object_name = parse_minio_url(upload_payload["file_path"])
    return await get_minio_client().astat_file(bucket_name, object_name)


async def _wait_for_replacement_cleanup(
    repository: KnowledgeFileRepository,
    collection,
    *,
    new_file_id: str,
    old_file_id: str,
    timeout: float = 60.0,
):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while loop.time() < deadline:
        new_record = await repository.get_by_file_id(new_file_id)
        old_rows = await _milvus_file_rows(collection, old_file_id)
        if (
            new_record is not None
            and new_record.processing_stage is None
            and new_record.processing_task_id is None
            and not old_rows
        ):
            return new_record
        await asyncio.sleep(0.25)
    raise AssertionError("replacement cleanup did not finish within the timeout")


async def test_upload_conflict_protocol_distinguishes_content_and_name(
    test_client,
    admin_headers,
    knowledge_database,
):
    kb_id = knowledge_database["kb_id"]
    unique_id = uuid.uuid4().hex
    filename = f"protocol-{unique_id}.txt"
    content = f"protocol-content-{unique_id}".encode()

    base_upload = await _upload(test_client, admin_headers, kb_id, filename, content)
    assert base_upload.status_code == 200, base_upload.text
    base_add = await _add_uploaded(test_client, admin_headers, kb_id, base_upload.json(), "prompt")
    assert base_add.status_code == 200, base_add.text
    existing_file_id = base_add.json()["items"][0]["file_id"]

    same_name_exact = await _upload(test_client, admin_headers, kb_id, filename, content)
    assert same_name_exact.status_code == 409
    exact_detail = same_name_exact.json()["detail"]
    assert exact_detail["code"] == "duplicate_conflict"
    assert exact_detail["conflict_type"] == "exact_content"
    assert exact_detail["allowed_strategies"] == ["skip"]
    assert exact_detail["conflicts"][0]["file_id"] == existing_file_id
    assert exact_detail["incoming"]["content_hash"] == exact_detail["conflicts"][0]["content_hash"]

    different_name_exact = await _upload(
        test_client,
        admin_headers,
        kb_id,
        f"renamed-{unique_id}.txt",
        content,
    )
    assert different_name_exact.status_code == 409
    assert different_name_exact.json()["detail"]["conflict_type"] == "exact_content"

    skipped = await _upload(
        test_client,
        admin_headers,
        kb_id,
        f"skipped-{unique_id}.txt",
        content,
        "skip",
    )
    assert skipped.status_code == 200
    assert skipped.json() == {
        "message": "Upload skipped because a conflicting document already exists",
        "uploaded": False,
        "action": "skipped",
        "existing_file_id": existing_file_id,
        "kb_id": kb_id,
    }

    same_name_new_content = await _upload(
        test_client,
        admin_headers,
        kb_id,
        filename,
        f"new-content-{unique_id}".encode(),
    )
    assert same_name_new_content.status_code == 409
    name_detail = same_name_new_content.json()["detail"]
    assert name_detail["code"] == "duplicate_conflict"
    assert name_detail["conflict_type"] == "same_name"
    assert name_detail["allowed_strategies"] == ["skip", "replace", "keep_both"]
    assert name_detail["incoming"]["content_hash"] != name_detail["conflicts"][0]["content_hash"]

    for unsupported_strategy in ("keep_both", "replace"):
        response = await _upload(
            test_client,
            admin_headers,
            kb_id,
            f"exact-{unsupported_strategy}-{unique_id}.txt",
            content,
            unsupported_strategy,
            existing_file_id if unsupported_strategy == "replace" else None,
        )
        assert response.status_code == 400
        assert "only supports the skip strategy" in response.json()["detail"]


async def test_same_name_conflicts_are_scoped_to_parent_but_exact_content_remains_global(
    test_client,
    admin_headers,
    knowledge_database,
):
    kb_id = knowledge_database["kb_id"]
    suffix = uuid.uuid4().hex

    async def create_folder(name: str) -> str:
        response = await test_client.post(
            f"/api/knowledge/databases/{kb_id}/folders",
            json={"folder_name": name, "parent_id": None},
            headers=admin_headers,
        )
        assert response.status_code == 200, response.text
        return response.json()["file_id"]

    first_parent = await create_folder(f"scope-a-{suffix}")
    second_parent = await create_folder(f"scope-b-{suffix}")
    filename = f"scope-{suffix}.txt"
    first_content = f"first-{suffix}".encode()
    second_content = f"second-{suffix}".encode()

    first_upload = await _upload(
        test_client,
        admin_headers,
        kb_id,
        filename,
        first_content,
        parent_id=first_parent,
    )
    assert first_upload.status_code == 200, first_upload.text
    first_add = await _add_uploaded(
        test_client,
        admin_headers,
        kb_id,
        first_upload.json(),
        "prompt",
        parent_id=first_parent,
    )
    assert first_add.status_code == 200, first_add.text
    first_file_id = first_add.json()["items"][0]["file_id"]
    await KnowledgeFileRepository().update_fields(
        file_id=first_file_id,
        kb_id=kb_id,
        data={"status": "error_indexing"},
    )

    different_folder = await _upload(
        test_client,
        admin_headers,
        kb_id,
        filename,
        second_content,
        parent_id=second_parent,
    )
    assert different_folder.status_code == 200, different_folder.text
    assert different_folder.json()["parent_id"] == second_parent
    different_folder_add = await _add_uploaded(
        test_client,
        admin_headers,
        kb_id,
        different_folder.json(),
        "prompt",
        parent_id=second_parent,
    )
    assert different_folder_add.status_code == 200, different_folder_add.text

    root_upload = await _upload(
        test_client,
        admin_headers,
        kb_id,
        filename,
        f"root-{suffix}".encode(),
    )
    assert root_upload.status_code == 200, root_upload.text
    root_add = await _add_uploaded(test_client, admin_headers, kb_id, root_upload.json(), "prompt")
    assert root_add.status_code == 200, root_add.text

    same_folder = await _upload(
        test_client,
        admin_headers,
        kb_id,
        filename,
        f"third-{suffix}".encode(),
        parent_id=first_parent,
    )
    assert same_folder.status_code == 409
    assert same_folder.json()["detail"]["conflict_type"] == "same_name"

    exact_across_folders = await _upload(
        test_client,
        admin_headers,
        kb_id,
        f"renamed-{suffix}.txt",
        first_content,
        parent_id=second_parent,
    )
    assert exact_across_folders.status_code == 409
    exact_detail = exact_across_folders.json()["detail"]
    assert exact_detail["conflict_type"] == "exact_content"
    assert exact_detail["conflicts"][0]["file_id"] == first_file_id
    assert exact_detail["conflicts"][0]["parent_id"] == first_parent
    assert exact_detail["conflicts"][0]["display_path"].endswith(filename)

    invalid_replace = await _upload(
        test_client,
        admin_headers,
        kb_id,
        filename,
        f"replace-{suffix}".encode(),
        strategy="replace",
        replace_file_id=first_file_id,
        parent_id=second_parent,
    )
    assert invalid_replace.status_code == 409
    assert invalid_replace.json()["detail"]["code"] == "invalid_replacement_target"

    staged_for_invalid_second_phase = await _upload(
        test_client,
        admin_headers,
        kb_id,
        filename,
        f"second-phase-replace-{suffix}".encode(),
        strategy="keep_both",
        parent_id=second_parent,
    )
    assert staged_for_invalid_second_phase.status_code == 200, staged_for_invalid_second_phase.text
    invalid_second_phase = await _add_uploaded(
        test_client,
        admin_headers,
        kb_id,
        staged_for_invalid_second_phase.json(),
        "replace",
        replace_file_id=first_file_id,
        parent_id=second_parent,
    )
    assert invalid_second_phase.status_code == 409
    assert invalid_second_phase.json()["detail"]["code"] == "invalid_replacement_target"


async def test_concurrent_cross_folder_same_name_is_independent_but_content_hash_is_global(
    test_client,
    admin_headers,
    knowledge_database,
):
    kb_id = knowledge_database["kb_id"]
    suffix = uuid.uuid4().hex
    parent_ids = []
    for name in (f"concurrent-a-{suffix}", f"concurrent-b-{suffix}"):
        response = await test_client.post(
            f"/api/knowledge/databases/{kb_id}/folders",
            json={"folder_name": name, "parent_id": None},
            headers=admin_headers,
        )
        assert response.status_code == 200, response.text
        parent_ids.append(response.json()["file_id"])

    filename = f"cross-folder-{suffix}.txt"
    uploads = await asyncio.gather(
        _upload(test_client, admin_headers, kb_id, filename, b"one", parent_id=parent_ids[0]),
        _upload(test_client, admin_headers, kb_id, filename, b"two", parent_id=parent_ids[1]),
    )
    assert [response.status_code for response in uploads] == [200, 200]
    additions = await asyncio.gather(
        *(
            _add_uploaded(
                test_client,
                admin_headers,
                kb_id,
                upload.json(),
                "prompt",
                parent_id=parent_id,
            )
            for upload, parent_id in zip(uploads, parent_ids, strict=True)
        )
    )
    assert [response.status_code for response in additions] == [200, 200]

    identical_uploads = await asyncio.gather(
        _upload(test_client, admin_headers, kb_id, f"same-a-{suffix}.txt", b"same", parent_id=parent_ids[0]),
        _upload(test_client, admin_headers, kb_id, f"same-b-{suffix}.txt", b"same", parent_id=parent_ids[1]),
    )
    assert [response.status_code for response in identical_uploads] == [200, 200]
    identical_additions = await asyncio.gather(
        *(
            _add_uploaded(
                test_client,
                admin_headers,
                kb_id,
                upload.json(),
                "prompt",
                parent_id=parent_id,
            )
            for upload, parent_id in zip(identical_uploads, parent_ids, strict=True)
        ),
        return_exceptions=False,
    )
    assert sorted(response.status_code for response in identical_additions) == [200, 409]
    conflict = next(response for response in identical_additions if response.status_code == 409)
    assert conflict.json()["detail"]["conflict_type"] == "exact_content"


async def test_concurrent_same_name_prompt_creates_one_record_and_removes_losing_stage(
    test_client,
    admin_headers,
    knowledge_database,
):
    kb_id = knowledge_database["kb_id"]
    filename = f"same-name-race-{uuid.uuid4().hex}.txt"
    uploads = await asyncio.gather(
        _upload(test_client, admin_headers, kb_id, filename, b"first distinct content"),
        _upload(test_client, admin_headers, kb_id, filename, b"second distinct content"),
    )
    assert [response.status_code for response in uploads] == [200, 200]

    additions = await asyncio.gather(
        *(_add_uploaded(test_client, admin_headers, kb_id, upload.json(), "prompt") for upload in uploads)
    )
    assert sorted(response.status_code for response in additions) == [200, 409]
    conflict_response = next(response for response in additions if response.status_code == 409)
    assert conflict_response.json()["detail"]["conflict_type"] == "same_name"

    records = await KnowledgeFileRepository().list_same_name_files(kb_id=kb_id, parent_id=None, filename=filename)
    assert len(records) == 1
    for upload, addition in zip(uploads, additions, strict=True):
        object_size = await _staged_object_size(upload.json())
        if addition.status_code == 200:
            assert object_size is not None
        else:
            assert object_size is None


async def test_concurrent_identical_second_stage_creates_only_one_document(
    test_client,
    admin_headers,
    knowledge_database,
):
    kb_id = knowledge_database["kb_id"]
    filename = f"concurrent-{uuid.uuid4().hex}.txt"
    content = b"same concurrent content"

    first_upload, second_upload = await asyncio.gather(
        _upload(test_client, admin_headers, kb_id, filename, content),
        _upload(test_client, admin_headers, kb_id, filename, content),
    )
    assert first_upload.status_code == second_upload.status_code == 200

    first_add, second_add = await asyncio.gather(
        _add_uploaded(test_client, admin_headers, kb_id, first_upload.json(), "prompt"),
        _add_uploaded(test_client, admin_headers, kb_id, second_upload.json(), "prompt"),
    )
    assert sorted([first_add.status_code, second_add.status_code]) == [200, 409]
    conflict = first_add.json() if first_add.status_code == 409 else second_add.json()
    detail = conflict["detail"]
    assert detail["conflict_type"] == "exact_content"
    assert detail["allowed_strategies"] == ["skip"]
    assert "object_name" not in str(detail)
    assert "bucket_name" not in str(detail)


async def test_concurrent_keep_both_allocates_sequential_names(
    test_client,
    admin_headers,
    knowledge_database,
):
    kb_id = knowledge_database["kb_id"]
    base_name = f"report-{uuid.uuid4().hex}.txt"

    base_upload = await _upload(test_client, admin_headers, kb_id, base_name, b"base")
    assert base_upload.status_code == 200, base_upload.text
    base_add = await _add_uploaded(test_client, admin_headers, kb_id, base_upload.json(), "prompt")
    assert base_add.status_code == 200, base_add.text

    first_upload, second_upload = await asyncio.gather(
        _upload(test_client, admin_headers, kb_id, base_name.upper(), b"version one", "keep_both"),
        _upload(test_client, admin_headers, kb_id, base_name, b"version two", "keep_both"),
    )
    assert first_upload.status_code == second_upload.status_code == 200

    first_add, second_add = await asyncio.gather(
        _add_uploaded(test_client, admin_headers, kb_id, first_upload.json(), "keep_both"),
        _add_uploaded(test_client, admin_headers, kb_id, second_upload.json(), "keep_both"),
    )
    assert first_add.status_code == second_add.status_code == 200

    response = await test_client.get(
        f"/api/knowledge/databases/{kb_id}/documents",
        params={"page_size": 100},
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text
    names = {item["filename"].casefold() for item in response.json()["items"]}
    stem = base_name.removesuffix(".txt").casefold()
    assert {f"{stem}.txt", f"{stem} (1).txt", f"{stem} (2).txt"}.issubset(names)


async def test_repeated_replace_second_stage_reuses_same_document_version(
    test_client,
    admin_headers,
    knowledge_database,
):
    kb_id = knowledge_database["kb_id"]
    filename = f"replace-{uuid.uuid4().hex}.txt"

    base_upload = await _upload(test_client, admin_headers, kb_id, filename, b"old content")
    base_add = await _add_uploaded(test_client, admin_headers, kb_id, base_upload.json(), "prompt")
    old_file_id = base_add.json()["items"][0]["file_id"]

    replacement_upload = await _upload(
        test_client,
        admin_headers,
        kb_id,
        filename,
        b"new content",
        "replace",
        old_file_id,
    )
    assert replacement_upload.status_code == 200, replacement_upload.text

    first_add = await _add_uploaded(
        test_client,
        admin_headers,
        kb_id,
        replacement_upload.json(),
        "replace",
        old_file_id,
    )
    second_add = await _add_uploaded(
        test_client,
        admin_headers,
        kb_id,
        replacement_upload.json(),
        "replace",
        old_file_id,
    )
    assert first_add.status_code == second_add.status_code == 200
    first_item = first_add.json()["items"][0]
    second_item = second_add.json()["items"][0]
    assert first_item["file_id"] == second_item["file_id"]
    assert first_item["action"] == "created"
    assert second_item["action"] == "existing"

    repository = KnowledgeFileRepository()
    new_file_id = first_item["file_id"]
    await repository.update_fields(
        file_id=new_file_id,
        kb_id=kb_id,
        data={"status": "indexed"},
    )
    await repository.switch_active_version(
        kb_id=kb_id,
        new_file_id=new_file_id,
        old_file_id=old_file_id,
    )
    # The switch itself is idempotent and never creates another version row.
    await repository.switch_active_version(
        kb_id=kb_id,
        new_file_id=new_file_id,
        old_file_id=old_file_id,
    )

    old_record = await repository.get_by_file_id(old_file_id)
    new_record = await repository.get_by_file_id(new_file_id)
    assert old_record.is_active is False
    assert old_record.superseded_at is not None
    assert new_record.is_active is True
    assert new_record.previous_version_id == old_file_id
    # This test exercises the repository switch directly and intentionally bypasses
    # the service that enqueues replacement cleanup. Do not leave an artificial
    # pending-cleanup state for the shared integration teardown.
    await repository.update_fields(
        file_id=new_file_id,
        kb_id=kb_id,
        data={"processing_stage": None, "processing_progress": 100},
    )


async def test_concurrent_replace_same_target_creates_one_candidate(
    test_client,
    admin_headers,
    knowledge_database,
):
    kb_id = knowledge_database["kb_id"]
    filename = f"replace-race-{uuid.uuid4().hex}.txt"
    base_upload = await _upload(test_client, admin_headers, kb_id, filename, b"base")
    base_add = await _add_uploaded(test_client, admin_headers, kb_id, base_upload.json(), "prompt")
    old_file_id = base_add.json()["items"][0]["file_id"]

    first_upload, second_upload = await asyncio.gather(
        _upload(test_client, admin_headers, kb_id, filename, b"candidate one", "replace", old_file_id),
        _upload(test_client, admin_headers, kb_id, filename, b"candidate two", "replace", old_file_id),
    )
    assert first_upload.status_code == second_upload.status_code == 200

    first_add, second_add = await asyncio.gather(
        _add_uploaded(test_client, admin_headers, kb_id, first_upload.json(), "replace", old_file_id),
        _add_uploaded(test_client, admin_headers, kb_id, second_upload.json(), "replace", old_file_id),
    )
    assert sorted([first_add.status_code, second_add.status_code]) == [200, 409]

    success = first_add if first_add.status_code == 200 else second_add
    conflict = first_add if first_add.status_code == 409 else second_add
    detail = conflict.json()["detail"]
    candidate_file_id = success.json()["items"][0]["file_id"]
    assert detail == {
        "code": "replacement_in_progress",
        "message": "该文档已有正在处理的替换版本",
        "target_file_id": old_file_id,
        "candidate_file_id": candidate_file_id,
    }
    for upload, addition in zip((first_upload, second_upload), (first_add, second_add), strict=True):
        object_size = await _staged_object_size(upload.json())
        if addition.status_code == 200:
            assert object_size is not None
        else:
            assert object_size is None

    repository = KnowledgeFileRepository()
    candidates = await repository.list_pending_replacement_candidates(
        kb_id=kb_id,
        replacement_target_file_id=old_file_id,
    )
    assert [record.file_id for record in candidates] == [candidate_file_id]

    await repository.update_fields(
        file_id=candidate_file_id,
        kb_id=kb_id,
        data={"status": "indexed"},
    )
    await repository.switch_active_version(
        kb_id=kb_id,
        new_file_id=candidate_file_id,
        old_file_id=old_file_id,
    )
    assert (await repository.get_by_file_id(old_file_id)).is_active is False
    assert (await repository.get_by_file_id(candidate_file_id)).is_active is True
    # This test exercises the repository switch directly and intentionally bypasses
    # the service that enqueues replacement cleanup. Do not leave an artificial
    # pending-cleanup state for the shared integration teardown.
    await repository.update_fields(
        file_id=candidate_file_id,
        kb_id=kb_id,
        data={"processing_stage": None, "processing_progress": 100},
    )


async def test_failed_replacement_candidate_releases_target(
    test_client,
    admin_headers,
    knowledge_database,
):
    kb_id = knowledge_database["kb_id"]
    filename = f"replace-release-{uuid.uuid4().hex}.txt"
    base_upload = await _upload(test_client, admin_headers, kb_id, filename, b"base")
    base_add = await _add_uploaded(test_client, admin_headers, kb_id, base_upload.json(), "prompt")
    old_file_id = base_add.json()["items"][0]["file_id"]

    failed_upload = await _upload(
        test_client,
        admin_headers,
        kb_id,
        filename,
        b"failed candidate",
        "replace",
        old_file_id,
    )
    failed_add = await _add_uploaded(
        test_client,
        admin_headers,
        kb_id,
        failed_upload.json(),
        "replace",
        old_file_id,
    )
    failed_file_id = failed_add.json()["items"][0]["file_id"]
    repository = KnowledgeFileRepository()
    await repository.update_fields(
        file_id=failed_file_id,
        kb_id=kb_id,
        data={"status": "error_indexing"},
    )

    next_upload = await _upload(
        test_client,
        admin_headers,
        kb_id,
        filename,
        b"failed candidate",
        "replace",
        old_file_id,
    )
    next_add = await _add_uploaded(
        test_client,
        admin_headers,
        kb_id,
        next_upload.json(),
        "replace",
        old_file_id,
    )

    assert next_upload.status_code == 200
    assert next_add.status_code == 200
    assert next_add.json()["items"][0]["file_id"] != failed_file_id


async def test_concurrent_replace_different_targets_do_not_conflict(
    test_client,
    admin_headers,
    knowledge_database,
):
    kb_id = knowledge_database["kb_id"]
    names = [f"replace-a-{uuid.uuid4().hex}.txt", f"replace-b-{uuid.uuid4().hex}.txt"]
    old_file_ids = []
    for index, filename in enumerate(names):
        uploaded = await _upload(test_client, admin_headers, kb_id, filename, f"base-{index}".encode())
        added = await _add_uploaded(test_client, admin_headers, kb_id, uploaded.json(), "prompt")
        old_file_ids.append(added.json()["items"][0]["file_id"])

    replacements = await asyncio.gather(
        *(
            _upload(
                test_client,
                admin_headers,
                kb_id,
                filename,
                f"new-{index}".encode(),
                "replace",
                old_file_ids[index],
            )
            for index, filename in enumerate(names)
        )
    )
    results = await asyncio.gather(
        *(
            _add_uploaded(
                test_client,
                admin_headers,
                kb_id,
                replacements[index].json(),
                "replace",
                old_file_ids[index],
            )
            for index in range(2)
        )
    )

    assert [response.status_code for response in results] == [200, 200]


async def test_keep_both_upload_and_replace_same_name_serialize_consistently(
    test_client,
    admin_headers,
    knowledge_database,
):
    kb_id = knowledge_database["kb_id"]
    filename = f"upload-replace-race-{uuid.uuid4().hex}.txt"
    base_upload = await _upload(test_client, admin_headers, kb_id, filename, b"base content")
    base_add = await _add_uploaded(test_client, admin_headers, kb_id, base_upload.json(), "prompt")
    old_file_id = base_add.json()["items"][0]["file_id"]

    keep_upload, replace_upload = await asyncio.gather(
        _upload(test_client, admin_headers, kb_id, filename, b"independent copy", "keep_both"),
        _upload(test_client, admin_headers, kb_id, filename, b"replacement content", "replace", old_file_id),
    )
    assert [keep_upload.status_code, replace_upload.status_code] == [200, 200]

    keep_add, replace_add = await asyncio.gather(
        _add_uploaded(test_client, admin_headers, kb_id, keep_upload.json(), "keep_both"),
        _add_uploaded(test_client, admin_headers, kb_id, replace_upload.json(), "replace", old_file_id),
    )
    assert [keep_add.status_code, replace_add.status_code] == [200, 200]

    repository = KnowledgeFileRepository()
    old_record = await repository.get_by_file_id(old_file_id)
    kept_record = await repository.get_by_file_id(keep_add.json()["items"][0]["file_id"])
    candidate = await repository.get_by_file_id(replace_add.json()["items"][0]["file_id"])
    assert old_record is not None and old_record.is_active is True
    assert kept_record is not None and kept_record.is_active is True
    assert kept_record.filename.casefold() != filename.casefold()
    assert candidate is not None and candidate.is_active is False
    assert candidate.replacement_target_file_id == old_file_id

    # This lock-focused test does not run parsing/indexing. Mark the candidate terminal
    # so it cannot block fixture teardown or a later replacement test.
    await repository.update_fields(
        file_id=candidate.file_id,
        kb_id=kb_id,
        data={"status": "error_indexing", "processing_stage": "error_indexing"},
    )


async def test_real_replacement_saga_preserves_history_and_switches_milvus_visibility(
    test_client,
    admin_headers,
    knowledge_database,
    monkeypatch,
):
    kb_id = knowledge_database["kb_id"]
    unique_id = uuid.uuid4().hex
    filename = f"replacement-e2e-{unique_id}.txt"
    old_marker = f"amberhistory{unique_id}"
    new_marker = f"cobaltcurrent{unique_id}"
    old_content = f"{old_marker} is the retained historical document.".encode()
    new_content = f"{new_marker} is the active replacement document.".encode()

    kb = await knowledge_base.aget_kb(kb_id)
    if kb_id not in kb.databases_meta:
        await kb._load_metadata()
    collection = await kb._get_milvus_collection(kb_id)
    assert collection is not None
    embedding_field = next(field for field in collection.schema.fields if field.name == "embedding")
    embedding_dimension = int(embedding_field.params["dim"])
    assert embedding_dimension >= 2

    def deterministic_vectors(texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            vector = [0.0] * embedding_dimension
            if new_marker in text:
                vector[0] = 1.0
            elif old_marker in text:
                vector[1] = 1.0
            else:
                vector[-1] = 1.0
            vectors.append(vector)
        return vectors

    async def async_embed(texts: list[str]) -> list[list[float]]:
        return deterministic_vectors(texts)

    def embedding_factory(_model_spec: str, *, sync: bool = False):
        return deterministic_vectors if sync else async_embed

    monkeypatch.setattr(kb, "_get_embedding_function", embedding_factory)

    repository = KnowledgeFileRepository()
    chunk_repository = KnowledgeChunkRepository()
    minio_client = get_minio_client()

    old_upload = await _upload(test_client, admin_headers, kb_id, filename, old_content)
    assert old_upload.status_code == 200, old_upload.text
    old_add = await _add_uploaded(test_client, admin_headers, kb_id, old_upload.json(), "prompt")
    assert old_add.status_code == 200, old_add.text
    old_file_id = old_add.json()["items"][0]["file_id"]

    old_record = await repository.get_by_file_id(old_file_id)
    assert old_record is not None
    assert old_record.is_active is True

    parsed_old = await kb.parse_file(kb_id, old_file_id)
    assert parsed_old["status"] == "parsed"
    indexed_old = await kb.index_file(kb_id, old_file_id)
    assert indexed_old["status"] == "indexed"

    old_record = await repository.get_by_file_id(old_file_id)
    assert old_record is not None
    assert old_record.is_active is True
    assert old_record.markdown_file
    assert old_record.path
    assert old_record.chunk_count > 0
    assert old_record.token_count > 0

    old_chunks_before = await chunk_repository.list_by_file_id(old_file_id)
    old_chunk_ids = [chunk.chunk_id for chunk in old_chunks_before]
    assert old_chunk_ids
    assert await _milvus_file_rows(collection, old_file_id)

    old_markdown_file = old_record.markdown_file
    old_original_path = old_record.path
    old_chunk_count = old_record.chunk_count
    old_token_count = old_record.token_count
    old_preview_before = await kb.read_file_preview(kb_id, old_file_id)
    assert old_preview_before["supported"] is True
    assert old_marker in str(old_preview_before["content"])

    old_original_before = await kb.get_file_download(kb_id, old_file_id, variant="original")
    old_markdown_before = await kb.get_file_download(kb_id, old_file_id, variant="parsed")
    assert old_marker.encode() in old_original_before["content"]
    assert old_marker.encode() in old_markdown_before["content"]

    replacement_upload = await _upload(
        test_client,
        admin_headers,
        kb_id,
        filename,
        new_content,
        "replace",
        old_file_id,
    )
    assert replacement_upload.status_code == 200, replacement_upload.text
    replacement_add = await _add_uploaded(
        test_client,
        admin_headers,
        kb_id,
        replacement_upload.json(),
        "replace",
        old_file_id,
    )
    assert replacement_add.status_code == 200, replacement_add.text
    new_file_id = replacement_add.json()["items"][0]["file_id"]

    new_record = await repository.get_by_file_id(new_file_id)
    assert new_record is not None
    assert new_record.is_active is False
    assert new_record.replacement_target_file_id == old_file_id

    parsed_new = await kb.parse_file(kb_id, new_file_id)
    assert parsed_new["status"] == "parsed"

    activation_reached = asyncio.Event()
    allow_activation = asyncio.Event()
    cleanup_task_ids: list[str] = []
    original_activate = DocumentIngestionService.activate_replacement
    original_enqueue = DocumentIngestionService.enqueue_replacement_cleanup

    async def paused_activate(self, *, kb_id: str, new_file_id: str, old_file_id: str) -> None:
        activation_reached.set()
        await allow_activation.wait()
        await original_activate(
            self,
            kb_id=kb_id,
            new_file_id=new_file_id,
            old_file_id=old_file_id,
        )

    async def capture_cleanup_task(self, **kwargs) -> str:
        task_id = await original_enqueue(self, **kwargs)
        cleanup_task_ids.append(task_id)
        return task_id

    monkeypatch.setattr(DocumentIngestionService, "activate_replacement", paused_activate)
    monkeypatch.setattr(DocumentIngestionService, "enqueue_replacement_cleanup", capture_cleanup_task)

    index_task = asyncio.create_task(kb.index_file(kb_id, new_file_id))
    try:
        await asyncio.wait_for(activation_reached.wait(), timeout=60)

        new_record = await repository.get_by_file_id(new_file_id)
        old_record = await repository.get_by_file_id(old_file_id)
        assert new_record is not None
        assert old_record is not None
        assert new_record.is_active is False
        assert old_record.is_active is True
        assert await kb.verify_file_vectors(kb_id, new_file_id) is True
        assert await _milvus_file_rows(collection, old_file_id)
        assert await _milvus_file_rows(collection, new_file_id)

        for search_mode in ("vector", "keyword", "hybrid"):
            before_switch = await kb.aquery(
                new_marker,
                kb_id,
                search_mode=search_mode,
                final_top_k=10,
                similarity_threshold=-1.0,
            )
            assert new_file_id not in _result_file_ids(before_switch)

        allow_activation.set()
        indexed_new = await asyncio.wait_for(index_task, timeout=60)
        assert indexed_new["status"] == "indexed"

        new_record = await repository.get_by_file_id(new_file_id)
        old_record = await repository.get_by_file_id(old_file_id)
        assert new_record is not None
        assert old_record is not None
        assert new_record.is_active is True
        assert new_record.previous_version_id == old_file_id
        assert old_record.is_active is False
        assert old_record.superseded_at is not None
        assert cleanup_task_ids

        await _wait_for_replacement_cleanup(
            repository,
            collection,
            new_file_id=new_file_id,
            old_file_id=old_file_id,
        )

        assert not await _milvus_file_rows(collection, old_file_id)
        assert await _milvus_file_rows(collection, new_file_id)

        old_chunks_after = await chunk_repository.list_by_file_id(old_file_id)
        assert [chunk.chunk_id for chunk in old_chunks_after] == old_chunk_ids
        for chunk_id in old_chunk_ids:
            historical_chunk = await chunk_repository.get_by_chunk_id(chunk_id)
            assert historical_chunk is not None
            assert historical_chunk.file_id == old_file_id

        old_record = await repository.get_by_file_id(old_file_id)
        assert old_record is not None
        assert old_record.markdown_file == old_markdown_file
        assert old_record.path == old_original_path
        assert old_record.chunk_count == old_chunk_count
        assert old_record.token_count == old_token_count

        for stored_path in (old_record.markdown_file, old_record.path):
            bucket_name, object_name = parse_minio_url(stored_path)
            assert await minio_client.astat_file(bucket_name, object_name) is not None

        old_preview_after = await kb.read_file_preview(kb_id, old_file_id)
        assert old_preview_after["supported"] is True
        assert old_marker in str(old_preview_after["content"])
        old_original_after = await kb.get_file_download(kb_id, old_file_id, variant="original")
        old_markdown_after = await kb.get_file_download(kb_id, old_file_id, variant="parsed")
        assert old_marker.encode() in old_original_after["content"]
        assert old_marker.encode() in old_markdown_after["content"]

        for search_mode in ("vector", "keyword", "hybrid"):
            after_switch = await kb.aquery(
                new_marker,
                kb_id,
                search_mode=search_mode,
                final_top_k=10,
                similarity_threshold=-1.0,
            )
            assert new_file_id in _result_file_ids(after_switch)

        await process_document_replacement_cleanup(
            {"job_try": 1},
            kb_id,
            new_file_id,
            old_file_id,
            cleanup_task_ids[0],
        )
        assert not await _milvus_file_rows(collection, old_file_id)
        assert [chunk.chunk_id for chunk in await chunk_repository.list_by_file_id(old_file_id)] == old_chunk_ids
        bucket_name, object_name = parse_minio_url(old_markdown_file)
        assert await minio_client.astat_file(bucket_name, object_name) is not None
        bucket_name, object_name = parse_minio_url(old_original_path)
        assert await minio_client.astat_file(bucket_name, object_name) is not None
    finally:
        allow_activation.set()
        if not index_task.done():
            with suppress(Exception):
                await asyncio.wait_for(index_task, timeout=60)
