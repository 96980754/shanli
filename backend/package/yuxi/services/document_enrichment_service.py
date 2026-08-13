"""Document summary, keyword, and tag generation with manual override protection."""
from __future__ import annotations
from copy import deepcopy
from typing import Any
from yuxi.config.app import config
from yuxi.knowledge.base import FileStatus
from yuxi.knowledge.enrichment import (
    ENRICHMENT_COMPONENTS,
    DocumentEnrichmentGenerator,
    EnrichmentProviderUnavailable,
    formal_content_hash,
    normalize_keywords,
    normalize_tags,
    validate_summary,
)
from yuxi.knowledge.utils import is_minio_url, parse_minio_url, sanitize_processing_error
from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository
from yuxi.services.task_service import TaskContext, tasker
from yuxi.storage.minio.client import get_minio_client
from yuxi.utils.datetime_utils import utc_isoformat, utc_now_naive
from yuxi.utils.logging_config import logger
GENERATABLE_FILE_STATUSES = {FileStatus.INDEXED, FileStatus.ERROR_REPLACEMENT_CLEANUP}
class DocumentEnrichmentError(ValueError):
    """Base document enrichment domain error."""
class EnrichmentNotFound(DocumentEnrichmentError):
    """The requested eligible document does not exist."""
class EnrichmentVersionConflict(DocumentEnrichmentError):
    """The document enrichment was modified by another operation."""
