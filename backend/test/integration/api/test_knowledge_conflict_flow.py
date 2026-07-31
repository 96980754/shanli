from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from yuxi.repositories.knowledge_chunk_repository import KnowledgeChunkRepository
from yuxi.repositories.knowledge_conflict_repository import KnowledgeConflictRepository
from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository
from yuxi.storage.postgres.manager import pg_manager

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


def _assertion_payload(
    *,
    file_id: str,
    chunk_id: str,
    evidence: str,
    predicate: str,
    raw_value,
    value_type: str,
    product_version: str = "V1",
    unit: str | None = None,
) -> dict:
    return {
        "entity_type": "Specification",
        "entity_name": "Shanli Conflict Demo",
        "predicate": predicate,
        "raw_value": raw_value,
        "value_type": value_type,
        "unit": unit,
        "product_version": product_version,
        "file_id": file_id,
        "chunk_id": chunk_id,
        "evidence": evidence,
        "extraction_method": "manual",
        "confidence": 1.0,
    }


async def test_product_specification_conflict_review_flow(
    knowledge_database,
    knowledge_router_app,
    admin_headers,
    admin_user,
    view_only_user,
) -> None:
    pg_manager.initialize()
    await pg_manager.ensure_knowledge_schema()
    kb_id = knowledge_database["kb_id"]
    unique = uuid.uuid4().hex
    file_id = f"file-conflict-{unique}"
    chunk_id = f"chunk-conflict-{unique}"
    evidence = "Shanli Conflict Demo V1 最大并发用户数为 100，支持 Windows 和 Linux。V2 最大并发用户数为 200，重量为 1000 g。"

    await KnowledgeFileRepository().upsert(
        file_id,
        {
            "kb_id": kb_id,
            "filename": f"conflict-{unique}.md",
            "original_filename": f"conflict-{unique}.md",
            "file_type": "md",
            "path": f"pytest/{file_id}.md",
            "markdown_file": f"minio://pytest/{file_id}.md",
            "status": "indexed",
            "content_hash": unique,
            "file_size": len(evidence.encode()),
            "chunk_count": 1,
            "token_count": 20,
            "cleaning_version": 1,
            "is_active": True,
            "is_folder": False,
            "created_by": admin_user["uid"],
        },
    )
    await KnowledgeChunkRepository().batch_upsert(
        [
            {
                "chunk_id": chunk_id,
                "file_id": file_id,
                "kb_id": kb_id,
                "chunk_index": 0,
                "content": evidence,
                "source_metadata": {"page_number": 1},
            }
        ]
    )

    async with AsyncClient(
        transport=ASGITransport(app=knowledge_router_app),
        base_url="http://test",
        headers=admin_headers,
    ) as client:
        first = await client.post(
            f"/api/knowledge/databases/{kb_id}/assertions/evaluate",
            json=_assertion_payload(
                file_id=file_id,
                chunk_id=chunk_id,
                evidence="最大并发用户数为 100",
                predicate="max_concurrent_users",
                raw_value=100,
                value_type="integer",
            ),
        )
        assert first.status_code == 200, first.text
        first_conflict = first.json()
        assert first_conflict["classification"] == "LINK_AMBIGUOUS"
        create_entity = await client.post(
            f"/api/knowledge/databases/{kb_id}/conflicts/{first_conflict['conflict_id']}/resolve",
            json={
                "resolution": "create_new_entity",
                "version": first_conflict["version"],
                "reason": "创建受审核的演示规格实体",
            },
        )
        assert create_entity.status_code == 200, create_entity.text
        assert create_entity.json()["incoming_assertion"]["status"] == "published"

        duplicate = await client.post(
            f"/api/knowledge/databases/{kb_id}/assertions/evaluate",
            json=_assertion_payload(
                file_id=file_id,
                chunk_id=chunk_id,
                evidence="最大并发用户数为 100",
                predicate="max_concurrent_users",
                raw_value="100",
                value_type="integer",
            ),
        )
        assert duplicate.status_code == 200, duplicate.text
        assert duplicate.json()["classification"] == "DUPLICATE"

        windows = await client.post(
            f"/api/knowledge/databases/{kb_id}/assertions/evaluate",
            json=_assertion_payload(
                file_id=file_id,
                chunk_id=chunk_id,
                evidence="支持 Windows",
                predicate="supported_os",
                raw_value="Windows",
                value_type="enum",
            ),
        )
        assert windows.status_code == 200, windows.text
        assert windows.json()["classification"] == "COMPLETION"
        publish_windows = await client.post(
            f"/api/knowledge/databases/{kb_id}/conflicts/{windows.json()['conflict_id']}/resolve",
            json={
                "resolution": "mark_as_completion",
                "version": windows.json()["version"],
                "reason": "确认正式支持 Windows",
            },
        )
        assert publish_windows.status_code == 200, publish_windows.text

        linux = await client.post(
            f"/api/knowledge/databases/{kb_id}/assertions/evaluate",
            json=_assertion_payload(
                file_id=file_id,
                chunk_id=chunk_id,
                evidence="Linux",
                predicate="supported_os",
                raw_value="Linux",
                value_type="enum",
            ),
        )
        assert linux.status_code == 200, linux.text
        assert linux.json()["classification"] == "COMPLETION"

        update = await client.post(
            f"/api/knowledge/databases/{kb_id}/assertions/evaluate",
            json=_assertion_payload(
                file_id=file_id,
                chunk_id=chunk_id,
                evidence="V2 最大并发用户数为 200",
                predicate="max_concurrent_users",
                raw_value=200,
                value_type="integer",
                product_version="V2",
            ),
        )
        assert update.status_code == 200, update.text
        assert update.json()["classification"] == "UPDATE"

        conflict = await client.post(
            f"/api/knowledge/databases/{kb_id}/assertions/evaluate",
            json=_assertion_payload(
                file_id=file_id,
                chunk_id=chunk_id,
                evidence="V2 最大并发用户数为 200",
                predicate="max_concurrent_users",
                raw_value=200,
                value_type="integer",
            ),
        )
        assert conflict.status_code == 200, conflict.text
        conflict_payload = conflict.json()
        assert conflict_payload["classification"] == "CONFLICT"
        assert conflict_payload["existing_assertions"][0]["raw_value"] == 100

        async with AsyncClient(
            transport=ASGITransport(app=knowledge_router_app),
            base_url="http://test",
            headers=view_only_user["headers"],
        ) as readonly_client:
            readable = await readonly_client.get(
                f"/api/knowledge/databases/{kb_id}/conflicts"
            )
            assert readable.status_code == 200, readable.text
            assert readable.json()["readonly"] is True
            forbidden = await readonly_client.post(
                f"/api/knowledge/databases/{kb_id}/conflicts/{conflict_payload['conflict_id']}/resolve",
                json={
                    "resolution": "use_new",
                    "version": conflict_payload["version"],
                },
            )
            assert forbidden.status_code == 403, forbidden.text

        cross_kb = await client.get(
            f"/api/knowledge/databases/kb_{uuid.uuid4().hex}/conflicts/{conflict_payload['conflict_id']}"
        )
        assert cross_kb.status_code == 404, cross_kb.text

        resolved = await client.post(
            f"/api/knowledge/databases/{kb_id}/conflicts/{conflict_payload['conflict_id']}/resolve",
            json={
                "resolution": "use_new",
                "version": conflict_payload["version"],
                "reason": "演示选择经过证据确认的新值",
            },
        )
        assert resolved.status_code == 200, resolved.text
        resolved_payload = resolved.json()
        assert resolved_payload["status"] == "resolved"
        assert resolved_payload["incoming_assertion"]["status"] == "published"
        assert resolved_payload["publish_status"] == "pending"

        repeated = await client.post(
            f"/api/knowledge/databases/{kb_id}/conflicts/{conflict_payload['conflict_id']}/resolve",
            json={
                "resolution": "use_new",
                "version": conflict_payload["version"],
                "reason": "重复请求不应再次改变版本",
            },
        )
        assert repeated.status_code == 200, repeated.text
        assert repeated.json()["version"] == resolved_payload["version"]

        stale_overwrite = await client.post(
            f"/api/knowledge/databases/{kb_id}/conflicts/{conflict_payload['conflict_id']}/resolve",
            json={
                "resolution": "keep_old",
                "version": conflict_payload["version"],
                "reason": "旧版本请求不得覆盖已经完成的人工裁决",
            },
        )
        assert stale_overwrite.status_code == 409, stale_overwrite.text
        assert stale_overwrite.json()["detail"]["code"] == "knowledge_conflict_version"

        detail = await client.get(
            f"/api/knowledge/databases/{kb_id}/conflicts/{conflict_payload['conflict_id']}"
        )
        assert detail.status_code == 200, detail.text
        assert (
            detail.json()["incoming_assertion"]["evidence"] == "V2 最大并发用户数为 200"
        )
        assert detail.json()["resolution"] == "use_new"
        assert detail.json()["resolved_by"] == admin_user["uid"]

    entity_id = resolved_payload["entity_id"]
    published = await KnowledgeConflictRepository().list_published_assertions(
        kb_id=kb_id,
        entity_id=entity_id,
        predicate="max_concurrent_users",
    )
    assert [item.normalized_value for item in published] == [200]
