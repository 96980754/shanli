from __future__ import annotations

import asyncio
import hashlib
import os
import re
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from urllib.parse import urlparse

from arq import Retry
from arq.jobs import Job, JobStatus
from yuxi.knowledge.runtime import knowledge_base
from yuxi.knowledge.utils.kb_utils import (
    calculate_content_hash,
    parse_minio_url,
    prepare_item_metadata,
    resolve_processing_params,
    sanitize_processing_error,
)
from yuxi.repositories.knowledge_base_repository import KnowledgeBaseRepository
from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository
from yuxi.services.run_queue_service import get_arq_pool
from yuxi.storage.minio.client import MinIOClient, get_minio_client
from yuxi.utils import logger
from yuxi.utils.datetime_utils import utc_isoformat, utc_now_naive

DUPLICATE_STRATEGIES = {"prompt", "skip", "replace", "keep_both"}
EXACT_CONTENT_STRATEGIES = ("skip",)
SAME_NAME_STRATEGIES = ("skip", "replace", "keep_both")
REPLACEMENT_CLEANUP_TASK = "process_document_replacement_cleanup"
REPLACEMENT_CLEANUP_MAX_RETRIES = 3
REPLACEMENT_CLEANUP_MAX_TRIES = REPLACEMENT_CLEANUP_MAX_RETRIES + 1
REPLACEMENT_CLEANUP_LEASE_SECONDS = 2 * 60 * 60
REPLACEMENT_CLEANUP_CLAIM_GRACE_SECONDS = 30
ACTIVE_ARQ_JOB_STATUSES = {"queued", "deferred", "in_progress"}


async def get_arq_job_status(queue, task_id: str) -> str:
    status = await Job(task_id, queue).status()
    return status.value if isinstance(status, JobStatus) else str(status)


class DuplicateConflictError(Exception):
    def __init__(
        self,
        *,
        conflict_type: str,
        incoming: dict[str, Any],
        conflicts: list[dict[str, Any]],
        allowed_strategies: tuple[str, ...],
        message: str,
        cleanup_pending: bool = False,
    ) -> None:
        super().__init__(message)
        self.detail = {
            "code": "duplicate_conflict",
            "conflict_type": conflict_type,
            "message": message,
            "incoming": incoming,
            "conflicts": conflicts,
            "allowed_strategies": list(allowed_strategies),
        }
        if cleanup_pending:
            self.detail["cleanup_pending"] = True


class DuplicateStrategyError(ValueError):
    pass


class ReplacementCleanupInvariantError(RuntimeError):
    pass


class ReplacementInProgressError(RuntimeError):
    def __init__(self, *, target_file_id: str, candidate_file_id: str) -> None:
        super().__init__("该文档已有正在处理的替换版本")
        self.detail = {
            "code": "replacement_in_progress",
            "message": "该文档已有正在处理的替换版本",
            "target_file_id": target_file_id,
            "candidate_file_id": candidate_file_id,
        }


@dataclass(frozen=True)
class UploadDecision:
    action: str
    existing_file_id: str | None = None
    conflicts: tuple[Any, ...] = ()


@dataclass(frozen=True)
class DocumentCreationResult:
    action: str
    file_meta: dict[str, Any] | None = None
    existing_file_id: str | None = None
    cleanup_pending: bool = False


