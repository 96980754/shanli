"""管理员处理知识缺口时的联网补答服务。"""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlparse

from sqlalchemy.ext.asyncio import AsyncSession
from tavily import AsyncTavilyClient

from yuxi.repositories.curated_qa_repository import CuratedQARepository
from yuxi.repositories.knowledge_gap_repository import KnowledgeGapRepository
from yuxi.services.curated_qa_service import CuratedQAService


class KnowledgeGapWebSearchService:
    """仅供管理员显式触发；不会改变普通问答的严格拒答策略。"""

    MAX_SOURCES = 5

    @staticmethod
    def _build_client() -> AsyncTavilyClient:
        api_key = str(os.getenv("TAVILY_API_KEY") or "").strip()
        if not api_key:
            raise ValueError("未配置 TAVILY_API_KEY，暂时无法联网补答")
        # 占位注释（如 "# 获取搜索服务的 api key 请访问 ..."）能通过“非空”判断，但会作为
        # Authorization: Bearer 头触发 httpx 的 ascii 编码错误，把 500 误报成联网失败。
        # 校验 key 必须是单个纯 ASCII token，否则给出清晰配置错误。
        if api_key.startswith("#") or not api_key.isascii() or any(ch.isspace() for ch in api_key):
            raise ValueError("TAVILY_API_KEY 配置无效（疑似占位或含非 ASCII/空白），请在 .env 配置真实 API Key")
        return AsyncTavilyClient(api_key=api_key)

    @staticmethod
    def _normalize_sources(raw_results: object) -> list[dict[str, Any]]:
        if not isinstance(raw_results, list):
            return []

        sources: list[dict[str, Any]] = []
        for item in raw_results[: KnowledgeGapWebSearchService.MAX_SOURCES]:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url") or "").strip()
            if not url or urlparse(url).scheme not in {"http", "https"}:
                continue
            sources.append(
                {
                    "title": str(item.get("title") or url).strip()[:300],
                    "url": url,
                    "content": str(item.get("content") or "").strip()[:2000],
                    "score": item.get("score"),
                }
            )
        return sources

    async def search(self, session: AsyncSession, gap_id: int) -> dict[str, Any] | None:
        gap = await KnowledgeGapRepository(session).get(gap_id)
        if gap is None:
            return None

        client = self._build_client()
        try:
            response = await client.search(
                query=gap.question,
                search_depth="advanced",
                include_answer=True,
                include_raw_content=False,
                max_results=self.MAX_SOURCES,
            )
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError("联网搜索失败，请稍后重试") from exc

        if not isinstance(response, dict):
            raise RuntimeError("联网搜索返回格式异常")

        draft_answer = str(response.get("answer") or "").strip()
        return {
            "gap_id": gap.id,
            "question": gap.question,
            "agent_slug": gap.agent_slug,
            "draft_answer": draft_answer,
            "sources": self._normalize_sources(response.get("results")),
        }

    async def save_answer(
        self,
        session: AsyncSession,
        *,
        gap_id: int,
        answer: str,
        operator_uid: str,
        sources: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any] | None:
        gap_repo = KnowledgeGapRepository(session)
        gap = await gap_repo.get(gap_id)
        if gap is None:
            return None

        normalized_answer = CuratedQAService.normalize_answer(answer)
        qa_pair = await CuratedQARepository(session).upsert(
            agent_slug=gap.agent_slug,
            question=gap.question,
            answer=normalized_answer,
            operator_uid=str(operator_uid),
            source_type="knowledge_gap",
            source_message_id=gap.assistant_message_id,
        )

        normalized_sources = self._normalize_sources(sources or [])
        note_parts = [f"联网补答已确认并保存为人工问答对 #{qa_pair.id}"]
        if normalized_sources:
            source_urls = [item["url"] for item in normalized_sources[:3]]
            note_parts.append("参考来源：" + "；".join(source_urls))
        resolution_note = "\n".join(note_parts)[:2000]

        updated_gap = await gap_repo.update_status(
            gap.id,
            status="resolved",
            resolution_note=resolution_note,
            operator_uid=str(operator_uid),
        )
        await session.commit()
        await session.refresh(qa_pair)

        gap_dict = updated_gap.to_dict() if updated_gap else None
        if gap_dict is not None:
            # 刚保存了问答对，has_answer 必然为真（assistant_message_id 缺失时除外）
            gap_dict["has_answer"] = bool(gap.assistant_message_id)

        return {
            "qa_pair": qa_pair.to_dict(),
            "gap": gap_dict,
        }
