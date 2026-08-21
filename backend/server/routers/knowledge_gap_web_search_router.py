"""知识缺口联网补答管理接口。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from server.utils.auth_middleware import get_db, get_superadmin_user
from yuxi.services.knowledge_gap_web_search_service import KnowledgeGapWebSearchService
from yuxi.storage.postgres.models_business import User


knowledge_gap_web_search = APIRouter(prefix="/dashboard/knowledge-gaps", tags=["Dashboard"])


class KnowledgeGapSaveQARequest(BaseModel):
    answer: str = Field(..., min_length=1, max_length=20_000)
    sources: list[dict[str, Any]] = Field(default_factory=list, max_length=5)


@knowledge_gap_web_search.post("/{gap_id}/web-search")
async def search_knowledge_gap_answer(
    gap_id: int,
    current_user: User = Depends(get_superadmin_user),
    db: AsyncSession = Depends(get_db),
):
    del current_user
    try:
        result = await KnowledgeGapWebSearchService().search(db, gap_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="知识缺口不存在")
    return result


@knowledge_gap_web_search.post("/{gap_id}/save-qa")
async def save_knowledge_gap_answer(
    gap_id: int,
    payload: KnowledgeGapSaveQARequest,
    current_user: User = Depends(get_superadmin_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        result = await KnowledgeGapWebSearchService().save_answer(
            db,
            gap_id=gap_id,
            answer=payload.answer,
            operator_uid=str(current_user.uid),
            sources=payload.sources,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if result is None:
        raise HTTPException(status_code=404, detail="知识缺口不存在")
    return result
