"""HTTP boundary for the flowchart-ingestion draft lifecycle."""

from __future__ import annotations

import traceback

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from yuxi.services.document_ingestion_service import (
    DuplicateConflictError,
    DuplicateStrategyError,
    InvalidReplacementTargetError,
    ReplacementInProgressError,
)
from yuxi.services.flowchart_ingestion_service import (
    FlowchartImmutable,
    FlowchartIngestionError,
    FlowchartIngestionService,
    FlowchartNotFound,
    FlowchartRevisionConflict,
    FlowchartStateConflict,
)
from yuxi.storage.postgres.models_business import User
from yuxi.utils import logger

from server.routers.knowledge_router import (
    _ensure_database_supports_documents,
    _has_kb_permission,
    _request_uses_replace,
    _require_kb_permission,
)
from server.utils.auth_middleware import get_required_user

flowcharts = APIRouter(prefix="/knowledge", tags=["knowledge-flowcharts"])


class FlowchartCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file_path: str = Field(min_length=1)
    params: dict = Field(default_factory=dict)


class FlowchartDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=0)
    semantic_markdown: str = Field(min_length=1)


class FlowchartRevisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=0)


def _raise_flowchart_http_error(error: Exception) -> None:
    if isinstance(error, FlowchartNotFound):
        raise HTTPException(status_code=404, detail=str(error)) from error
    if isinstance(error, (FlowchartRevisionConflict, FlowchartImmutable, FlowchartStateConflict)):
        raise HTTPException(
            status_code=409,
            detail={"code": getattr(error, "code", "FLOWCHART_STATE_CONFLICT"), "message": str(error)},
        ) from error
    if isinstance(error, (DuplicateConflictError, ReplacementInProgressError, InvalidReplacementTargetError)):
        detail = getattr(error, "detail", str(error))
        raise HTTPException(status_code=409, detail=detail) from error
    if isinstance(error, (FlowchartIngestionError, DuplicateStrategyError)):
        raise HTTPException(
            status_code=400,
            detail={"code": getattr(error, "code", "INVALID_FLOWCHART_PDF"), "message": str(error)},
        ) from error
    if isinstance(error, HTTPException):
        raise error
    logger.error("流程图操作失败: {}\n{}", error, traceback.format_exc())
    raise HTTPException(status_code=500, detail="流程图操作失败") from error


@flowcharts.post("/databases/{kb_id}/flowcharts")
async def create_flowchart(
    kb_id: str,
    request: FlowchartCreateRequest,
    current_user: User = Depends(get_required_user),
):
    await _require_kb_permission(current_user, kb_id, "can_upload")
    if _request_uses_replace(request.params):
        await _require_kb_permission(current_user, kb_id, "can_manage")
    await _ensure_database_supports_documents(kb_id, "流程图上传")
    try:
        return await FlowchartIngestionService().create(
            kb_id=kb_id,
            file_path=request.file_path,
            params=request.params,
            operator_id=current_user.uid,
        )
    except Exception as error:  # noqa: BLE001
        _raise_flowchart_http_error(error)


@flowcharts.get("/databases/{kb_id}/flowcharts/{file_id}/preview")
async def get_flowchart_preview(
    kb_id: str,
    file_id: str,
    current_user: User = Depends(get_required_user),
):
    await _require_kb_permission(current_user, kb_id, "can_view")
    await _ensure_database_supports_documents(kb_id, "流程图预览")
    try:
        payload = await FlowchartIngestionService().get_preview(kb_id=kb_id, file_id=file_id)
        payload["readonly"] = payload["confirmed_at"] is not None or not await _has_kb_permission(
            current_user, kb_id, "can_manage"
        )
        return payload
    except Exception as error:  # noqa: BLE001
        _raise_flowchart_http_error(error)


@flowcharts.put("/databases/{kb_id}/flowcharts/{file_id}/draft")
async def update_flowchart_draft(
    kb_id: str,
    file_id: str,
    request: FlowchartDraftRequest,
    current_user: User = Depends(get_required_user),
):
    await _require_kb_permission(current_user, kb_id, "can_manage")
    await _ensure_database_supports_documents(kb_id, "保存流程图草稿")
    try:
        payload = await FlowchartIngestionService().update_draft(
            kb_id=kb_id,
            file_id=file_id,
            expected_revision=request.expected_revision,
            semantic_markdown=request.semantic_markdown,
            operator_id=current_user.uid,
        )
        payload["readonly"] = False
        return payload
    except Exception as error:  # noqa: BLE001
        _raise_flowchart_http_error(error)


@flowcharts.post("/databases/{kb_id}/flowcharts/{file_id}/reparse")
async def reparse_flowchart(
    kb_id: str,
    file_id: str,
    request: FlowchartRevisionRequest,
    current_user: User = Depends(get_required_user),
):
    await _require_kb_permission(current_user, kb_id, "can_manage")
    await _ensure_database_supports_documents(kb_id, "重新解析流程图")
    try:
        return await FlowchartIngestionService().reparse(
            kb_id=kb_id,
            file_id=file_id,
            expected_revision=request.expected_revision,
            operator_id=current_user.uid,
        )
    except Exception as error:  # noqa: BLE001
        _raise_flowchart_http_error(error)


@flowcharts.post("/databases/{kb_id}/flowcharts/{file_id}/confirm")
async def confirm_flowchart(
    kb_id: str,
    file_id: str,
    request: FlowchartRevisionRequest,
    current_user: User = Depends(get_required_user),
):
    await _require_kb_permission(current_user, kb_id, "can_manage")
    await _ensure_database_supports_documents(kb_id, "确认流程图")
    try:
        return await FlowchartIngestionService().confirm(
            kb_id=kb_id,
            file_id=file_id,
            expected_revision=request.expected_revision,
            operator_id=current_user.uid,
        )
    except Exception as error:  # noqa: BLE001
        _raise_flowchart_http_error(error)


__all__ = ["flowcharts"]
