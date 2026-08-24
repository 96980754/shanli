"""人工确认问答调优管理接口。"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from server.utils.auth_middleware import get_db, get_superadmin_user
from yuxi.services.curated_qa_service import CuratedQAService
from yuxi.storage.postgres.models_business import User


curated_qa_dashboard = APIRouter(prefix="/dashboard", tags=["Dashboard"])


class SaveCuratedQARequest(BaseModel):
    answer: str = Field(..., min_length=1, max_length=20_000)


@curated_qa_dashboard.get("/feedbacks/{feedback_id}/tuning-context")
async def get_feedback_tuning_context(
    feedback_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_superadmin_user),
):
    del current_user
    try:
        context = await CuratedQAService().get_feedback_context(db, feedback_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if context is None:
        raise HTTPException(status_code=404, detail="反馈记录不存在")
    return {"item": context}


@curated_qa_dashboard.put("/feedbacks/{feedback_id}/qa-pair")
async def save_feedback_qa_pair(
    feedback_id: int,
    payload: SaveCuratedQARequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_superadmin_user),
):
    try:
        item = await CuratedQAService().save_from_feedback(
            db,
            feedback_id=feedback_id,
            answer=payload.answer,
            operator_uid=str(current_user.uid),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if item is None:
        raise HTTPException(status_code=404, detail="反馈记录不存在")
    return {"item": item}
