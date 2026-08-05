from __future__ import annotations

from typing import Any

from sqlalchemy import func, select

from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_knowledge import KnowledgeBase, KnowledgeBaseCategory


class KnowledgeBaseRepository:
    async def get_all(self) -> list[KnowledgeBase]:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(select(KnowledgeBase))
            return list(result.scalars().all())

    async def get_all_with_category(
        self, category_id: int | None = None
    ) -> list[tuple[KnowledgeBase, KnowledgeBaseCategory]]:
        statement = select(KnowledgeBase, KnowledgeBaseCategory).join(
            KnowledgeBaseCategory,
            KnowledgeBase.category_id == KnowledgeBaseCategory.id,
        )
        if category_id is not None:
            statement = statement.where(KnowledgeBase.category_id == category_id)
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(statement)
            return list(result.all())

    async def get_by_kb_id_with_category(
        self, kb_id: str
    ) -> tuple[KnowledgeBase, KnowledgeBaseCategory] | None:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(KnowledgeBase, KnowledgeBaseCategory)
                .join(KnowledgeBaseCategory, KnowledgeBase.category_id == KnowledgeBaseCategory.id)
                .where(KnowledgeBase.kb_id == kb_id)
            )
            return result.one_or_none()

    async def get_by_kb_id(self, kb_id: str) -> KnowledgeBase | None:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(select(KnowledgeBase).where(KnowledgeBase.kb_id == kb_id))
            return result.scalar_one_or_none()

    async def create(self, data: dict[str, Any]) -> KnowledgeBase:
        kb = KnowledgeBase(**data)
        async with pg_manager.get_async_session_context() as session:
            session.add(kb)
        return kb

    async def update(self, kb_id: str, data: dict[str, Any]) -> KnowledgeBase | None:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(select(KnowledgeBase).where(KnowledgeBase.kb_id == kb_id))
            kb = result.scalar_one_or_none()
            if kb is None:
                return None
            for key, value in data.items():
                setattr(kb, key, value)
        return kb

    async def list_ontology_config_references(
        self,
        registry_id: str,
        version: str,
        digest: str,
    ) -> list[dict[str, str]]:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(KnowledgeBase.kb_id, KnowledgeBase.name).where(
                    func.jsonb_extract_path_text(
                        KnowledgeBase.additional_params,
                        "graph_build_config",
                        "extractor_options",
                        "ontology_registry_id",
                    )
                    == registry_id,
                    func.jsonb_extract_path_text(
                        KnowledgeBase.additional_params,
                        "graph_build_config",
                        "extractor_options",
                        "ontology_version",
                    )
                    == version,
                    func.jsonb_extract_path_text(
                        KnowledgeBase.additional_params,
                        "graph_build_config",
                        "extractor_options",
                        "ontology_digest",
                    )
                    == digest,
                )
            )
            return [{"kb_id": str(kb_id), "name": str(name)} for kb_id, name in result.all()]

    async def delete(self, kb_id: str) -> None:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(select(KnowledgeBase).where(KnowledgeBase.kb_id == kb_id))
            kb = result.scalar_one_or_none()
            if kb is not None:
                await session.delete(kb)
