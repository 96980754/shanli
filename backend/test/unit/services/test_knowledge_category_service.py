from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.exc import IntegrityError

from yuxi.services.knowledge_category_service import KnowledgeCategoryError, KnowledgeCategoryService


def _category(**overrides):
    data = {
        "id": 1,
        "name": "产品资料",
        "sort_order": 10,
        "is_default": False,
        "is_protected": False,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


@pytest.mark.asyncio
async def test_create_category_trims_name_and_serializes_usage_count():
    repository = SimpleNamespace(
        get_by_normalized_name=AsyncMock(return_value=None),
        create=AsyncMock(return_value=_category(name="产品资料")),
    )
    service = KnowledgeCategoryService(repository)

    item = await service.create_category(name="  产品资料  ", sort_order=10, actor_uid="user-1")

    repository.get_by_normalized_name.assert_awaited_once_with("产品资料")
    repository.create.assert_awaited_once_with(
        {
            "name": "产品资料",
            "sort_order": 10,
            "created_by": "user-1",
            "updated_by": "user-1",
        }
    )
    assert item["usage_count"] == 0


@pytest.mark.asyncio
async def test_create_category_rejects_case_insensitive_duplicate():
    repository = SimpleNamespace(get_by_normalized_name=AsyncMock(return_value=_category()))
    service = KnowledgeCategoryService(repository)

    with pytest.raises(KnowledgeCategoryError) as exc_info:
        await service.create_category(name="产品资料", sort_order=0, actor_uid="user-1")

    assert exc_info.value.code == "knowledge_category_name_conflict"
    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_protected_category_cannot_be_renamed_or_deleted():
    repository = SimpleNamespace(get_by_id=AsyncMock(return_value=_category(is_protected=True)))
    service = KnowledgeCategoryService(repository)

    with pytest.raises(KnowledgeCategoryError) as rename_error:
        await service.update_category(1, name="新名称", sort_order=None, actor_uid="user-1")
    with pytest.raises(KnowledgeCategoryError) as delete_error:
        await service.delete_category(1)

    assert rename_error.value.code == "knowledge_category_protected"
    assert delete_error.value.code == "knowledge_category_protected"


@pytest.mark.asyncio
async def test_delete_category_rejects_in_use_category():
    repository = SimpleNamespace(
        get_by_id=AsyncMock(return_value=_category()),
        count_knowledge_bases=AsyncMock(return_value=3),
    )
    service = KnowledgeCategoryService(repository)

    with pytest.raises(KnowledgeCategoryError) as exc_info:
        await service.delete_category(1)

    assert exc_info.value.code == "knowledge_category_in_use"
    assert exc_info.value.details == {"usage_count": 3}


@pytest.mark.asyncio
async def test_delete_category_maps_foreign_key_race_to_in_use():
    repository = SimpleNamespace(
        get_by_id=AsyncMock(return_value=_category()),
        count_knowledge_bases=AsyncMock(side_effect=[0, 1]),
        delete=AsyncMock(side_effect=IntegrityError("delete", {}, Exception("fk"))),
    )
    service = KnowledgeCategoryService(repository)

    with pytest.raises(KnowledgeCategoryError) as exc_info:
        await service.delete_category(1)

    assert exc_info.value.code == "knowledge_category_in_use"
    assert exc_info.value.details == {"usage_count": 1}
