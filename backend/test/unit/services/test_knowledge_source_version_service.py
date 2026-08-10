from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from yuxi.services.knowledge_source_version_service import KnowledgeSourceVersionService


def _version(
    file_id: str,
    version: int | None,
    *,
    current: bool = False,
    previous_version_id: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        file_id=file_id,
        filename="星河终端.docx",
        document_version=version,
        is_current=current,
        is_active=current,
        previous_version_id=previous_version_id,
        activated_at=datetime(2026, 8, 8 + (version or 1)),
        superseded_at=None,
        updated_at=None,
        created_at=datetime(2026, 8, 8 + (version or 1)),
        minio_url=f"minio://knowledgebases/{file_id}.docx",
        path=None,
    )


@pytest.mark.asyncio
async def test_source_versions_returns_empty_history_without_extra_queries():
    current = _version("file-v1", 1, current=True)
    repository = SimpleNamespace(
        list_version_chains_for_current_files=AsyncMock(return_value={"file-v1": [current]})
    )
    original_exists = AsyncMock(return_value=True)
    service = KnowledgeSourceVersionService(repository=repository, original_exists=original_exists)

    result = await service.list_for_current_files(kb_id="kb-1", file_ids=["file-v1", "file-v1"])

    assert result == [
        {
            "file_id": "file-v1",
            "filename": "星河终端.docx",
            "document_version": 1,
            "history_versions": [],
        }
    ]
    repository.list_version_chains_for_current_files.assert_awaited_once_with(
        kb_id="kb-1",
        file_ids=["file-v1"],
    )
    original_exists.assert_not_awaited()


@pytest.mark.asyncio
async def test_source_versions_sorts_multiple_history_versions():
    current = _version("file-v3", 3, current=True)
    v2 = _version("file-v2", 2)
    v1 = _version("file-v1", 1)
    repository = SimpleNamespace(
        list_version_chains_for_current_files=AsyncMock(
            return_value={"file-v3": [v1, current, v2]}
        )
    )

    service = KnowledgeSourceVersionService(
        repository=repository,
        original_exists=AsyncMock(return_value=True),
    )

    result = await service.list_for_current_files(kb_id="kb-1", file_ids=["file-v3"])

    assert result[0]["document_version"] == 3
    assert [item["file_id"] for item in result[0]["history_versions"]] == ["file-v2", "file-v1"]
    assert [item["document_version"] for item in result[0]["history_versions"]] == [2, 1]


@pytest.mark.asyncio
async def test_source_versions_skips_history_with_missing_original_object():
    current = _version("file-v2", 2, current=True)
    v1 = _version("file-v1", 1)
    repository = SimpleNamespace(
        list_version_chains_for_current_files=AsyncMock(return_value={"file-v2": [current, v1]})
    )
    service = KnowledgeSourceVersionService(
        repository=repository,
        original_exists=AsyncMock(return_value=False),
    )

    result = await service.list_for_current_files(kb_id="kb-1", file_ids=["file-v2"])

    assert result[0]["history_versions"] == []


@pytest.mark.asyncio
async def test_source_versions_derives_numbers_for_replacement_chain():
    v1 = _version("replacement-v1", None)
    v2 = _version("replacement-v2", None, previous_version_id="replacement-v1")
    current = _version("replacement-v3", None, current=True, previous_version_id="replacement-v2")
    repository = SimpleNamespace(
        list_version_chains_for_current_files=AsyncMock(
            return_value={"replacement-v3": [v2, v1, current]}
        )
    )
    service = KnowledgeSourceVersionService(
        repository=repository,
        original_exists=AsyncMock(return_value=True),
    )

    result = await service.list_for_current_files(kb_id="kb-1", file_ids=["replacement-v3"])

    assert result[0]["document_version"] == 3
    assert [item["document_version"] for item in result[0]["history_versions"]] == [2, 1]


@pytest.mark.asyncio
async def test_source_versions_ignores_default_one_for_replacement_current():
    history = _version("replacement-v1", 1)
    current = _version(
        "replacement-v2",
        1,
        current=True,
        previous_version_id="replacement-v1",
    )
    repository = SimpleNamespace(
        list_version_chains_for_current_files=AsyncMock(
            return_value={"replacement-v2": [current, history]}
        )
    )
    service = KnowledgeSourceVersionService(
        repository=repository,
        original_exists=AsyncMock(return_value=True),
    )

    result = await service.list_for_current_files(
        kb_id="kb-1",
        file_ids=["replacement-v2"],
    )

    assert result[0]["document_version"] == 2
    assert result[0]["history_versions"][0]["document_version"] == 1