class DocumentEnrichmentService:
    def __init__(
        self,
        *,
        file_repository: KnowledgeFileRepository | None = None,
        generator: DocumentEnrichmentGenerator | None = None,
    ) -> None:
        self.file_repository = file_repository or KnowledgeFileRepository()
        self.generator = generator or DocumentEnrichmentGenerator()
    async def _get_record(self, kb_id: str, file_id: str):
        record = await self.file_repository.get_by_file_id(file_id)
        if record is None or record.kb_id != kb_id or record.is_folder:
            raise EnrichmentNotFound("文档不存在")
        return record
    @staticmethod
    def _assert_eligible(record) -> None:
        if not record.is_active:
            raise EnrichmentNotFound("文档不是当前生效版本")
        if record.status not in GENERATABLE_FILE_STATUSES or not record.markdown_file:
            raise EnrichmentNotFound("文档尚未完成入库")
    @staticmethod
    async def _read_markdown(path: str | None) -> str:
        if not path or not is_minio_url(path):
            raise DocumentEnrichmentError("文档没有可用的正式 Markdown")
        bucket_name, object_name = parse_minio_url(path)
        content = await get_minio_client().adownload_file(bucket_name, object_name)
        try:
            markdown = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise DocumentEnrichmentError("正式 Markdown 编码无效") from exc
        if not markdown.strip():
            raise DocumentEnrichmentError("正式 Markdown 为空")
        if len(markdown) > int(config.document_enrichment_max_chars):
            raise DocumentEnrichmentError("文档超过信息增强允许的最大字符数")
        return markdown
    @staticmethod
    def _public_payload(record) -> dict[str, Any]:
        data = deepcopy(record.enrichment_data or {})
        return {
            "kb_id": record.kb_id,
            "file_id": record.file_id,
            "filename": record.filename,
            "status": record.enrichment_status or "not_generated",
            "version": int(record.enrichment_version or 0),
            "content_version": int(record.cleaning_version or 0),
            "content_hash": record.enrichment_content_hash,
            "generated_at": (utc_isoformat(record.enrichment_generated_at) if record.enrichment_generated_at else None),
            "error": record.enrichment_error,
            "possibly_outdated": bool(record.enrichment_possibly_outdated),
            "summary": data.get("summary"),
            "keywords": data.get("keywords") or [],
            "tags": data.get("tags") or [],
            "keyword_source": (data.get("component_sources") or {}).get("keywords"),
            "tag_source": (data.get("component_sources") or {}).get("tags"),
        }
    async def get(self, *, kb_id: str, file_id: str) -> dict[str, Any]:
        record = await self._get_record(kb_id, file_id)
        return self._public_payload(record)
    async def get_generation_identity(self, *, kb_id: str, file_id: str) -> dict[str, Any]:
        record = await self._get_record(kb_id, file_id)
        self._assert_eligible(record)
        return {
            "file_id": record.file_id,
            "cleaning_version": int(record.cleaning_version or 0),
        }
    async def generate(
        self,
        *,
        kb_id: str,
        file_id: str,
        operator_id: str,
        components: set[str],
        model_spec: str | None = None,
        overwrite_manual: bool = False,
    ) -> dict[str, Any]:
        requested = set(components) & ENRICHMENT_COMPONENTS
        if not requested:
            raise DocumentEnrichmentError("请选择至少一个生成项")
        record = await self._get_record(kb_id, file_id)
        self._assert_eligible(record)
        markdown = await self._read_markdown(record.markdown_file)
        content_hash = formal_content_hash(markdown)
        content_version = int(record.cleaning_version or 0)
        current_version = int(record.enrichment_version or 0)
        existing_data = deepcopy(record.enrichment_data or {})
        effective_requested = (
            requested
            if overwrite_manual
            else {component for component in requested if not self._component_is_manual(existing_data, component)}
        )
        if not effective_requested:
            return self._public_payload(record) | {"idempotent": True}
        claimed = await self.file_repository.update_enrichment_fields_with_version(
            kb_id=kb_id,
            file_id=file_id,
            expected_version=current_version,
            expected_cleaning_version=content_version,
            increment_version=True,
            data={
                "enrichment_status": "generating",
                "enrichment_content_hash": content_hash,
                "enrichment_error": None,
                "updated_by": operator_id,
            },
        )
        if claimed is None:
            raise EnrichmentVersionConflict("文档信息已被其他操作更新，请刷新后重试")
        claimed_version = int(claimed.enrichment_version or current_version + 1)
        configured_model = (
            model_spec if model_spec is not None else (config.document_enrichment_model or config.default_model)
        )
        try:
            generated = await self.generator.generate(
                markdown,
                components=effective_requested,
                model_spec=configured_model,
                temperature=float(config.document_enrichment_temperature),
                timeout_seconds=float(config.document_enrichment_timeout_seconds),
                chunk_chars=int(config.document_enrichment_chunk_chars),
                attempts=int(config.document_enrichment_output_attempts),
                summary_max_chars=int(config.document_enrichment_summary_max_chars),
                keyword_limit=int(config.document_enrichment_keyword_limit),
                tag_limit=int(config.document_enrichment_tag_limit),
            )
        except EnrichmentProviderUnavailable as exc:
            updated = await self._finish_generation_failure(
                kb_id=kb_id,
                file_id=file_id,
                operator_id=operator_id,
                claimed_version=claimed_version,
                content_version=content_version,
                status="skipped",
                error=exc,
            )
            return self._public_payload(updated) | {"idempotent": False}
        except Exception as exc:
            await self._finish_generation_failure(
                kb_id=kb_id,
                file_id=file_id,
                operator_id=operator_id,
                claimed_version=claimed_version,
                content_version=content_version,
                status="failed",
                error=exc,
            )
            raise DocumentEnrichmentError(sanitize_processing_error(exc)) from exc
        now = utc_now_naive()
        next_data = deepcopy(existing_data)
        model_name = str(generated.get("model_name") or "")
        model_version = str(generated.get("model_version") or "")
        if "summary" in effective_requested:
            next_data["summary"] = {
                "text": validate_summary(
                    markdown,
                    str(generated.get("summary") or ""),
                    max_chars=int(config.document_enrichment_summary_max_chars),
                ),
                "source": "generated",
                "status": "ready",
                "generated_at": utc_isoformat(now),
                "edited_at": None,
                "model_name": model_name,
                "model_version": model_version,
                "content_version": content_version,
            }
        if "keywords" in effective_requested:
            next_data["keywords"] = self._build_generated_keywords(
                generated.get("keywords") or [],
                markdown=markdown,
                generated_at=now,
                model_name=model_name,
                model_version=model_version,
                content_version=content_version,
            )
            next_data.setdefault("component_sources", {})["keywords"] = "generated"
            next_data.setdefault("component_statuses", {})["keywords"] = "ready"
        if "tags" in effective_requested:
            next_data["tags"] = self._build_generated_tags(
                generated.get("tags") or [],
                generated_at=now,
                model_name=model_name,
                model_version=model_version,
                content_version=content_version,
            )
            next_data.setdefault("component_sources", {})["tags"] = "generated"
            next_data.setdefault("component_statuses", {})["tags"] = "ready"
        possibly_outdated = self._has_outdated_components(next_data)
        updated = await self.file_repository.update_enrichment_fields_with_version(
            kb_id=kb_id,
            file_id=file_id,
            expected_version=claimed_version,
            expected_cleaning_version=content_version,
            increment_version=True,
            data={
                "enrichment_data": next_data,
                "enrichment_status": "possibly_outdated" if possibly_outdated else "ready",
                "enrichment_content_hash": content_hash,
                "enrichment_generated_at": now,
                "enrichment_error": None,
                "enrichment_possibly_outdated": possibly_outdated,
                "updated_by": operator_id,
            },
        )
        if updated is None:
            raise EnrichmentVersionConflict("正文版本或文档信息已变化，本次生成结果未保存")
        return self._public_payload(updated) | {"idempotent": False}
    async def _finish_generation_failure(
        self,
        *,
        kb_id: str,
        file_id: str,
        operator_id: str,
        claimed_version: int,
        content_version: int,
        status: str,
        error: BaseException,
    ):
        updated = await self.file_repository.update_enrichment_fields_with_version(
            kb_id=kb_id,
            file_id=file_id,
            expected_version=claimed_version,
            expected_cleaning_version=content_version,
            increment_version=True,
            data={
                "enrichment_status": status,
                "enrichment_error": sanitize_processing_error(error),
                "updated_by": operator_id,
            },
        )
        if updated is None:
            raise EnrichmentVersionConflict("正文版本或文档信息已变化")
        return updated
    @staticmethod
    def _component_is_manual(data: dict[str, Any], component: str) -> bool:
        if component == "summary":
            return (data.get("summary") or {}).get("source") == "manual"
        source = (data.get("component_sources") or {}).get(component)
        if source:
            return source == "manual"
        value = data.get(component) or []
        return bool(value) and all(isinstance(item, dict) and item.get("source") == "manual" for item in value)
    @staticmethod
    def _has_outdated_components(data: dict[str, Any]) -> bool:
        summary = data.get("summary")
        if isinstance(summary, dict) and summary.get("status") == "possibly_outdated":
            return True
        if "possibly_outdated" in set((data.get("component_statuses") or {}).values()):
            return True
        return any(
            isinstance(item, dict) and item.get("status") == "possibly_outdated"
            for component in ("keywords", "tags")
            for item in (data.get(component) or [])
        )
    @staticmethod
    def _build_generated_keywords(
        values: list[Any],
        *,
        markdown: str,
        generated_at,
        model_name: str,
        model_version: str,
        content_version: int,
    ) -> list[dict[str, Any]]:
        normalized = normalize_keywords(values, markdown, limit=int(config.document_enrichment_keyword_limit))
        return [
            {
                **item,
                "source": "generated",
                "confidence": None,
                "order": order,
                "status": "ready",
                "generated_at": utc_isoformat(generated_at),
                "edited_at": None,
                "model_name": model_name,
                "model_version": model_version,
                "content_version": content_version,
            }
            for order, item in enumerate(normalized)
        ]
    @staticmethod
    def _build_generated_tags(
        values: list[Any],
        *,
        generated_at,
        model_name: str,
        model_version: str,
        content_version: int,
    ) -> list[dict[str, Any]]:
        normalized = normalize_tags(values, limit=int(config.document_enrichment_tag_limit))
        return [
            {
                **item,
                "source": "generated",
                "status": "ready",
                "generated_at": utc_isoformat(generated_at),
                "edited_at": None,
                "model_name": model_name,
                "model_version": model_version,
                "content_version": content_version,
            }
            for item in normalized
        ]
    async def update_summary(
        self,
        *,
        kb_id: str,
        file_id: str,
        operator_id: str,
        expected_version: int,
        text: str,
    ) -> dict[str, Any]:
        record, markdown = await self._manual_update_context(kb_id, file_id)
        data = deepcopy(record.enrichment_data or {})
        now = utc_now_naive()
        data["summary"] = {
            "text": validate_summary(
                markdown,
                text,
                max_chars=int(config.document_enrichment_summary_max_chars),
            ),
            "source": "manual",
            "status": "ready",
            "generated_at": (data.get("summary") or {}).get("generated_at"),
            "edited_at": utc_isoformat(now),
            "model_name": None,
            "model_version": None,
            "content_version": int(record.cleaning_version or 0),
            "updated_by": operator_id,
        }
        return await self._save_manual_data(record, data, operator_id, expected_version)
    async def update_keywords(
        self,
        *,
        kb_id: str,
        file_id: str,
        operator_id: str,
        expected_version: int,
        values: list[str],
    ) -> dict[str, Any]:
        record, markdown = await self._manual_update_context(kb_id, file_id)
        now = utc_now_naive()
        normalized = normalize_keywords(values, markdown, limit=int(config.document_enrichment_keyword_limit))
        data = deepcopy(record.enrichment_data or {})
        data["keywords"] = [
            {
                **item,
                "source": "manual",
                "confidence": None,
                "order": order,
                "status": "ready",
                "generated_at": None,
                "edited_at": utc_isoformat(now),
                "model_name": None,
                "model_version": None,
                "content_version": int(record.cleaning_version or 0),
                "updated_by": operator_id,
            }
            for order, item in enumerate(normalized)
        ]
        data.setdefault("component_sources", {})["keywords"] = "manual"
        data.setdefault("component_statuses", {})["keywords"] = "ready"
        return await self._save_manual_data(record, data, operator_id, expected_version)
    async def update_tags(
        self,
        *,
        kb_id: str,
        file_id: str,
        operator_id: str,
        expected_version: int,
        values: list[str],
    ) -> dict[str, Any]:
        record, _markdown = await self._manual_update_context(kb_id, file_id)
        now = utc_now_naive()
        normalized = normalize_tags(values, limit=int(config.document_enrichment_tag_limit))
        data = deepcopy(record.enrichment_data or {})
        data["tags"] = [
            {
                **item,
                "source": "manual",
                "status": "ready",
                "generated_at": None,
                "edited_at": utc_isoformat(now),
                "model_name": None,
                "model_version": None,
                "content_version": int(record.cleaning_version or 0),
                "updated_by": operator_id,
            }
            for item in normalized
        ]
        data.setdefault("component_sources", {})["tags"] = "manual"
        data.setdefault("component_statuses", {})["tags"] = "ready"
        return await self._save_manual_data(record, data, operator_id, expected_version)
    async def _manual_update_context(self, kb_id: str, file_id: str):
        record = await self._get_record(kb_id, file_id)
        self._assert_eligible(record)
        markdown = await self._read_markdown(record.markdown_file)
        return record, markdown
    async def _save_manual_data(
        self,
        record,
        data: dict[str, Any],
        operator_id: str,
        expected_version: int,
    ) -> dict[str, Any]:
        outdated = self._has_outdated_components(data)
        updated = await self.file_repository.update_enrichment_fields_with_version(
            kb_id=record.kb_id,
            file_id=record.file_id,
            expected_version=max(0, int(expected_version)),
            expected_cleaning_version=int(record.cleaning_version or 0),
            increment_version=True,
            data={
                "enrichment_data": data,
                "enrichment_status": "possibly_outdated" if outdated else "ready",
                "enrichment_content_hash": formal_content_hash(await self._read_markdown(record.markdown_file)),
                "enrichment_error": None,
                "enrichment_possibly_outdated": outdated,
                "updated_by": operator_id,
            },
        )
        if updated is None:
            raise EnrichmentVersionConflict("文档信息已被其他编辑更新，请刷新后重试")
        return self._public_payload(updated)
    async def mark_enqueue_failure(self, *, kb_id: str, file_id: str, operator_id: str, error: BaseException) -> None:
        record = await self._get_record(kb_id, file_id)
        await self.file_repository.update_enrichment_fields_with_version(
            kb_id=kb_id,
            file_id=file_id,
            expected_version=int(record.enrichment_version or 0),
            expected_cleaning_version=int(record.cleaning_version or 0),
            increment_version=True,
            data={
                "enrichment_status": "failed",
                "enrichment_error": sanitize_processing_error(error),
                "updated_by": operator_id,
            },
        )
