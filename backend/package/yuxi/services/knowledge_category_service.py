from __future__ import annotations

from typing import Any

from sqlalchemy.exc import IntegrityError

from yuxi.repositories.knowledge_category_repository import KnowledgeCategoryRepository
from yuxi.storage.postgres.models_knowledge import KnowledgeBaseCategory


class KnowledgeCategoryError(Exception):
    def __init__(self, code: str, message: str, *, status_code: int, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}


class KnowledgeCategoryService:
    def __init__(self, repository: KnowledgeCategoryRepository | None = None):
        self.repository = repository or KnowledgeCategoryRepository()

    async def list_categories(self, *, include_usage_count: bool = False) -> list[dict[str, Any]]:
        categories = await self.repository.list_all()
        items = []
        for category in categories:
            usage_count = (
                await self.repository.count_knowledge_bases(category.id) if include_usage_count else None
            )
            items.append(self.serialize(category, usage_count=usage_count))
        return items

    async def require_category(self, category_id: int) -> KnowledgeBaseCategory:
        category = await self.repository.get_by_id(category_id)
        if category is None:
            raise KnowledgeCategoryError(
                "knowledge_category_not_found",
                "知识库分类不存在",
                status_code=404,
            )
        return category

    async def create_category(self, *, name: str, sort_order: int, actor_uid: str) -> dict[str, Any]:
        normalized_name = self._normalize_name(name)
        if await self.repository.get_by_normalized_name(normalized_name):
            self._raise_duplicate_name()
        try:
            category = await self.repository.create(
                {
                    "name": normalized_name,
                    "sort_order": sort_order,
                    "created_by": actor_uid,
                    "updated_by": actor_uid,
                }
            )
        except IntegrityError as exc:
            self._raise_duplicate_name(exc)
        return self.serialize(category, usage_count=0)

    async def update_category(
        self,
        category_id: int,
        *,
        name: str | None,
        sort_order: int | None,
        actor_uid: str,
    ) -> dict[str, Any]:
        category = await self.require_category(category_id)
        data: dict[str, Any] = {"updated_by": actor_uid}

        if name is not None:
            normalized_name = self._normalize_name(name)
            if category.is_protected and normalized_name != category.name:
                raise KnowledgeCategoryError(
                    "knowledge_category_protected",
                    "系统默认分类不能重命名",
                    status_code=409,
                )
            duplicate = await self.repository.get_by_normalized_name(normalized_name)
            if duplicate is not None and duplicate.id != category_id:
                self._raise_duplicate_name()
            data["name"] = normalized_name
        if sort_order is not None:
            data["sort_order"] = sort_order

        try:
            updated = await self.repository.update(category_id, data)
        except IntegrityError as exc:
            self._raise_duplicate_name(exc)
        if updated is None:
            raise KnowledgeCategoryError(
                "knowledge_category_not_found",
                "知识库分类不存在",
                status_code=404,
            )
        usage_count = await self.repository.count_knowledge_bases(category_id)
        return self.serialize(updated, usage_count=usage_count)

    async def delete_category(self, category_id: int) -> None:
        category = await self.require_category(category_id)
        if category.is_protected:
            raise KnowledgeCategoryError(
                "knowledge_category_protected",
                "系统默认分类不能删除",
                status_code=409,
            )
        usage_count = await self.repository.count_knowledge_bases(category_id)
        if usage_count:
            self._raise_in_use(usage_count)
        try:
            deleted = await self.repository.delete(category_id)
        except IntegrityError as exc:
            usage_count = await self.repository.count_knowledge_bases(category_id)
            self._raise_in_use(usage_count or 1, exc)
        if not deleted:
            raise KnowledgeCategoryError(
                "knowledge_category_not_found",
                "知识库分类不存在",
                status_code=404,
            )

    @staticmethod
    def serialize(category: KnowledgeBaseCategory, *, usage_count: int | None = None) -> dict[str, Any]:
        item = {
            "id": category.id,
            "name": category.name,
            "sort_order": category.sort_order,
            "is_default": category.is_default,
            "is_protected": category.is_protected,
        }
        if usage_count is not None:
            item["usage_count"] = usage_count
        return item

    @staticmethod
    def _normalize_name(name: str) -> str:
        normalized = name.strip()
        if not normalized:
            raise KnowledgeCategoryError(
                "knowledge_category_invalid_name",
                "分类名称不能为空",
                status_code=400,
            )
        if len(normalized) > 64:
            raise KnowledgeCategoryError(
                "knowledge_category_invalid_name",
                "分类名称不能超过 64 个字符",
                status_code=400,
            )
        return normalized

    @staticmethod
    def _raise_duplicate_name(exc: Exception | None = None) -> None:
        error = KnowledgeCategoryError(
            "knowledge_category_name_conflict",
            "分类名称已存在",
            status_code=409,
        )
        if exc is None:
            raise error
        raise error from exc

    @staticmethod
    def _raise_in_use(usage_count: int, exc: Exception | None = None) -> None:
        error = KnowledgeCategoryError(
            "knowledge_category_in_use",
            "该分类仍被知识库使用，不能删除",
            status_code=409,
            details={"usage_count": usage_count},
        )
        if exc is None:
            raise error
        raise error from exc
