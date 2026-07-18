from __future__ import annotations

from typing import Any

from sqlalchemy import select

from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_knowledge import KnowledgeBasePermission


class KnowledgePermissionRepository:
    async def list_by_kb_id(self, kb_id: str) -> list[KnowledgeBasePermission]:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(KnowledgeBasePermission).where(KnowledgeBasePermission.kb_id == kb_id)
            )
            return list(result.scalars().all())

    async def upsert(self, data: dict[str, Any]) -> KnowledgeBasePermission:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(KnowledgeBasePermission).where(
                    KnowledgeBasePermission.kb_id == data["kb_id"],
                    KnowledgeBasePermission.subject_type == data["subject_type"],
                    KnowledgeBasePermission.subject_id == data["subject_id"],
                )
            )
            permission = result.scalar_one_or_none()
            if permission is None:
                permission = KnowledgeBasePermission(**data)
                session.add(permission)
                return permission
            for key, value in data.items():
                setattr(permission, key, value)
            return permission

    async def delete(self, permission_id: int) -> bool:
        async with pg_manager.get_async_session_context() as session:
            permission = await session.get(KnowledgeBasePermission, permission_id)
            if permission is None:
                return False
            await session.delete(permission)
            return True
