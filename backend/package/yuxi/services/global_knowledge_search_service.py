"""Permission-aware search across every knowledge base available to a user."""

from __future__ import annotations

import asyncio
from typing import Any

from yuxi.knowledge.runtime import knowledge_base
from yuxi.permissions.knowledge import KnowledgePermissionService
from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository
from yuxi.utils import logger

# 全库搜索融合的相关性下限。不同知识库的检索模式可能不同（vector/hybrid/keyword）：
# - vector（COSINE）与 hybrid 的 score 都是有界相似度/加权分，可直接跨库比较；
#   但各库默认 similarity_threshold=0.2 偏低，会带出大量低相关片段，这里在全局融合时
#   用一个更高的下限过滤"无关内容"。
# - keyword（BM25）的 bm25_score 无上界、跨库不可比，必须先在本库内归一化到 [0,1] 才能
#   与上面这类分数放进同一次排序（见 _kb_global_scores），否则它会恒沉底被截断。
VECTOR_RELEVANCE_FLOOR = 0.35


class GlobalKnowledgeSearchService:
    """Aggregate existing per-KB retrieval without bypassing ``can_search``."""

    def __init__(self, permission_service: KnowledgePermissionService | None = None):
        self.permission_service = permission_service or KnowledgePermissionService()

    async def search(self, user: Any, query: str, limit: int = 10) -> list[dict]:
        results, _ = await self.search_with_status(user, query, limit)
        return results

    async def search_with_status(self, user: Any, query: str, limit: int = 10) -> tuple[list[dict], bool]:
        """Return results and whether any permitted knowledge base could not be searched."""
        query = query.strip()
        if not query:
            return [], False

        databases = await knowledge_base.get_databases_by_uid(user.uid)
        candidates = databases.get("databases", [])
        context = {"uid": user.uid, "role": user.role, "department_id": user.department_id}
        allowed = [
            database
            for database in candidates
            if await self.permission_service.has_permission(context, database["kb_id"], "can_search")
        ]

        async def search_one(database: dict) -> tuple[dict, list[dict], bool]:
            try:
                results = await knowledge_base.aquery(
                    query,
                    kb_id=database["kb_id"],
                    final_top_k=max(limit, 10),
                )
                return database, results or [], False
            except Exception as exc:
                logger.warning("Global search skipped knowledge base %s: %s", database["kb_id"], exc)
                return database, [], True

        grouped = await asyncio.gather(*(search_one(database) for database in allowed))
        merged: list[dict] = []
        for database, results, _ in grouped:
            for result, global_score in zip(results, self._kb_global_scores(results)):
                if global_score is None:
                    continue
                item = dict(result)
                item["kb_id"] = database["kb_id"]
                item["kb_name"] = database.get("name") or database["kb_id"]
                item["global_score"] = global_score
                merged.append(item)

        merged.sort(key=lambda item: item["global_score"], reverse=True)
        final = merged[:limit]
        await self._enrich_file_paths(final)
        return final, any(search_failed for _, _, search_failed in grouped)

    @classmethod
    def _kb_global_scores(cls, results: list[dict]) -> list[float | None]:
        """把单库结果的分数换算成可跨库比较的 [0,1] 全局分；None 表示被相关性下限过滤。

        - 有界相似度（vector/hybrid 的 score）本身跨库可比，直接沿用，低于下限的过滤掉；
        - 无界分（keyword 的 bm25_score）与无分数字段（自定义后端）跨库不可比，
          在本库内归一化：有原始分时做 min-max，没有时按库内排名取相对位置。
          两类都落在 [0,1]，既保序又不会因为量级太小而恒沉底被截断。
        """
        normalized_indexes = [index for index, item in enumerate(results) if cls._needs_normalize(item)]
        scores: list[float | None] = [None] * len(results)
        if normalized_indexes:
            raws: list[float | None] = []
            for index in normalized_indexes:
                value = results[index].get("score")
                raws.append(float(value) if value is not None else None)
            known = [raw for raw in raws if raw is not None]
            low, high = (min(known), max(known)) if known else (0.0, 0.0)
            for rank, (index, raw) in enumerate(zip(normalized_indexes, raws), start=1):
                if raw is None:
                    scores[index] = (len(normalized_indexes) - rank + 1) / len(normalized_indexes)
                elif high > low:
                    scores[index] = (raw - low) / (high - low)
                else:
                    # 库内分数全都相同：非零视作该库的最优候选，零分视作无信号。
                    scores[index] = 1.0 if raw > 0 else 0.0

        for index, item in enumerate(results):
            if cls._needs_normalize(item):
                continue
            score = float(item["score"])
            scores[index] = score if score >= VECTOR_RELEVANCE_FLOOR else None
        return scores

    @staticmethod
    def _needs_normalize(item: dict) -> bool:
        """BM25 分数无上界，无 score 字段则无从比较：两类都要先在库内归一化。"""
        return "bm25_score" in item or item.get("score") is None

    @staticmethod
    async def _enrich_file_paths(items: list[dict]) -> None:
        """给每条结果补 file_name / file_dir，便于前端按「知识库→目录→文件」展示。"""
        file_ids = {
            str(item["metadata"]["file_id"])
            for item in items
            if item.get("metadata", {}).get("file_id")
        }
        if not file_ids:
            return
        repo = KnowledgeFileRepository()
        records = await repo.list_by_file_ids(list(file_ids))
        paths = await repo.build_document_display_paths(records)
        for item in items:
            file_id = str(item.get("metadata", {}).get("file_id") or "")
            display = paths.get(file_id)
            if not display:
                continue
            *folders, name = display.rsplit("/", 1)
            item["file_dir"] = "/".join(folders)
            item["file_name"] = name
