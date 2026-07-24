from __future__ import annotations

from typing import Any

from sqlalchemy import func, select

from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_knowledge import KnowledgeBase, KnowledgeBaseCategory


class KnowledgeCategoryRepository:
    async def list_all(self) -> list[KnowledgeBaseCategory]:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(KnowledgeBaseCategory).order_by(
                    KnowledgeBaseCategory.sort_order,
                    func.lower(KnowledgeBaseCategory.name),
                    KnowledgeBaseCategory.id,
                )
            )
            return list(result.scalars().all())

    async def get_by_id(self, category_id: int) -> KnowledgeBaseCategory | None:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(KnowledgeBaseCategory).where(KnowledgeBaseCategory.id == category_id)
            )
            return result.scalar_one_or_none()

    async def get_by_normalized_name(self, name: str) -> KnowledgeBaseCategory | None:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(KnowledgeBaseCategory).where(func.lower(KnowledgeBaseCategory.name) == name.strip().lower())
            )
            return result.scalar_one_or_none()

    async def create(self, data: dict[str, Any]) -> KnowledgeBaseCategory:
        category = KnowledgeBaseCategory(**data)
        async with pg_manager.get_async_session_context() as session:
            session.add(category)
            await session.flush()
            await session.refresh(category)
        return category

    async def update(self, category_id: int, data: dict[str, Any]) -> KnowledgeBaseCategory | None:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(KnowledgeBaseCategory).where(KnowledgeBaseCategory.id == category_id)
            )
            category = result.scalar_one_or_none()
            if category is None:
                return None
            for key, value in data.items():
                setattr(category, key, value)
            await session.flush()
            await session.refresh(category)
            return category

    async def count_knowledge_bases(self, category_id: int) -> int:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(func.count()).select_from(KnowledgeBase).where(KnowledgeBase.category_id == category_id)
            )
            return int(result.scalar_one())

    async def delete(self, category_id: int) -> bool:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(KnowledgeBaseCategory).where(KnowledgeBaseCategory.id == category_id)
            )
            category = result.scalar_one_or_none()
            if category is None:
                return False
            await session.delete(category)
            await session.flush()
            return True