class DocumentIngestionService:
    def __init__(self, *, file_repository: KnowledgeFileRepository | None = None) -> None:
        self.file_repository = file_repository or KnowledgeFileRepository()

    async def check_upload_conflict(
        self,
        *,
        kb_id: str,
        filename: str,
        content_hash: str,
        file_size: int,
        duplicate_strategy: str,
        replace_file_id: str | None = None,
    ) -> UploadDecision:
        strategy = self._validate_strategy(duplicate_strategy)
        incoming = {"filename": filename, "size": file_size, "content_hash": content_hash}

        exact_records = await self.file_repository.list_by_content_hash(kb_id=kb_id, content_hash=content_hash)
        if exact_records:
            if strategy == "skip":
                return UploadDecision(action="skipped", existing_file_id=exact_records[0].file_id)
            if strategy != "prompt":
                raise DuplicateStrategyError("exact_content only supports the skip strategy")
            raise DuplicateConflictError(
                conflict_type="exact_content",
                incoming=incoming,
                conflicts=self._serialize_conflicts(exact_records),
                allowed_strategies=EXACT_CONTENT_STRATEGIES,
                message="同一知识库中已存在内容完全相同的文件",
            )

        same_name_records = await self.file_repository.list_same_name_files(kb_id=kb_id, filename=filename)
        if not same_name_records:
            if strategy == "replace":
                message = "replace_file_id is not an active same-name document in this knowledge base"
                raise DuplicateStrategyError(message)
            return UploadDecision(action="upload")

        if strategy == "prompt":
            raise DuplicateConflictError(
                conflict_type="same_name",
                incoming=incoming,
                conflicts=self._serialize_conflicts(same_name_records),
                allowed_strategies=SAME_NAME_STRATEGIES,
                message="同一知识库中已存在同名但内容不同的文件",
            )
        if strategy == "skip":
            return UploadDecision(action="skipped", existing_file_id=same_name_records[0].file_id)
        if strategy == "replace" and not any(record.file_id == replace_file_id for record in same_name_records):
            raise DuplicateStrategyError("replace_file_id is not an active same-name document in this knowledge base")
        if strategy == "replace" and replace_file_id:
            pending_candidates = await self.file_repository.list_pending_replacement_candidates(
                kb_id=kb_id,
                replacement_target_file_id=replace_file_id,
            )
            if pending_candidates:
                raise ReplacementInProgressError(
                    target_file_id=replace_file_id,
                    candidate_file_id=pending_candidates[0].file_id,
                )
        return UploadDecision(action="upload", conflicts=tuple(same_name_records))

    async def create_uploaded_document(
        self,
        *,
        kb_id: str,
        item: str,
        params: dict[str, Any],
        operator_id: str,
    ) -> DocumentCreationResult:
        minio_client = get_minio_client()
        self._validate_upload_url_host(item, getattr(minio_client, "public_endpoint", ""))
        bucket_name, object_name = parse_minio_url(item)
        staged_filename = self._filename_from_staged_object(kb_id, bucket_name, object_name)
        try:
            duplicate_strategy = self._validate_strategy(str(params.get("duplicate_strategy") or "prompt"))
        except DuplicateStrategyError:
            await self._delete_staged_object_if_unclaimed(kb_id, item, bucket_name, object_name)
            raise
        replace_file_id = self._normalize_optional_string(params.get("replace_file_id"))
        file_bytes = await minio_client.adownload_file(bucket_name, object_name)
        preprocessed_map = params.get("_preprocessed_map")
        is_preprocessed = isinstance(preprocessed_map, dict) and item in preprocessed_map
        if not is_preprocessed:
            from yuxi.knowledge.parser.unified import validate_document_bytes

            try:
                validate_document_bytes(staged_filename, file_bytes, params=params)
            except ValueError:
                await self._delete_staged_object_if_unclaimed(kb_id, item, bucket_name, object_name)
                raise
        content_hash = await calculate_content_hash(file_bytes)
        file_size = len(file_bytes)

        trusted_params = dict(params)
        trusted_params["content_hashes"] = {item: content_hash}
        trusted_params["file_sizes"] = {item: file_size}
        requested_source_path = params.get("source_path")
        if isinstance(requested_source_path, str):
            requested_source_path = requested_source_path.strip().replace("\\", "/")
            source_parts = [part for part in requested_source_path.split("/") if part and part != "."]
            if (
                not source_parts
                or requested_source_path.startswith("/")
                or re.match(r"^[A-Za-z]:/", requested_source_path)
                or ".." in source_parts
                or len("/".join(source_parts)) > 512
            ):
                requested_source_path = None
        else:
            requested_source_path = None
        trusted_params["source_path"] = requested_source_path or staged_filename
        if is_preprocessed:
            preprocessed_info = preprocessed_map[item]
            if not isinstance(preprocessed_info, dict):
                await self._delete_staged_object_if_unclaimed(kb_id, item, bucket_name, object_name)
                raise ValueError("Invalid preprocessed file metadata")
            expected_object_name = f"{kb_id}/upload/{content_hash}.html"
            if object_name != expected_object_name:
                await self._delete_staged_object_if_unclaimed(kb_id, item, bucket_name, object_name)
                raise ValueError("Preprocessed file object does not match its server content hash")
            trusted_preprocessed_info = dict(preprocessed_info)
            trusted_preprocessed_info.update({"path": item, "content_hash": content_hash, "file_size": file_size})
            trusted_params["_preprocessed_map"] = {item: trusted_preprocessed_info}
        try:
            metadata = await prepare_item_metadata(item, "file", kb_id, params=trusted_params)
            filename = str(metadata.get("filename") or staged_filename)
            kb_record = await KnowledgeBaseRepository().get_by_kb_id(kb_id)
            metadata["processing_params"] = resolve_processing_params(
                kb_additional_params=kb_record.additional_params if kb_record else None,
                file_processing_params=metadata.get("processing_params"),
            )
        except Exception:
            await self._delete_staged_object_if_unclaimed(kb_id, item, bucket_name, object_name)
            raise
        metadata.update(
            {
                "filename": filename,
                "content_hash": content_hash,
                "size": file_size,
                "status": "uploaded",
                "processing_progress": 0,
                "created_by": operator_id,
                "original_filename": os.path.splitext(os.path.basename(filename))[0],
                "minio_url": item,
            }
        )

        record_data = {
            "kb_id": kb_id,
            "parent_id": metadata.get("parent_id"),
            "filename": metadata["filename"],
            "original_filename": metadata.get("original_filename"),
            "file_type": metadata.get("file_type"),
            "path": item,
            "minio_url": item,
            "status": metadata["status"],
            "content_hash": content_hash,
            "file_size": file_size,
            "content_type": "file",
            "processing_params": metadata.get("processing_params"),
            "processing_progress": 0,
            "is_folder": False,
            "is_active": True,
            "created_by": operator_id,
            "updated_by": operator_id,
        }
        try:
            outcome = await self.file_repository.create_document_with_duplicate_guard(
                file_id=metadata["file_id"],
                data=record_data,
                duplicate_strategy=duplicate_strategy,
                replace_file_id=replace_file_id,
            )
        except Exception:
            await self._delete_staged_object_if_unclaimed(kb_id, item, bucket_name, object_name)
            raise

        if outcome.action == "created" and outcome.record is not None:
            kb = await knowledge_base.aget_kb(kb_id)
            await kb.refresh_database_stats(kb_id)
            return DocumentCreationResult(action="created", file_meta=kb._file_record_to_meta(outcome.record))
        if outcome.action == "existing" and outcome.record is not None:
            kb = await knowledge_base.aget_kb(kb_id)
            return DocumentCreationResult(action="existing", file_meta=kb._file_record_to_meta(outcome.record))

        cleanup_succeeded = await self._delete_staged_object_if_unclaimed(
            kb_id,
            item,
            bucket_name,
            object_name,
        )
        if outcome.action == "skipped" and outcome.existing is not None:
            return DocumentCreationResult(
                action="skipped",
                existing_file_id=outcome.existing.file_id,
                cleanup_pending=not cleanup_succeeded,
            )
        if outcome.action == "invalid_replace_target":
            raise DuplicateStrategyError("replace_file_id is not an active same-name document in this knowledge base")
        if outcome.action == "replacement_in_progress" and outcome.existing is not None:
            raise ReplacementInProgressError(
                target_file_id=replace_file_id or "",
                candidate_file_id=outcome.existing.file_id,
            )
        if outcome.action == "conflict":
            conflict_type = outcome.conflict_type or "same_name"
            allowed = EXACT_CONTENT_STRATEGIES if conflict_type == "exact_content" else SAME_NAME_STRATEGIES
            message = (
                "同一知识库中已存在内容完全相同的文件"
                if conflict_type == "exact_content"
                else "同一知识库中已存在同名但内容不同的文件"
            )
            raise DuplicateConflictError(
                conflict_type=conflict_type,
                incoming={"filename": filename, "size": file_size, "content_hash": content_hash},
                conflicts=self._serialize_conflicts(list(outcome.conflicts)),
                allowed_strategies=allowed,
                message=message,
                cleanup_pending=not cleanup_succeeded,
            )
        raise RuntimeError("Unsupported document creation outcome")

    async def activate_replacement(self, *, kb_id: str, new_file_id: str, old_file_id: str) -> None:
        await self.file_repository.switch_active_version(
            kb_id=kb_id,
            new_file_id=new_file_id,
            old_file_id=old_file_id,
        )
        try:
            await self.enqueue_replacement_cleanup(kb_id=kb_id, new_file_id=new_file_id, old_file_id=old_file_id)
        except Exception as error:
            logger.error(
                "Replacement {} was activated but cleanup could not be enqueued: {}",
                new_file_id,
                sanitize_processing_error(error),
            )

    @staticmethod
    def _cleanup_task_prefix(new_file_id: str) -> str:
        digest = hashlib.sha256(new_file_id.encode()).hexdigest()[:24]
        return f"replacement-cleanup:{digest}:"

    @staticmethod
    def _next_cleanup_task_id(new_file_id: str, current_task_id: str | None) -> str:
        prefix = DocumentIngestionService._cleanup_task_prefix(new_file_id)
        generation = 0
        if current_task_id and current_task_id.startswith(prefix):
            suffix = current_task_id[len(prefix) :]
            generation = int(suffix) if suffix.isdigit() else 0
        return f"{prefix}{generation + 1}"

    @staticmethod
    def _lease_is_valid(lease_expires_at, now) -> bool:
        if lease_expires_at is None:
            return False
        if getattr(lease_expires_at, "tzinfo", None) is not None and getattr(now, "tzinfo", None) is None:
            lease_expires_at = lease_expires_at.replace(tzinfo=None)
        return lease_expires_at > now

    @staticmethod
    def _task_update_is_recent(task_updated_at, now) -> bool:
        if task_updated_at is None:
            return False
        if getattr(task_updated_at, "tzinfo", None) is not None and getattr(now, "tzinfo", None) is None:
            task_updated_at = task_updated_at.replace(tzinfo=None)
        return task_updated_at >= now - timedelta(seconds=REPLACEMENT_CLEANUP_CLAIM_GRACE_SECONDS)

    async def _ensure_replacement_cleanup_enqueued(
        self,
        *,
        kb_id: str,
        new_file_id: str,
        old_file_id: str,
        queue,
        force_reclaim: bool = False,
    ) -> tuple[str, bool]:
        record = await self.file_repository.get_by_file_id(new_file_id)
        if record is None or record.kb_id != kb_id or not record.is_active or record.previous_version_id != old_file_id:
            raise ReplacementCleanupInvariantError("Replacement version has not been switched")

        current_task_id = record.processing_task_id
        current_task_attempt = int(getattr(record, "processing_task_attempt", 0) or 0)
        current_task_updated_at = getattr(record, "processing_task_updated_at", None)
        current_lease_expires_at = getattr(record, "processing_task_lease_expires_at", None)
        now = utc_now_naive()
        job_status = "not_found"
        if current_task_id and not force_reclaim:
            try:
                job_status = await get_arq_job_status(queue, current_task_id)
            except Exception as error:
                await self.mark_cleanup_failure(kb_id=kb_id, new_file_id=new_file_id, error=error)
                raise
            lease_is_valid = self._lease_is_valid(
                current_lease_expires_at,
                now,
            )
            if job_status == "complete" or (job_status in ACTIVE_ARQ_JOB_STATUSES and lease_is_valid):
                return current_task_id, False
            if (
                job_status == "not_found"
                and lease_is_valid
                and self._task_update_is_recent(current_task_updated_at, now)
            ):
                return current_task_id, False

        can_reuse_missing_task_id = (
            current_task_id
            and current_task_id.startswith(self._cleanup_task_prefix(new_file_id))
            and job_status == "not_found"
            and not force_reclaim
        )
        task_id = (
            current_task_id if can_reuse_missing_task_id else self._next_cleanup_task_id(new_file_id, current_task_id)
        )
        lease_expires_at = now + timedelta(seconds=REPLACEMENT_CLEANUP_LEASE_SECONDS)
        reset_attempt = current_task_id is None or force_reclaim
        claimed = await self.file_repository.claim_replacement_cleanup(
            kb_id=kb_id,
            file_id=new_file_id,
            expected_task_id=current_task_id,
            expected_lease_expires_at=current_lease_expires_at,
            task_id=task_id,
            task_updated_at=now,
            lease_expires_at=lease_expires_at,
            reset_attempt=reset_attempt,
        )
        if claimed is None:
            latest = await self.file_repository.get_by_file_id(new_file_id)
            if latest is None or not latest.processing_task_id:
                raise RuntimeError("Replacement cleanup task could not be claimed")
            return latest.processing_task_id, False

        try:
            enqueue_kwargs = {"_job_id": task_id}
            if not reset_attempt and current_task_attempt > 0:
                enqueue_kwargs["_job_try"] = current_task_attempt + 1
            job = await queue.enqueue_job(
                REPLACEMENT_CLEANUP_TASK,
                kb_id,
                new_file_id,
                old_file_id,
                task_id,
                **enqueue_kwargs,
            )
            if job is None:
                raise RuntimeError("Replacement cleanup job was not accepted by ARQ")
        except Exception as error:
            await self.file_repository.update_fields(
                file_id=new_file_id,
                kb_id=kb_id,
                data={
                    "status": "error_replacement_cleanup",
                    "processing_stage": "replacement_cleanup",
                    "processing_task_updated_at": now,
                    "processing_task_lease_expires_at": None,
                    "error_message": sanitize_processing_error(error),
                },
            )
            raise
        return task_id, True

    async def enqueue_replacement_cleanup(
        self,
        *,
        kb_id: str,
        new_file_id: str,
        old_file_id: str,
        force_reclaim: bool = False,
    ) -> str:
        try:
            queue = await get_arq_pool()
        except Exception as error:
            await self.file_repository.update_fields(
                file_id=new_file_id,
                kb_id=kb_id,
                data={
                    "status": "error_replacement_cleanup",
                    "processing_stage": "replacement_cleanup",
                    "processing_task_updated_at": utc_now_naive(),
                    "processing_task_lease_expires_at": None,
                    "error_message": sanitize_processing_error(error),
                },
            )
            raise
        task_id, _enqueued = await self._ensure_replacement_cleanup_enqueued(
            kb_id=kb_id,
            new_file_id=new_file_id,
            old_file_id=old_file_id,
            queue=queue,
            force_reclaim=force_reclaim,
        )
        return task_id

    async def recover_pending_replacement_cleanups(self, *, queue=None) -> int:
        pending_records = await self.file_repository.list_pending_replacement_cleanup()
        if not pending_records:
            return 0

        queue = queue or await get_arq_pool()
        recovered = 0
        for record in pending_records:
            if not record.previous_version_id or record.status == "error_replacement_cleanup":
                continue
            try:
                _task_id, enqueued = await self._ensure_replacement_cleanup_enqueued(
                    kb_id=record.kb_id,
                    new_file_id=record.file_id,
                    old_file_id=record.previous_version_id,
                    queue=queue,
                )
                recovered += int(enqueued)
            except Exception as error:
                logger.error(
                    "Failed to recover replacement cleanup for {}: {}",
                    record.file_id,
                    sanitize_processing_error(error),
                )
        return recovered

    async def cleanup_replaced_version(
        self,
        *,
        kb_id: str,
        new_file_id: str,
        old_file_id: str,
        task_id: str,
    ) -> None:
        new_record = await self.file_repository.get_by_file_id(new_file_id)
        old_record = await self.file_repository.get_by_file_id(old_file_id)
        if new_record is None or old_record is None or new_record.kb_id != kb_id or old_record.kb_id != kb_id:
            raise ReplacementCleanupInvariantError("Replacement version record not found")
        if new_record.processing_task_id != task_id:
            logger.info("Skip stale replacement cleanup task {} for {}", task_id, new_file_id)
            return
        if new_record.previous_version_id != old_file_id or not new_record.is_active or old_record.is_active:
            raise ReplacementCleanupInvariantError("Replacement version has not been switched")

        await self.file_repository.update_fields(
            file_id=new_file_id,
            kb_id=kb_id,
            data={
                "status": "indexed",
                "processing_stage": "replacement_cleanup",
                "processing_progress": 96,
                "processing_task_updated_at": utc_now_naive(),
                "processing_task_lease_expires_at": utc_now_naive()
                + timedelta(seconds=REPLACEMENT_CLEANUP_LEASE_SECONDS),
                "error_message": None,
            },
        )
        kb = await knowledge_base.aget_kb(kb_id)
        if kb_id not in kb.databases_meta:
            await kb._load_metadata()
        strict_vector_delete = getattr(kb, "delete_file_vectors_strict", None)
        if strict_vector_delete is None:
            raise ReplacementCleanupInvariantError("Knowledge base does not support strict vector cleanup")
        await self._cleanup_superseded_chain(
            kb_id=kb_id,
            first_file_id=old_file_id,
            strict_vector_delete=strict_vector_delete,
        )
        await self.file_repository.update_fields(
            file_id=new_file_id,
            kb_id=kb_id,
            data={
                "status": "indexed",
                "processing_stage": None,
                "processing_progress": 100,
                "processing_task_id": None,
                "processing_task_updated_at": None,
                "processing_task_lease_expires_at": None,
                "replacement_target_file_id": None,
                "error_message": None,
            },
        )

    async def _cleanup_superseded_chain(self, *, kb_id: str, first_file_id: str, strict_vector_delete) -> None:
        current_file_id: str | None = first_file_id
        visited: set[str] = set()
        while current_file_id and current_file_id not in visited:
            visited.add(current_file_id)
            record = await self.file_repository.get_by_file_id(current_file_id)
            if record is None or record.kb_id != kb_id or record.is_active:
                raise ReplacementCleanupInvariantError("Superseded replacement version is not safe to clean")

            await strict_vector_delete(kb_id, current_file_id)
            current_file_id = getattr(record, "previous_version_id", None)

    async def mark_cleanup_failure(
        self,
        *,
        kb_id: str,
        new_file_id: str,
        error: BaseException,
        attempt: int | None = None,
    ) -> None:
        data = {
            "status": "error_replacement_cleanup",
            "processing_stage": "replacement_cleanup",
            "processing_task_updated_at": utc_now_naive(),
            "error_message": sanitize_processing_error(error),
        }
        if attempt is not None:
            data["processing_task_attempt"] = attempt
        await self.file_repository.update_fields(
            file_id=new_file_id,
            kb_id=kb_id,
            data=data,
        )

    async def mark_cleanup_retry(
        self,
        *,
        kb_id: str,
        new_file_id: str,
        error: BaseException,
        attempt: int,
    ) -> None:
        await self.file_repository.update_fields(
            file_id=new_file_id,
            kb_id=kb_id,
            data={
                "status": "indexed",
                "processing_stage": "replacement_cleanup",
                "processing_task_attempt": attempt,
                "processing_task_updated_at": utc_now_naive(),
                "error_message": sanitize_processing_error(error),
            },
        )

    @staticmethod
    def _validate_strategy(value: str) -> str:
        strategy = value.strip().lower()
        if strategy not in DUPLICATE_STRATEGIES:
            raise DuplicateStrategyError(f"Unsupported duplicate_strategy: {value}")
        return strategy

    @staticmethod
    def _normalize_optional_string(value: object) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = value.strip()
        return normalized or None

    @staticmethod
    def _serialize_conflicts(records: list[Any]) -> list[dict[str, Any]]:
        return [
            {
                "file_id": record.file_id,
                "filename": record.filename,
                "size": int(record.file_size or 0),
                "content_hash": record.content_hash,
                "status": record.status,
                "is_active": bool(record.is_active),
                "created_at": utc_isoformat(record.created_at) if record.created_at else None,
            }
            for record in records
        ]

    @staticmethod
    def _validate_upload_url_host(item: str, public_endpoint: str) -> None:
        parsed = urlparse(item)
        expected_host = str(public_endpoint or "").strip().lower()
        actual_host = str(parsed.netloc or "").strip().lower()
        if parsed.scheme not in {"http", "https"} or not expected_host or actual_host != expected_host:
            raise ValueError("Invalid upload URL host for configured MinIO endpoint")

    @staticmethod
    def _filename_from_staged_object(kb_id: str, bucket_name: str, object_name: str) -> str:
        expected_bucket = MinIOClient.KB_BUCKETS["documents"]
        prefix = f"{kb_id}/upload/"
        if bucket_name != expected_bucket or not object_name.startswith(prefix):
            raise ValueError("Uploaded object does not belong to this knowledge base")
        relative_name = object_name[len(prefix) :]
        match = re.match(r"^(.+)_\d{13,19}(\.[^./]+)$", relative_name)
        filename = f"{match.group(1)}{match.group(2)}" if match else relative_name
        normalized = filename.strip().replace("\\", "/")
        if not normalized or normalized.startswith("/") or ".." in normalized.split("/"):
            raise ValueError("Invalid staged object filename")
        return normalized

    @staticmethod
    async def _delete_staged_object(bucket_name: str, object_name: str) -> bool:
        minio_client = get_minio_client()
        for attempt in range(1, 4):
            try:
                await minio_client.adelete_file(bucket_name, object_name)
                return True
            except Exception as error:
                logger.warning(
                    "Failed to clean staged upload on attempt {}/3: {}",
                    attempt,
                    sanitize_processing_error(error),
                )
                if attempt < 3:
                    await asyncio.sleep(0.05 * (2 ** (attempt - 1)))
        return False

    async def _delete_staged_object_if_unclaimed(
        self,
        kb_id: str,
        item: str,
        bucket_name: str,
        object_name: str,
    ) -> bool:
        try:
            if await self.file_repository.exists_by_storage_path(kb_id=kb_id, storage_path=item):
                return True
        except Exception as error:
            logger.warning(
                "Could not verify whether staged upload is already referenced: {}",
                sanitize_processing_error(error),
            )
            return False
        return await self._delete_staged_object(bucket_name, object_name)


