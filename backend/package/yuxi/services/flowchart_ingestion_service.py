"""Flowchart upload, editable draft and confirmation lifecycle."""

from __future__ import annotations

import asyncio
import os
import secrets
from collections.abc import Awaitable, Callable
from copy import deepcopy
from typing import Any

from yuxi import config
from yuxi.knowledge.base import FileStatus
from yuxi.knowledge.flowchart import FLOWCHART_INGESTION_TYPE, FLOWCHART_METADATA_KEY, flowchart_revision, is_flowchart
from yuxi.knowledge.flowchart_analysis import (
    PAGE_PROMPT,
    FlowchartAnalysisError,
    FlowchartVisionAnalyzer,
    validate_semantic_markdown,
)
from yuxi.knowledge.flowchart_render import FlowchartRenderError, render_flowchart_pdf
from yuxi.knowledge.utils import is_minio_url, parse_minio_url, sanitize_processing_error
from yuxi.repositories.knowledge_file_repository import (
    FlowchartRevisionConflict,
    FlowchartStateConflict,
    KnowledgeFileRepository,
)
from yuxi.services.document_ingestion_service import DocumentCreationResult, DocumentIngestionService
from yuxi.storage.minio import get_minio_client
from yuxi.utils import logger
from yuxi.utils.datetime_utils import utc_isoformat, utc_now_naive

FLOWCHART_INDEX_NOT_IMPLEMENTED = "P0-B 尚未接入流程图索引调度"


class FlowchartIngestionError(ValueError):
    """User-visible flowchart lifecycle error."""

    def __init__(self, message: str, *, code: str = "FLOWCHART_INVALID_OUTPUT"):
        self.code = code
        super().__init__(message)


class FlowchartNotFound(FlowchartIngestionError):
    pass


class FlowchartImmutable(FlowchartIngestionError):
    pass


FlowchartIndexStarter = Callable[[str, str, str], Awaitable[None]]