async def enqueue_document_enrichment(
    *,
    kb_id: str,
    file_id: str,
    operator_id: str,
    components: set[str] | None = None,
    overwrite_manual: bool = False,
) -> tuple[str, bool]:
    requested = set(components or ENRICHMENT_COMPONENTS) & ENRICHMENT_COMPONENTS
    service = DocumentEnrichmentService()
    identity = await service.get_generation_identity(kb_id=kb_id, file_id=file_id)
    normalized_components = sorted(requested)
    async def run_enrichment(context: TaskContext):
        await context.set_progress(10, "正在生成文档摘要、关键词和标签")
        result = await DocumentEnrichmentService().generate(
            kb_id=kb_id,
            file_id=file_id,
            operator_id=operator_id,
            components=requested,
            overwrite_manual=overwrite_manual,
        )
        await context.set_progress(100, "文档信息增强已完成")
        return {
            "kb_id": kb_id,
            "file_id": file_id,
            "status": result["status"],
            "version": result["version"],
        }
    payload = {
        "kb_id": kb_id,
        "file_id": file_id,
        "cleaning_version": identity["cleaning_version"],
        "components": normalized_components,
        "overwrite_manual": bool(overwrite_manual),
    }
    task, created = await tasker.enqueue_unique_by_payload(
        name="生成文档摘要、关键词和标签",
        task_type="document_enrichment",
        payload=payload,
        payload_match=payload,
        statuses={"pending", "running"},
        coroutine=run_enrichment,
    )
    return task.id, created
async def enqueue_auto_document_enrichment(*, kb_id: str, file_id: str, operator_id: str) -> None:
    if not config.document_enrichment_auto_generate:
        return
    try:
        await enqueue_document_enrichment(kb_id=kb_id, file_id=file_id, operator_id=operator_id)
    except Exception as exc:  # noqa: BLE001 - enrichment must never make indexing fail
        logger.warning("Failed to enqueue document enrichment for {}: {}", file_id, sanitize_processing_error(exc))
        try:
            await DocumentEnrichmentService().mark_enqueue_failure(
                kb_id=kb_id,
                file_id=file_id,
                operator_id=operator_id,
                error=exc,
            )
        except Exception as status_error:  # noqa: BLE001
            logger.warning(
                "Failed to record document enrichment enqueue failure for {}: {}",
                file_id,
                sanitize_processing_error(status_error),
            )
__all__ = [
    "DocumentEnrichmentError",
    "DocumentEnrichmentService",
    "EnrichmentNotFound",
    "EnrichmentVersionConflict",
    "enqueue_auto_document_enrichment",
    "enqueue_document_enrichment",
]
