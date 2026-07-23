"""Permission-aware search across every knowledge base available to a user."""

from __future__ import annotations

import asyncio
from typing import Any

from yuxi.knowledge.runtime import knowledge_base
from yuxi.permissions.knowledge import KnowledgePermissionService
from yuxi.utils import logger


class GlobalKnowledgeSearchService:
    """Aggregate existing per-KB retrieval without bypassing ``can_search``."""

    def __init__(self, permission_service: KnowledgePermissionService | None = None):
        self.permission_service = permission_service or KnowledgePermissionService()

    async def search(self, user: Any, query: str, limit: int = 10) -> list[dict]:
        query = query.strip()
        if not query:
            return []

        databases = await knowledge_base.get_databases_by_uid(user.uid)
        candidates = databases.get("databases", [])
        context = {"uid": user.uid, "role": user.role, "department_id": user.department_id}
        allowed = [
            database
            for database in candidates
            if await self.permission_service.has_permission(context, database["kb_id"], "can_search")
        ]

        async def search_one(database: dict) -> tuple[dict, list[dict]]:
            try:
                results = await knowledge_base.aquery(
                    query,
                    kb_id=database["kb_id"],
                    final_top_k=max(limit, 10),
                )
                return database, results or []
            except Exception as exc:
                logger.warning("Global search skipped knowledge base %s: %s", database["kb_id"], exc)
                return database, []

        grouped = await asyncio.gather(*(search_one(database) for database in allowed))
        merged: list[dict] = []
        for database, results in grouped:
            for rank, result in enumerate(results, start=1):
                item = dict(result)
                item["kb_id"] = database["kb_id"]
                item["kb_name"] = database.get("name") or database["kb_id"]
                # Scores from heterogeneous retrievers are not comparable. Reciprocal rank
                # fusion keeps the existing per-KB ranking while allowing one combined list.
                item["global_score"] = 1 / (60 + rank)
                merged.append(item)

        merged.sort(key=lambda item: item["global_score"], reverse=True)
        return merged[:limit]
