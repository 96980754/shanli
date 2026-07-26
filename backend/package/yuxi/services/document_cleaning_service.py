"""Document cleaning drafts, confirmation, and safe indexing orchestration."""

from __future__ import annotations

import asyncio
import secrets
from copy import deepcopy
from typing import Any

from yuxi import config
from yuxi.knowledge.base import FileStatus
from yuxi.knowledge.cleaning import OptionalAIDocumentCleaner, sanitize_markdown_html
from yuxi.knowledge.runtime import knowledge_base
from yuxi.knowledge.utils import is_minio_url, parse_minio_url, sanitize_processing_error
from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository
from yuxi.storage.minio import get_minio_client
from yuxi.utils import logger
from yuxi.utils.datetime_utils import utc_isoformat, utc_now_naive


class DocumentCleaningError(ValueError):
    """User-visible cleaning domain error."""


class CleaningVersionConflict(DocumentCleaningError):
    """Raised when a stale editor tries to overwrite a newer draft."""


class DocumentCleaningNotFound(DocumentCleaningError):
    """Raised without revealing whether a document exists in another knowledge base."""


class DocumentCleaningService:
    def __init__(
        self,
        *,
        file_repository: KnowledgeFileRepository | None = None,
        cleaner: OptionalAIDocumentCleaner | None = None,
    ):
        self.file_repository = file_repository or KnowledgeFileRepository()
        self.cleaner = cleaner or OptionalAIDocumentCleaner()

    @staticmethod
    def resolve_auto_confirm(params: dict[str, Any] | None) -> bool:
        params = params or {}
        if isinstance(params.get("auto_confirm"), bool):
            return bool(params["auto_confirm"])
        if params.get("auto_index") is False:
            return False
        return bool(config.document_cleaning_auto_confirm)

    @staticmethod
    def _has_online_version(record) -> bool:
        return bool(record.is_active and int(record.chunk_count or 0) > 0)

    @classmethod
    def _preserve_previous_confirmation(cls, record, metadata: dict[str, Any]) -> None:
        if not cls._has_online_version(record) or "_previous_confirmed" in metadata:
            return
        metadata["_previous_confirmed"] = {
            "cleaning_metadata": deepcopy(record.cleaning_metadata or {}),
            "confirmed_at": utc_isoformat(record.confirmed_at) if record.confirmed_at else None,
            "confirmed_by": record.confirmed_by,
        }

    @staticmethod
    def _public_cleaning_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
        public_metadata = deepcopy(metadata or {})
        public_metadata.pop("_previous_confirmed", None)
        return public_metadata

    async def _get_record(self, kb_id: str, file_id: str):
        record = await self.file_repository.get_by_file_id(file_id)
        if record is None or record.kb_id != kb_id or record.is_folder:
            raise DocumentCleaningNotFound("文档不存在")
        return record

    @staticmethod
    async def _read_markdown(path: str | None) -> str:
        if not path or not is_minio_url(path):
            raise DocumentCleaningError("文档没有可用的 Markdown 内容")
        bucket_name, object_name = parse_minio_url(path)
        content = await get_minio_client().adownload_file(bucket_name, object_name)
        try:
            return content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise DocumentCleaningError("Markdown 内容编码无效") from exc

    @staticmethod
    async def _save_draft(kb_id: str, file_id: str, version: int, content: str) -> str:
        minio_client = get_minio_client()
        bucket_name = minio_client.KB_BUCKETS["parsed"]
        await asyncio.to_thread(minio_client.ensure_bucket_exists, bucket_name)
        nonce = secrets.token_hex(4)
        object_name = f"{kb_id}/cleaning/{file_id}/draft-v{version}-{nonce}.md"
        result = await minio_client.aupload_file(
            bucket_name=bucket_name,
            object_name=object_name,
            data=content.encode("utf-8"),
            content_type="text/markdown; charset=utf-8",
        )
        return result.url

    @staticmethod
    async def _delete_draft(path: str | None) -> None:
        if not path or not is_minio_url(path):
            return
        try:
            bucket_name, object_name = parse_minio_url(path)
            await get_minio_client().adelete_file(bucket_name, object_name)
        except Exception as exc:  # noqa: BLE001 - orphan cleanup must not hide the domain result
            logger.warning("Failed to clean unused document draft: {}", sanitize_processing_error(exc))

    @staticmethod
    def _validate_content(content: str) -> str:
        if not isinstance(content, str) or not content.strip():
            raise DocumentCleaningError("清洗草稿不能为空")
        if len(content) > int(config.document_cleaning_max_chars):
            raise DocumentCleaningError("清洗草稿超过允许的最大字符数")
        sanitized = sanitize_markdown_html(content)
        if not sanitized.strip():
            raise DocumentCleaningError("清洗草稿不包含可保存内容")
        return sanitized

    async def get_preview(self, *, kb_id: str, file_id: str) -> dict[str, Any]:
        record = await self._get_record(kb_id, file_id)
        original_path = record.original_markdown_file or record.markdown_file
        if not original_path:
            raise DocumentCleaningError("文档尚未完成解析")
        original = await self._read_markdown(original_path)
        draft_path = record.cleaning_draft_file
        cleaned = await self._read_markdown(draft_path) if draft_path else original
        if len(original) > int(config.document_cleaning_max_chars) or len(cleaned) > int(
            config.document_cleaning_max_chars
        ):
            raise DocumentCleaningError("文档内容超过网页编辑上限")
        return {
            "kb_id": kb_id,
            "file_id": file_id,
            "filename": record.filename,
            "status": record.status,
            "processing_stage": record.processing_stage,
            "original_markdown": original,
            "cleaned_markdown": cleaned,
            "cleaning_version": int(record.cleaning_version or 0),
            "cleaning_metadata": self._public_cleaning_metadata(record.cleaning_metadata),
            "confirmed_at": utc_isoformat(record.confirmed_at) if record.confirmed_at else None,
            "confirmed_by": record.confirmed_by,
            "has_online_chunks": int(record.chunk_count or 0) > 0,
            "error_message": record.error_message,
        }

    async def generate_draft(
        self,
        *,
        kb_id: str,
        file_id: str,
        operator_id: str,
        auto_confirm: bool,
        use_ai: bool | None = None,
    ) -> dict[str, Any]:
        record = await self._get_record(kb_id, file_id)
        if record.status in {FileStatus.PARSING, FileStatus.INDEXING}:
            raise DocumentCleaningError("文档当前正在处理")
        original_path = record.original_markdown_file or record.markdown_file
        original = await self._read_markdown(original_path)
        self._validate_content(original)

        await self.file_repository.update_fields(
            file_id=file_id,
            kb_id=kb_id,
            data={
                "status": FileStatus.CLEANING,
                "processing_stage": "cleaning",
                "processing_progress": 58,
                "error_message": None,
                "updated_by": operator_id,
            },
        )
        expected_version = int(record.cleaning_version or 0)
        next_version = expected_version + 1
        draft_path: str | None = None
        try:
            ai_enabled = bool(config.document_ai_cleaning_enabled) if use_ai is None else bool(use_ai)
            result = await self.cleaner.clean(
                original,
                parse_metadata=record.parse_metadata,
                enabled=ai_enabled,
                model_spec=config.document_ai_cleaning_model,
                temperature=float(config.document_ai_cleaning_temperature),
                timeout_seconds=float(config.document_ai_cleaning_timeout_seconds),
                chunk_chars=int(config.document_ai_cleaning_chunk_chars),
            )
            cleaned = self._validate_content(result.cleaned_markdown)
            draft_path = await self._save_draft(kb_id, file_id, next_version, cleaned)
            metadata = result.to_metadata(status="confirmed" if auto_confirm else "waiting_confirmation")
            metadata["generated_at"] = utc_isoformat()
            self._preserve_previous_confirmation(record, metadata)
            has_online_version = self._has_online_version(record)
            updated = await self.file_repository.update_cleaning_fields_with_version(
                kb_id=kb_id,
                file_id=file_id,
                expected_version=expected_version,
                increment_version=True,
                data={
                    "status": FileStatus.WAITING_CONFIRMATION,
                    "processing_stage": None,
                    "processing_progress": 65,
                    "original_markdown_file": original_path,
                    "cleaning_draft_file": draft_path,
                    "cleaning_metadata": metadata,
                    "confirmed_at": record.confirmed_at if has_online_version else None,
                    "confirmed_by": record.confirmed_by if has_online_version else None,
                    "error_message": None,
                    "updated_by": operator_id,
                },
            )
            if updated is None:
                await self._delete_draft(draft_path)
                raise CleaningVersionConflict("清洗草稿已被其他编辑更新，请刷新后重试")
            if auto_confirm:
                return await self.confirm(
                    kb_id=kb_id,
                    file_id=file_id,
                    operator_id=operator_id,
                    expected_version=int(updated.cleaning_version or next_version),
                )
            return await self.get_preview(kb_id=kb_id, file_id=file_id)
        except CleaningVersionConflict:
            raise
        except Exception as exc:
            if draft_path:
                await self._delete_draft(draft_path)
            await self.file_repository.update_fields(
                file_id=file_id,
                kb_id=kb_id,
                data={
                    "status": FileStatus.ERROR_CLEANING,
                    "processing_stage": "cleaning",
                    "error_message": sanitize_processing_error(exc),
                    "updated_by": operator_id,
                },
            )
            raise

    async def save_draft(
        self,
        *,
        kb_id: str,
        file_id: str,
        operator_id: str,
        expected_version: int,
        content: str,
    ) -> dict[str, Any]:
        record = await self._get_record(kb_id, file_id)
        cleaned = self._validate_content(content)
        next_version = max(0, int(expected_version)) + 1
        previous_draft_path = record.cleaning_draft_file
        draft_path = await self._save_draft(kb_id, file_id, next_version, cleaned)
        metadata = deepcopy(record.cleaning_metadata or {})
        metadata.update(
            {
                "status": "waiting_confirmation",
                "manually_edited": True,
                "last_edited_at": utc_isoformat(),
            }
        )
        self._preserve_previous_confirmation(record, metadata)
        has_online_version = self._has_online_version(record)
        changes = list(metadata.get("changes") or [])
        changes.append(
            {
                "change_type": "manual_edit",
                "original_text": "",
                "cleaned_text": "",
                "reason": "用户编辑清洗草稿",
                "position": None,
            }
        )
        metadata["changes"] = changes[-200:]
        updated = await self.file_repository.update_cleaning_fields_with_version(
            kb_id=kb_id,
            file_id=file_id,
            expected_version=expected_version,
            increment_version=True,
            data={
                "status": FileStatus.WAITING_CONFIRMATION,
                "processing_stage": None,
                "processing_progress": 65,
                "cleaning_draft_file": draft_path,
                "cleaning_metadata": metadata,
                "confirmed_at": record.confirmed_at if has_online_version else None,
                "confirmed_by": record.confirmed_by if has_online_version else None,
                "error_message": None,
                "updated_by": operator_id,
            },
        )
        if updated is None:
            await self._delete_draft(draft_path)
            raise CleaningVersionConflict("清洗草稿已被其他编辑更新，请刷新后重试")
        if previous_draft_path and previous_draft_path != record.markdown_file:
            await self._delete_draft(previous_draft_path)
        return await self.get_preview(kb_id=kb_id, file_id=file_id)

    async def cancel_draft(
        self,
        *,
        kb_id: str,
        file_id: str,
        operator_id: str,
        expected_version: int,
    ) -> dict[str, Any]:
        record = await self._get_record(kb_id, file_id)
        restored_status = FileStatus.INDEXED if int(record.chunk_count or 0) > 0 else FileStatus.PARSED
        metadata = deepcopy(record.cleaning_metadata or {})
        previous_confirmation = metadata.pop("_previous_confirmed", None)
        if restored_status == FileStatus.INDEXED and isinstance(previous_confirmation, dict):
            metadata = deepcopy(previous_confirmation.get("cleaning_metadata") or {})
        else:
            metadata.update({"status": "cancelled", "cancelled_at": utc_isoformat()})
        updated = await self.file_repository.update_cleaning_fields_with_version(
            kb_id=kb_id,
            file_id=file_id,
            expected_version=expected_version,
            increment_version=True,
            data={
                "status": restored_status,
                "processing_stage": None,
                "processing_progress": 100 if restored_status == FileStatus.INDEXED else 55,
                "cleaning_draft_file": (record.markdown_file if restored_status == FileStatus.INDEXED else None),
                "cleaning_metadata": metadata,
                "error_message": None,
                "updated_by": operator_id,
            },
        )
        if updated is None:
            raise CleaningVersionConflict("清洗草稿已被其他编辑更新，请刷新后重试")
        if record.cleaning_draft_file and record.cleaning_draft_file != record.markdown_file:
            await self._delete_draft(record.cleaning_draft_file)
        return await self.get_preview(kb_id=kb_id, file_id=file_id)

    async def confirm(
        self,
        *,
        kb_id: str,
        file_id: str,
        operator_id: str,
        expected_version: int,
    ) -> dict[str, Any]:
        record = await self._get_record(kb_id, file_id)
        if int(record.cleaning_version or 0) != max(0, int(expected_version)):
            raise CleaningVersionConflict("清洗草稿版本已变化，请刷新后重试")

        if record.status in {FileStatus.INDEXED, FileStatus.ERROR_REPLACEMENT_CLEANUP} and record.confirmed_at:
            return {"file_id": record.file_id, "status": record.status, "idempotent": True}
        if not record.cleaning_draft_file:
            raise DocumentCleaningError("文档没有可确认的清洗草稿")

        now = utc_now_naive()
        metadata = deepcopy(record.cleaning_metadata or {})
        previous_confirmation = metadata.pop("_previous_confirmed", None)
        metadata.update({"status": "confirmed", "confirmed_at": utc_isoformat(now)})

        target_record = record
        created_candidate = False
        if record.is_active and int(record.chunk_count or 0) > 0:
            candidate_id = f"file_{secrets.token_hex(6)}"
            working_draft_path = record.cleaning_draft_file
            candidate_content = await self._read_markdown(working_draft_path)
            candidate_draft_path = await self._save_draft(
                kb_id,
                candidate_id,
                int(record.cleaning_version or 0),
                candidate_content,
            )
            candidate_data = {
                "parent_id": record.parent_id,
                "filename": record.filename,
                "original_filename": record.original_filename,
                "file_type": record.file_type,
                "path": record.path,
                "minio_url": record.minio_url,
                "markdown_file": candidate_draft_path,
                "original_markdown_file": record.original_markdown_file or record.markdown_file,
                "cleaning_draft_file": candidate_draft_path,
                "cleaning_metadata": metadata,
                "cleaning_version": int(record.cleaning_version or 0),
                "confirmed_at": now,
                "confirmed_by": operator_id,
                "status": FileStatus.CONFIRMED,
                "content_hash": record.content_hash,
                "file_size": record.file_size,
                "chunk_count": 0,
                "token_count": 0,
                "content_type": record.content_type,
                "processing_params": record.processing_params,
                "parse_metadata": record.parse_metadata,
                "processing_stage": "replacement_preparing",
                "processing_progress": 68,
                "error_message": None,
                "created_by": operator_id,
                "updated_by": operator_id,
                "is_folder": False,
            }
            try:
                target_record, created_candidate = await self.file_repository.create_cleaning_replacement_candidate(
                    file_id=candidate_id,
                    kb_id=kb_id,
                    target_file_id=file_id,
                    data=candidate_data,
                    target_restore_data={
                        "status": FileStatus.INDEXED,
                        "processing_stage": None,
                        "processing_progress": 100,
                        "cleaning_draft_file": record.markdown_file,
                        "cleaning_metadata": (
                            deepcopy(previous_confirmation.get("cleaning_metadata") or {})
                            if isinstance(previous_confirmation, dict)
                            else {}
                        ),
                        "confirmed_at": record.confirmed_at,
                        "confirmed_by": record.confirmed_by,
                        "error_message": None,
                        "updated_by": operator_id,
                    },
                )
            except Exception:
                await self._delete_draft(candidate_draft_path)
                raise
            if not created_candidate:
                await self._delete_draft(candidate_draft_path)
            elif working_draft_path != record.markdown_file:
                await self._delete_draft(working_draft_path)
            if not created_candidate and target_record.status in {
                FileStatus.CONFIRMED,
                FileStatus.INDEXED,
                FileStatus.INDEXING,
                FileStatus.ERROR_REPLACEMENT_CLEANUP,
            }:
                return {
                    "file_id": target_record.file_id,
                    "status": target_record.status,
                    "idempotent": True,
                }
        else:
            updated = await self.file_repository.update_cleaning_fields_with_version(
                kb_id=kb_id,
                file_id=file_id,
                expected_version=expected_version,
                increment_version=False,
                data={
                    "status": FileStatus.CONFIRMED,
                    "processing_stage": None,
                    "processing_progress": 68,
                    "markdown_file": record.cleaning_draft_file,
                    "cleaning_metadata": metadata,
                    "confirmed_at": now,
                    "confirmed_by": operator_id,
                    "error_message": None,
                    "updated_by": operator_id,
                },
                allowed_statuses={
                    FileStatus.WAITING_CONFIRMATION,
                    FileStatus.ERROR_INDEXING,
                },
            )
            if updated is None:
                current = await self._get_record(kb_id, file_id)
                if current.confirmed_at and current.status in {
                    FileStatus.CONFIRMED,
                    FileStatus.INDEXING,
                    FileStatus.INDEXED,
                    FileStatus.ERROR_REPLACEMENT_CLEANUP,
                }:
                    return {
                        "file_id": current.file_id,
                        "status": current.status,
                        "idempotent": True,
                    }
                raise CleaningVersionConflict("清洗草稿已被其他编辑更新，请刷新后重试")
            target_record = updated

        result = await knowledge_base.index_file(
            kb_id,
            target_record.file_id,
            operator_id=operator_id,
            params=record.processing_params or {},
        )
        return {
            "file_id": target_record.file_id,
            "previous_file_id": file_id if created_candidate else None,
            "status": result.get("status") or FileStatus.INDEXED,
            "idempotent": False,
        }


__all__ = [
    "CleaningVersionConflict",
    "DocumentCleaningError",
    "DocumentCleaningNotFound",
    "DocumentCleaningService",
]