async def process_document_replacement_cleanup(
    ctx: dict[str, Any],
    kb_id: str,
    new_file_id: str,
    old_file_id: str,
    task_id: str,
) -> None:
    service = DocumentIngestionService()
    try:
        await service.cleanup_replaced_version(
            kb_id=kb_id,
            new_file_id=new_file_id,
            old_file_id=old_file_id,
            task_id=task_id,
        )
    except Exception as error:
        attempt = max(1, int(ctx.get("job_try") or 1))
        should_retry = not isinstance(error, ReplacementCleanupInvariantError) and (
            attempt <= REPLACEMENT_CLEANUP_MAX_RETRIES
        )
        try:
            if should_retry:
                await service.mark_cleanup_retry(
                    kb_id=kb_id,
                    new_file_id=new_file_id,
                    error=error,
                    attempt=attempt,
                )
            else:
                await service.mark_cleanup_failure(
                    kb_id=kb_id,
                    new_file_id=new_file_id,
                    error=error,
                    attempt=attempt,
                )
        except Exception as status_error:
            logger.error(
                "Failed to persist replacement cleanup error for {}: {}",
                new_file_id,
                sanitize_processing_error(status_error),
            )
        if isinstance(error, ReplacementCleanupInvariantError):
            raise
        if should_retry:
            raise Retry(defer=2 ** (attempt - 1)) from error
        raise


__all__ = [
    "DocumentCreationResult",
    "DocumentIngestionService",
    "DuplicateConflictError",
    "DuplicateStrategyError",
    "ReplacementCleanupInvariantError",
    "ReplacementInProgressError",
    "REPLACEMENT_CLEANUP_MAX_RETRIES",
    "REPLACEMENT_CLEANUP_MAX_TRIES",
    "REPLACEMENT_CLEANUP_TASK",
    "get_arq_job_status",
    "process_document_replacement_cleanup",
]