class FlowchartIngestionService:
    def __init__(
        self,
        *,
        file_repository: KnowledgeFileRepository | None = None,
        document_ingestion: DocumentIngestionService | None = None,
        index_starter: FlowchartIndexStarter | None = None,
        vision_analyzer: FlowchartVisionAnalyzer | None = None,
    ) -> None:
        self.file_repository = file_repository or KnowledgeFileRepository()
        self.document_ingestion = document_ingestion or DocumentIngestionService(file_repository=self.file_repository)
        self.index_starter = index_starter
        self.vision_analyzer = vision_analyzer or FlowchartVisionAnalyzer()

    async def create(
        self,
        *,
        kb_id: str,
        file_path: str,
        params: dict[str, Any],
        operator_id: str,
    ) -> dict[str, Any]:
        self._validate_pdf_staging_path(kb_id, file_path)
        try:
            creation = await self.document_ingestion.create_uploaded_document(
                kb_id=kb_id,
                item=file_path,
                params=params,
                operator_id=operator_id,
                ingestion_type=FLOWCHART_INGESTION_TYPE,
            )
        except ValueError as exc:
            if str(exc) == "PDF 文件签名不匹配":
                raise FlowchartIngestionError("文件不是有效的 PDF", code="INVALID_FLOWCHART_PDF") from exc
            raise
        if creation.action != "created" or not creation.file_meta:
            if creation.file_meta and (creation.file_meta.get("processing_params") or {}).get(
                "ingestion_type"
            ) != FLOWCHART_INGESTION_TYPE:
                raise FlowchartIngestionError("该上传文件已被普通文档流程占用")
            return self._serialize_creation_result(creation)

        file_id = str(creation.file_meta["file_id"])
        await self.file_repository.update_flowchart_with_revision(
            kb_id=kb_id,
            file_id=file_id,
            expected_revision=0,
            increment_revision=False,
            allowed_statuses={FileStatus.UPLOADED},
            data={
                "status": FileStatus.FLOWCHART_PARSING,
                "processing_stage": "flowchart_parsing",
                "processing_progress": 10,
                "error_message": None,
                "updated_by": operator_id,
            },
            metadata_updates={"parse_status": "parsing", "parse_requested_at": utc_isoformat()},
        )
        return await self._parse_record(kb_id=kb_id, file_id=file_id, expected_revision=0, operator_id=operator_id)

    async def get_preview(self, *, kb_id: str, file_id: str) -> dict[str, Any]:
        record = await self._get_record(kb_id, file_id)
        content = await self._read_markdown(record.markdown_file) if record.markdown_file else ""
        metadata = deepcopy((record.parse_metadata or {}).get(FLOWCHART_METADATA_KEY) or {})
        return {
            **self._serialize_record(record),
            "semantic_markdown": content,
            "flowchart_metadata": metadata,
        }

    async def update_draft(
        self,
        *,
        kb_id: str,
        file_id: str,
        expected_revision: int,
        semantic_markdown: str,
        operator_id: str,
    ) -> dict[str, Any]:
        content = self._validate_markdown(semantic_markdown)
        record = await self._get_record(kb_id, file_id)
        self._ensure_mutable(record)
        next_revision = max(0, int(expected_revision)) + 1
        draft_path = await self._save_draft(kb_id, file_id, next_revision, content)
        previous_path = record.markdown_file
        try:
            await self.file_repository.update_flowchart_with_revision(
                kb_id=kb_id,
                file_id=file_id,
                expected_revision=expected_revision,
                allowed_statuses={
                    FileStatus.ERROR_FLOWCHART_PARSING,
                    FileStatus.FLOWCHART_WAITING_CONFIRMATION,
                },
                data={
                    "status": FileStatus.FLOWCHART_WAITING_CONFIRMATION,
                    "markdown_file": draft_path,
                    "processing_stage": "flowchart_review",
                    "processing_progress": 60,
                    "error_message": None,
                    "updated_by": operator_id,
                },
                metadata_updates={
                    "parse_status": "waiting_confirmation",
                    "manually_edited": True,
                    "draft_updated_at": utc_isoformat(),
                },
            )
        except Exception:
            await self._delete_draft(draft_path)
            raise
        if previous_path and previous_path not in {draft_path, record.original_markdown_file}:
            await self._delete_draft(previous_path)
        return await self.get_preview(kb_id=kb_id, file_id=file_id)

    async def reparse(
        self,
        *,
        kb_id: str,
        file_id: str,
        expected_revision: int,
        operator_id: str,
    ) -> dict[str, Any]:
        record = await self._get_record(kb_id, file_id)
        self._ensure_mutable(record)
        await self.file_repository.update_flowchart_with_revision(
            kb_id=kb_id,
            file_id=file_id,
            expected_revision=expected_revision,
            increment_revision=False,
            allowed_statuses={
                FileStatus.ERROR_FLOWCHART_PARSING,
                FileStatus.FLOWCHART_WAITING_CONFIRMATION,
            },
            data={
                "status": FileStatus.FLOWCHART_PARSING,
                "processing_stage": "flowchart_parsing",
                "processing_progress": 10,
                "error_message": None,
                "updated_by": operator_id,
            },
            metadata_updates={
                "parse_status": "parsing",
                "reparse_requested_at": utc_isoformat(),
            },
        )
        return await self._parse_record(
            kb_id=kb_id, file_id=file_id, expected_revision=expected_revision, operator_id=operator_id
        )

    async def _parse_record(
        self, *, kb_id: str, file_id: str, expected_revision: int, operator_id: str
    ) -> dict[str, Any]:
        record = await self._get_record(kb_id, file_id)
        old_draft_path = record.markdown_file
        new_draft_path = None
        try:
            model_spec = (config.flowchart_vision_model_spec or "").strip()
            if not model_spec:
                raise FlowchartAnalysisError("FLOWCHART_VISION_MODEL_NOT_CONFIGURED", "请先配置流程图视觉模型")
            bucket_name, object_name = parse_minio_url(record.path)
            try:
                pdf_bytes = await get_minio_client().adownload_file(bucket_name, object_name)
            except Exception as exc:
                raise FlowchartRenderError("INVALID_FLOWCHART_PDF", "无法读取流程图 PDF 原文件") from exc
            images = await asyncio.to_thread(render_flowchart_pdf, pdf_bytes)
            analysis = await self.vision_analyzer.analyze_flowchart(images, PAGE_PROMPT, model_spec)
            if [page.page_number for page in analysis.pages] != [image.page_number for image in images]:
                raise FlowchartAnalysisError("FLOWCHART_INVALID_OUTPUT", "视觉解析结果与 PDF 页码不一致")
            content = self._validate_markdown(analysis.semantic_markdown)
            validate_semantic_markdown(content)
            new_draft_path = await self._save_draft(kb_id, file_id, expected_revision + 1, content)
            parsed_at = utc_isoformat()
            result = await self.file_repository.update_flowchart_with_revision(
                kb_id=kb_id,
                file_id=file_id,
                expected_revision=expected_revision,
                allowed_statuses={FileStatus.FLOWCHART_PARSING},
                data={
                    "status": FileStatus.FLOWCHART_WAITING_CONFIRMATION,
                    "markdown_file": new_draft_path,
                    "original_markdown_file": record.original_markdown_file or new_draft_path,
                    "processing_stage": "flowchart_review",
                    "processing_progress": 60,
                    "error_message": None,
                    "updated_by": operator_id,
                },
                metadata_updates={
                    "schema_version": 1,
                    "parse_status": "waiting_confirmation",
                    "model_spec": analysis.model_spec,
                    "prompt_version": analysis.prompt_version,
                    "render_dpi": config.flowchart_render_dpi,
                    "page_count": len(analysis.pages),
                    "pages": [
                        {
                            "page_number": page.page_number,
                            "role": images[index].role,
                            "crop_box": images[index].crop_box,
                            "width": images[index].width,
                            "height": images[index].height,
                            "requested_dpi": images[index].requested_dpi,
                            "effective_dpi": images[index].effective_dpi,
                            "warnings": list(page.warnings),
                        }
                        for index, page in enumerate(analysis.pages)
                    ],
                    "parsed_at": parsed_at,
                    "manually_edited": False,
                    "warnings": list(analysis.warnings),
                    "error_code": None,
                },
            )
            if old_draft_path and old_draft_path != record.original_markdown_file:
                await self._delete_draft(old_draft_path)
            return self._serialize_record(result.record)
        except (FlowchartRenderError, FlowchartAnalysisError, FlowchartIngestionError) as exc:
            error_code = getattr(exc, "code", "FLOWCHART_INVALID_OUTPUT")
            message = str(exc)
        except (FlowchartRevisionConflict, FlowchartStateConflict):
            if new_draft_path:
                await self._delete_draft(new_draft_path)
            raise
        except Exception as exc:  # noqa: BLE001 - storage failure still leaves old draft intact
            logger.error("Flowchart parse failed for {}: {}", file_id, type(exc).__name__)
            error_code = "FLOWCHART_DRAFT_STORAGE_ERROR"
            message = "流程图解析失败，请稍后重试"

        if new_draft_path:
            await self._delete_draft(new_draft_path)
        failed = await self.file_repository.update_flowchart_with_revision(
            kb_id=kb_id,
            file_id=file_id,
            expected_revision=expected_revision,
            increment_revision=False,
            allowed_statuses={FileStatus.FLOWCHART_PARSING},
            data={
                "status": FileStatus.ERROR_FLOWCHART_PARSING,
                "processing_stage": "flowchart_parsing",
                "processing_progress": 10,
                "error_message": message,
                "updated_by": operator_id,
            },
            metadata_updates={"parse_status": "failed", "error_code": error_code},
        )
        payload = self._serialize_record(failed.record)
        payload["error_code"] = error_code
        return payload

    async def confirm(
        self,
        *,
        kb_id: str,
        file_id: str,
        expected_revision: int,
        operator_id: str,
    ) -> dict[str, Any]:
        record = await self._get_record(kb_id, file_id)
        if record.confirmed_at is None:
            if not record.markdown_file:
                raise FlowchartIngestionError("流程图没有可确认的语义 Markdown 草稿")
            self._validate_markdown(await self._read_markdown(record.markdown_file))

        now = utc_now_naive()
        result = await self.file_repository.update_flowchart_with_revision(
            kb_id=kb_id,
            file_id=file_id,
            expected_revision=expected_revision,
            allowed_statuses={FileStatus.FLOWCHART_WAITING_CONFIRMATION},
            idempotent_if_confirmed=True,
            data={
                "status": FileStatus.FLOWCHART_CONFIRMING,
                "processing_stage": "flowchart_confirming",
                "processing_progress": 68,
                "confirmed_at": now,
                "confirmed_by": operator_id,
                "error_message": None,
                "updated_by": operator_id,
            },
            metadata_updates={
                "parse_status": "confirmed",
                "confirmed_at": utc_isoformat(now),
            },
        )
        if result.idempotent:
            payload = self._serialize_record(result.record)
            payload["idempotent"] = True
            return payload

        indexed_record = await self._begin_indexing(kb_id=kb_id, file_id=file_id, operator_id=operator_id)
        payload = self._serialize_record(indexed_record)
        payload["idempotent"] = False
        return payload

    async def _begin_indexing(self, *, kb_id: str, file_id: str, operator_id: str):
        await self.file_repository.update_flowchart_status(
            kb_id=kb_id,
            file_id=file_id,
            allowed_statuses={FileStatus.FLOWCHART_CONFIRMING},
            require_confirmed=True,
            data={
                "status": FileStatus.INDEXING,
                "processing_stage": "flowchart_indexing",
                "processing_progress": 70,
                "updated_by": operator_id,
            },
        )
        try:
            if self.index_starter is None:
                raise NotImplementedError(FLOWCHART_INDEX_NOT_IMPLEMENTED)
            await self.index_starter(kb_id, file_id, operator_id)
        except Exception as exc:  # noqa: BLE001 - failure is persisted as the retryable boundary
            message = sanitize_processing_error(exc)
            logger.info("Flowchart indexing was not started for {}: {}", file_id, message)
            return await self.file_repository.update_flowchart_status(
                kb_id=kb_id,
                file_id=file_id,
                allowed_statuses={FileStatus.INDEXING},
                require_confirmed=True,
                data={
                    "status": FileStatus.ERROR_INDEXING,
                    "processing_stage": "flowchart_indexing",
                    "processing_progress": 70,
                    "error_message": message,
                    "updated_by": operator_id,
                },
            )
        return await self._get_record(kb_id, file_id)

    async def _get_record(self, kb_id: str, file_id: str):
        record = await self.file_repository.get_by_file_id(file_id)
        if record is None or record.kb_id != kb_id or record.is_folder or not is_flowchart(record):
            raise FlowchartNotFound("流程图不存在")
        return record

    @staticmethod
    def _ensure_mutable(record) -> None:
        if record.confirmed_at is not None:
            raise FlowchartImmutable("流程图当前版本已确认，不能继续修改")

    @staticmethod
    def _validate_pdf_staging_path(kb_id: str, file_path: str) -> None:
        if not is_minio_url(file_path):
            raise FlowchartIngestionError("File source must be a MinIO URL", code="INVALID_FLOWCHART_PDF")
        bucket_name, object_name = parse_minio_url(file_path)
        filename = DocumentIngestionService._filename_from_staged_object(kb_id, bucket_name, object_name)
        if os.path.splitext(filename)[1].lower() != ".pdf":
            raise FlowchartIngestionError("流程图第一版只支持 PDF", code="INVALID_FLOWCHART_PDF")

    @staticmethod
    def _validate_markdown(content: str) -> str:
        if not isinstance(content, str) or not content.strip():
            raise FlowchartIngestionError("流程图语义 Markdown 不能为空")
        if len(content) > int(config.document_cleaning_max_chars):
            raise FlowchartIngestionError("流程图语义 Markdown 超过允许的最大字符数")
        return content.strip()

    @staticmethod
    async def _read_markdown(path: str) -> str:
        if not is_minio_url(path):
            raise FlowchartIngestionError("流程图没有可用的语义 Markdown")
        bucket_name, object_name = parse_minio_url(path)
        content = await get_minio_client().adownload_file(bucket_name, object_name)
        try:
            return content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise FlowchartIngestionError("流程图语义 Markdown 编码无效") from exc

    @staticmethod
    async def _save_draft(kb_id: str, file_id: str, revision: int, content: str) -> str:
        client = get_minio_client()
        bucket_name = client.KB_BUCKETS["parsed"]
        await asyncio.to_thread(client.ensure_bucket_exists, bucket_name)
        object_name = f"{kb_id}/flowcharts/{file_id}/draft-r{revision}-{secrets.token_hex(4)}.md"
        result = await client.aupload_file(
            bucket_name=bucket_name,
            object_name=object_name,
            data=content.encode("utf-8"),
            content_type="text/markdown; charset=utf-8",
        )
        return result.url

    @staticmethod
    async def _delete_draft(path: str) -> None:
        if not path or not is_minio_url(path):
            return
        try:
            bucket_name, object_name = parse_minio_url(path)
            await get_minio_client().adelete_file(bucket_name, object_name)
        except Exception as exc:  # noqa: BLE001 - orphan cleanup must not hide the domain result
            logger.warning("Failed to clean unused flowchart draft: {}", sanitize_processing_error(exc))

    @staticmethod
    def _serialize_creation_result(result: DocumentCreationResult) -> dict[str, Any]:
        return {
            "action": result.action,
            "file_id": result.file_meta.get("file_id") if result.file_meta else result.existing_file_id,
            "status": result.file_meta.get("status") if result.file_meta else None,
            "cleanup_pending": result.cleanup_pending,
        }

    @staticmethod
    def _serialize_record(record) -> dict[str, Any]:
        return {
            "file_id": record.file_id,
            "kb_id": record.kb_id,
            "filename": record.filename,
            "file_type": record.file_type,
            "content_type": record.content_type,
            "ingestion_type": (record.processing_params or {}).get("ingestion_type"),
            "status": record.status,
            "revision": flowchart_revision(record),
            "confirmed_at": utc_isoformat(record.confirmed_at) if record.confirmed_at else None,
            "confirmed_by": record.confirmed_by,
            "is_current": bool(record.is_current),
            "is_active": bool(record.is_active),
            "error_message": record.error_message,
        }


__all__ = [
    "FlowchartImmutable",
    "FlowchartIngestionError",
    "FlowchartIngestionService",
    "FlowchartNotFound",
    "FlowchartRevisionConflict",
    "FlowchartStateConflict",
]
