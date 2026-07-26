from __future__ import annotations

import hashlib
import os
from collections.abc import Iterator
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from sqlalchemy import DateTime, String, and_, case, cast, func, literal, or_, select, text, union_all, update

from yuxi.knowledge.enrichment import mark_enrichment_data_outdated
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_knowledge import KnowledgeFile
from yuxi.utils.datetime_utils import utc_now_naive

# asyncpg 单条 SQL 参数上限为 32767；按 file_id 批量查询时统一分批，避免
# mindmap_file_ids 等大尺寸传入触发 `too many parameters` 报错。
SQL_IN_BATCH_SIZE = 10_000
FAILED_REPLACEMENT_CANDIDATE_STATUSES = {
    "cancelled",
    "canceled",
    "parse_failed",
    "index_failed",
    "error_parsing",
    "error_indexing",
}


def stable_advisory_lock_key(namespace: str, value: str) -> int:
    """Return a process-independent signed 64-bit PostgreSQL advisory lock key."""
    digest = hashlib.sha256(f"{namespace}\0{value}".encode()).digest()[:8]
    return int.from_bytes(digest, byteorder="big", signed=True)


@dataclass(frozen=True)
class DocumentCreateOutcome:
    action: str
    record: KnowledgeFile | None = None
    existing: KnowledgeFile | None = None
    conflicts: tuple[KnowledgeFile, ...] = ()
    conflict_type: str | None = None


class KnowledgeFileRepository:
    _writable_fields = {
        "kb_id",
        "parent_id",
        "filename",
        "original_filename",
        "file_type",
        "path",
        "minio_url",
        "markdown_file",
        "status",
        "content_hash",
        "file_size",
        "chunk_count",
        "token_count",
        "content_type",
        "processing_params",
        "parse_metadata",
        "original_markdown_file",
        "cleaning_draft_file",
        "cleaning_metadata",
        "cleaning_version",
        "confirmed_at",
        "confirmed_by",
        "enrichment_data",
        "enrichment_status",
        "enrichment_version",
        "enrichment_content_hash",
        "enrichment_generated_at",
        "enrichment_error",
        "enrichment_possibly_outdated",
        "processing_stage",
        "processing_progress",
        "processing_task_id",
        "processing_task_attempt",
        "processing_task_updated_at",
        "processing_task_lease_expires_at",
        "replacement_target_file_id",
        "previous_version_id",
        "is_active",
        "superseded_at",
        "is_folder",
        "error_message",
        "created_by",
        "updated_by",
    }

    @staticmethod
    def _iter_batches(items: list[str], batch_size: int = SQL_IN_BATCH_SIZE) -> Iterator[list[str]]:
        for index in range(0, len(items), batch_size):
            yield items[index : index + batch_size]

    @classmethod
    def _sanitize_data(cls, data: dict[str, Any]) -> dict[str, Any]:
        sanitized = {key: value for key, value in data.items() if key in cls._writable_fields}
        if "processing_progress" in sanitized:
            sanitized["processing_progress"] = max(0, min(int(sanitized["processing_progress"] or 0), 100))
        if "cleaning_version" in sanitized:
            sanitized["cleaning_version"] = max(0, int(sanitized["cleaning_version"] or 0))
        if "enrichment_version" in sanitized:
            sanitized["enrichment_version"] = max(0, int(sanitized["enrichment_version"] or 0))
        if sanitized:
            sanitized["updated_at"] = utc_now_naive()
        return sanitized

    async def get_all(self) -> list[KnowledgeFile]:
        """获取所有文件记录"""
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(select(KnowledgeFile))
            return list(result.scalars().all())

    async def get_by_file_id(self, file_id: str) -> KnowledgeFile | None:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(select(KnowledgeFile).where(KnowledgeFile.file_id == file_id))
            return result.scalar_one_or_none()

    async def exists_by_storage_path(self, *, kb_id: str, storage_path: str) -> bool:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(KnowledgeFile.file_id)
                .where(
                    KnowledgeFile.kb_id == kb_id,
                    or_(KnowledgeFile.path == storage_path, KnowledgeFile.minio_url == storage_path),
                )
                .limit(1)
            )
            return result.scalar_one_or_none() is not None

    async def list_by_file_ids(self, file_ids: list[str]) -> list[KnowledgeFile]:
        normalized_ids = [file_id for file_id in file_ids if file_id]
        if not normalized_ids:
            return []

        records_by_id: dict[str, KnowledgeFile] = {}
        async with pg_manager.get_async_session_context() as session:
            for batch in self._iter_batches(normalized_ids):
                result = await session.execute(select(KnowledgeFile).where(KnowledgeFile.file_id.in_(batch)))
                records_by_id.update({record.file_id: record for record in result.scalars().all()})
        return [records_by_id[file_id] for file_id in normalized_ids if file_id in records_by_id]

    async def list_by_kb_id(self, kb_id: str) -> list[KnowledgeFile]:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(select(KnowledgeFile).where(KnowledgeFile.kb_id == kb_id))
            return list(result.scalars().all())

    async def list_by_kb_id_after(
        self,
        kb_id: str,
        *,
        after_file_id: str | None = None,
        limit: int = 500,
        files_only: bool = False,
    ) -> list[KnowledgeFile]:
        filters = [KnowledgeFile.kb_id == kb_id]
        if after_file_id:
            filters.append(KnowledgeFile.file_id > after_file_id)
        if files_only:
            filters.append(KnowledgeFile.is_folder.is_(False))

        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(KnowledgeFile)
                .where(*filters)
                .order_by(KnowledgeFile.file_id.asc())
                .limit(min(max(int(limit or 100), 1), 1000))
            )
            return list(result.scalars().all())

    async def get_filenames_by_file_ids(self, *, kb_id: str, file_ids: list[str]) -> dict[str, str]:
        normalized_ids = [file_id for file_id in file_ids if file_id]
        if not normalized_ids:
            return {}

        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(KnowledgeFile.file_id, KnowledgeFile.filename).where(
                    KnowledgeFile.kb_id == kb_id,
                    KnowledgeFile.file_id.in_(normalized_ids),
                )
            )
            return {str(file_id): str(filename or "") for file_id, filename in result.all()}

    async def list_children(
        self,
        *,
        kb_id: str,
        parent_id: str | None,
        include_version_history: bool = False,
    ) -> list[KnowledgeFile]:
        filters = [KnowledgeFile.kb_id == kb_id, self._parent_condition(parent_id)]
        if not include_version_history:
            filters.append(self._visible_version_condition())
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(KnowledgeFile)
                .where(*filters)
                .order_by(KnowledgeFile.is_folder.desc(), func.lower(KnowledgeFile.filename).asc())
            )
            return list(result.scalars().all())

    async def list_same_name_files(self, *, kb_id: str, filename: str) -> list[KnowledgeFile]:
        normalized_filename = filename.strip()
        if not normalized_filename:
            return []

        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(KnowledgeFile)
                .where(
                    KnowledgeFile.kb_id == kb_id,
                    KnowledgeFile.is_folder.is_(False),
                    KnowledgeFile.is_active.is_(True),
                    func.lower(KnowledgeFile.filename) == normalized_filename.lower(),
                )
                .order_by(KnowledgeFile.created_at.desc())
            )
            return list(result.scalars().all())

    async def list_by_content_hash(self, *, kb_id: str, content_hash: str) -> list[KnowledgeFile]:
        normalized_hash = content_hash.strip()
        if not normalized_hash:
            return []

        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(KnowledgeFile)
                .where(
                    KnowledgeFile.kb_id == kb_id,
                    KnowledgeFile.is_folder.is_(False),
                    KnowledgeFile.content_hash == normalized_hash,
                    self._not_failed_replacement_candidate_condition(),
                )
                .order_by(KnowledgeFile.created_at.desc(), KnowledgeFile.file_id.asc())
            )
            return list(result.scalars().all())

    async def list_file_ids_by_filename_contains(
        self,
        *,
        kb_id: str,
        filename_pattern: str,
        limit: int = 10_000,
    ) -> list[str]:
        normalized_pattern = filename_pattern.replace("%", "").strip().lower()
        if not normalized_pattern:
            return []

        escaped_pattern = normalized_pattern.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(KnowledgeFile.file_id)
                .where(
                    KnowledgeFile.kb_id == kb_id,
                    KnowledgeFile.is_folder.is_(False),
                    KnowledgeFile.is_active.is_(True),
                    func.lower(KnowledgeFile.filename).like(f"%{escaped_pattern}%", escape="\\"),
                )
                .order_by(KnowledgeFile.file_id.asc())
                .limit(min(max(int(limit or 100), 1), 10_000))
            )
            return [str(file_id) for file_id in result.scalars().all()]

    async def list_inactive_file_ids(self, *, kb_id: str) -> list[str]:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(KnowledgeFile.file_id)
                .where(
                    KnowledgeFile.kb_id == kb_id,
                    KnowledgeFile.is_folder.is_(False),
                    KnowledgeFile.is_active.is_(False),
                )
                .order_by(KnowledgeFile.file_id.asc())
            )
            return [str(file_id) for file_id in result.scalars().all()]

    async def list_pending_replacement_candidates(
        self,
        *,
        kb_id: str,
        replacement_target_file_id: str,
    ) -> list[KnowledgeFile]:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(KnowledgeFile)
                .where(
                    KnowledgeFile.kb_id == kb_id,
                    KnowledgeFile.is_folder.is_(False),
                    KnowledgeFile.is_active.is_(False),
                    KnowledgeFile.replacement_target_file_id == replacement_target_file_id,
                    or_(
                        KnowledgeFile.status.is_(None),
                        KnowledgeFile.status.notin_(FAILED_REPLACEMENT_CANDIDATE_STATUSES),
                    ),
                )
                .order_by(KnowledgeFile.created_at.asc(), KnowledgeFile.file_id.asc())
            )
            return list(result.scalars().all())

    async def exists_by_content_hash(self, *, kb_id: str, content_hash: str) -> bool:
        normalized_hash = content_hash.strip()
        if not normalized_hash:
            return False

        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(KnowledgeFile.file_id)
                .where(
                    KnowledgeFile.kb_id == kb_id,
                    KnowledgeFile.is_folder.is_(False),
                    KnowledgeFile.content_hash == normalized_hash,
                    self._not_failed_replacement_candidate_condition(),
                )
                .limit(1)
            )
            return result.scalar_one_or_none() is not None

    async def create_document_with_duplicate_guard(
        self,
        *,
        file_id: str,
        data: dict[str, Any],
        duplicate_strategy: str,
        replace_file_id: str | None = None,
    ) -> DocumentCreateOutcome:
        sanitized_data = self._sanitize_data(data)
        kb_id = str(sanitized_data.get("kb_id") or "")
        content_hash = str(sanitized_data.get("content_hash") or "").strip()
        filename = str(sanitized_data.get("filename") or "").strip()
        if not kb_id or not content_hash or not filename:
            raise ValueError("kb_id, content_hash and filename are required")

        lock_keys = {
            stable_advisory_lock_key("knowledge-file-content", f"{kb_id}\0{content_hash}"),
            stable_advisory_lock_key("knowledge-file-name", f"{kb_id}\0{filename.casefold()}"),
        }
        if duplicate_strategy == "keep_both":
            lock_keys.add(stable_advisory_lock_key("knowledge-file-name-allocation", kb_id))
        if duplicate_strategy == "replace" and replace_file_id:
            lock_keys.add(stable_advisory_lock_key("knowledge-file-replacement-target", f"{kb_id}\0{replace_file_id}"))

        async with pg_manager.get_async_session_context() as session:
            for lock_key in sorted(lock_keys):
                await session.execute(text("SELECT pg_advisory_xact_lock(:lock_key)"), {"lock_key": lock_key})

            exact_records = list(
                (
                    await session.execute(
                        select(KnowledgeFile)
                        .where(
                            KnowledgeFile.kb_id == kb_id,
                            KnowledgeFile.is_folder.is_(False),
                            KnowledgeFile.content_hash == content_hash,
                            self._not_failed_replacement_candidate_condition(),
                        )
                        .order_by(KnowledgeFile.created_at.desc(), KnowledgeFile.file_id.asc())
                    )
                )
                .scalars()
                .all()
            )
            if exact_records:
                same_upload = next(
                    (
                        record
                        for record in exact_records
                        if str(record.path or "") == str(sanitized_data.get("path") or "")
                    ),
                    None,
                )
                if same_upload is not None:
                    return DocumentCreateOutcome(action="existing", record=same_upload, existing=same_upload)
                if duplicate_strategy == "skip":
                    return DocumentCreateOutcome(action="skipped", existing=exact_records[0])
                return DocumentCreateOutcome(
                    action="conflict",
                    conflicts=tuple(exact_records),
                    conflict_type="exact_content",
                )

            same_name_records = list(
                (
                    await session.execute(
                        select(KnowledgeFile)
                        .where(
                            KnowledgeFile.kb_id == kb_id,
                            KnowledgeFile.is_folder.is_(False),
                            KnowledgeFile.is_active.is_(True),
                            func.lower(KnowledgeFile.filename) == filename.lower(),
                        )
                        .order_by(KnowledgeFile.created_at.desc(), KnowledgeFile.file_id.asc())
                    )
                )
                .scalars()
                .all()
            )

            if same_name_records and duplicate_strategy == "prompt":
                return DocumentCreateOutcome(
                    action="conflict",
                    conflicts=tuple(same_name_records),
                    conflict_type="same_name",
                )
            if same_name_records and duplicate_strategy == "skip":
                return DocumentCreateOutcome(action="skipped", existing=same_name_records[0])
            if duplicate_strategy == "replace":
                replacement_target = (
                    await session.execute(
                        select(KnowledgeFile)
                        .where(
                            KnowledgeFile.kb_id == kb_id,
                            KnowledgeFile.file_id == replace_file_id,
                            KnowledgeFile.is_folder.is_(False),
                            KnowledgeFile.is_active.is_(True),
                            func.lower(KnowledgeFile.filename) == filename.lower(),
                        )
                        .with_for_update()
                    )
                ).scalar_one_or_none()
                if replacement_target is None:
                    return DocumentCreateOutcome(
                        action="invalid_replace_target",
                        conflicts=tuple(same_name_records),
                        conflict_type="same_name",
                    )
                pending_candidates = list(
                    (
                        await session.execute(
                            select(KnowledgeFile)
                            .where(
                                KnowledgeFile.kb_id == kb_id,
                                KnowledgeFile.is_folder.is_(False),
                                KnowledgeFile.is_active.is_(False),
                                KnowledgeFile.replacement_target_file_id == replacement_target.file_id,
                                or_(
                                    KnowledgeFile.status.is_(None),
                                    KnowledgeFile.status.notin_(FAILED_REPLACEMENT_CANDIDATE_STATUSES),
                                ),
                            )
                            .order_by(KnowledgeFile.created_at.asc(), KnowledgeFile.file_id.asc())
                        )
                    )
                    .scalars()
                    .all()
                )
                if pending_candidates:
                    return DocumentCreateOutcome(
                        action="replacement_in_progress",
                        existing=pending_candidates[0],
                    )
                sanitized_data["replacement_target_file_id"] = replacement_target.file_id
                sanitized_data["is_active"] = False
                sanitized_data["processing_stage"] = "replacement_preparing"
                if replacement_target.enrichment_data:
                    sanitized_data.update(
                        {
                            "enrichment_data": mark_enrichment_data_outdated(replacement_target.enrichment_data),
                            "enrichment_status": "possibly_outdated",
                            "enrichment_version": int(replacement_target.enrichment_version or 0),
                            "enrichment_content_hash": replacement_target.enrichment_content_hash,
                            "enrichment_generated_at": replacement_target.enrichment_generated_at,
                            "enrichment_error": None,
                            "enrichment_possibly_outdated": True,
                        }
                    )
            elif duplicate_strategy == "keep_both" and same_name_records:
                all_names = set(
                    (
                        await session.execute(
                            select(func.lower(KnowledgeFile.filename)).where(
                                KnowledgeFile.kb_id == kb_id,
                                KnowledgeFile.is_folder.is_(False),
                                KnowledgeFile.is_active.is_(True),
                            )
                        )
                    ).scalars()
                )
                sanitized_data["filename"] = self._next_available_filename(filename, all_names)

            record = KnowledgeFile(file_id=file_id, **sanitized_data)
            session.add(record)
            await session.flush()
            return DocumentCreateOutcome(action="created", record=record)

    async def switch_active_version(self, *, kb_id: str, new_file_id: str, old_file_id: str) -> KnowledgeFile:
        async with pg_manager.get_async_session_context() as session:
            target_lock_key = stable_advisory_lock_key(
                "knowledge-file-replacement-target",
                f"{kb_id}\0{old_file_id}",
            )
            await session.execute(
                text("SELECT pg_advisory_xact_lock(:lock_key)"),
                {"lock_key": target_lock_key},
            )
            records = list(
                (
                    await session.execute(
                        select(KnowledgeFile)
                        .where(
                            KnowledgeFile.kb_id == kb_id,
                            KnowledgeFile.file_id.in_([new_file_id, old_file_id]),
                        )
                        .order_by(KnowledgeFile.file_id.asc())
                        .with_for_update()
                    )
                )
                .scalars()
                .all()
            )
            records_by_id = {record.file_id: record for record in records}
            new_record = records_by_id.get(new_file_id)
            old_record = records_by_id.get(old_file_id)
            if new_record is None or old_record is None:
                raise ValueError("Replacement version record not found")
            if new_record.previous_version_id == old_file_id and new_record.is_active and not old_record.is_active:
                return new_record
            if new_record.replacement_target_file_id != old_file_id:
                raise ValueError("Replacement relationship changed")
            if not old_record.is_active:
                raise ValueError("Replacement target is no longer active")
            if new_record.status != "indexed":
                raise ValueError("New document version must be indexed before activation")

            now = utc_now_naive()
            new_record.is_active = True
            new_record.previous_version_id = old_file_id
            new_record.processing_stage = "replacement_cleanup"
            new_record.processing_progress = 95
            new_record.error_message = None
            new_record.updated_at = now
            old_record.is_active = False
            old_record.superseded_at = now
            old_record.updated_at = now
            await session.flush()
            return new_record

    async def list_pending_replacement_cleanup(self) -> list[KnowledgeFile]:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(KnowledgeFile).where(
                    KnowledgeFile.is_active.is_(True),
                    KnowledgeFile.previous_version_id.is_not(None),
                    KnowledgeFile.processing_stage == "replacement_cleanup",
                )
            )
            return list(result.scalars().all())

    async def claim_replacement_cleanup(
        self,
        *,
        kb_id: str,
        file_id: str,
        expected_task_id: str | None,
        expected_lease_expires_at,
        task_id: str,
        task_updated_at,
        lease_expires_at,
        reset_attempt: bool,
    ) -> KnowledgeFile | None:
        task_condition = (
            KnowledgeFile.processing_task_id.is_(None)
            if expected_task_id is None
            else KnowledgeFile.processing_task_id == expected_task_id
        )
        lease_condition = (
            KnowledgeFile.processing_task_lease_expires_at.is_(None)
            if expected_lease_expires_at is None
            else KnowledgeFile.processing_task_lease_expires_at == expected_lease_expires_at
        )
        async with pg_manager.get_async_session_context() as session:
            values = {
                "status": "indexed",
                "processing_stage": "replacement_cleanup",
                "processing_progress": 95,
                "processing_task_id": task_id,
                "processing_task_updated_at": task_updated_at,
                "processing_task_lease_expires_at": lease_expires_at,
                "error_message": None,
                "updated_at": utc_now_naive(),
            }
            if reset_attempt:
                values["processing_task_attempt"] = 0
            result = await session.execute(
                update(KnowledgeFile)
                .where(
                    KnowledgeFile.kb_id == kb_id,
                    KnowledgeFile.file_id == file_id,
                    KnowledgeFile.is_active.is_(True),
                    KnowledgeFile.previous_version_id.is_not(None),
                    task_condition,
                    lease_condition,
                )
                .values(**values)
                .returning(KnowledgeFile)
            )
            return result.scalar_one_or_none()

    @staticmethod
    def _next_available_filename(filename: str, existing_lower_names: set[str]) -> str:
        directory, leaf_name = os.path.split(filename.replace("\\", "/"))
        stem, extension = os.path.splitext(leaf_name)
        counter = 1
        while True:
            suffix = f" ({counter})"
            max_stem_length = max(1, 512 - len(directory) - len(extension) - len(suffix) - (1 if directory else 0))
            candidate_leaf = f"{stem[:max_stem_length]}{suffix}{extension}"
            candidate = f"{directory}/{candidate_leaf}" if directory else candidate_leaf
            if candidate.lower() not in existing_lower_names:
                return candidate
            counter += 1

    async def count_all(self) -> int:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(select(func.count()).select_from(KnowledgeFile))
            return int(result.scalar() or 0)

    async def list_file_ids_by_exact_statuses(
        self,
        *,
        kb_id: str,
        statuses: list[str],
        after_file_id: str | None = None,
        limit: int = 500,
    ) -> list[str]:
        normalized_statuses = [status for status in statuses if status]
        if not normalized_statuses:
            return []

        normalized_limit = min(max(int(limit or 100), 1), 500)
        filters = [
            KnowledgeFile.kb_id == kb_id,
            KnowledgeFile.is_folder.is_(False),
            KnowledgeFile.is_active.is_(True),
            KnowledgeFile.status.in_(normalized_statuses),
        ]
        if after_file_id:
            filters.append(KnowledgeFile.file_id > after_file_id)

        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(KnowledgeFile.file_id)
                .where(*filters)
                .order_by(KnowledgeFile.file_id.asc())
                .limit(normalized_limit)
            )
            return [str(file_id) for file_id in result.scalars().all()]

    async def exists_by_filename(self, *, kb_id: str, filename: str) -> bool:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(KnowledgeFile.file_id)
                .where(
                    KnowledgeFile.kb_id == kb_id,
                    func.lower(KnowledgeFile.filename) == filename.lower(),
                    KnowledgeFile.is_folder.is_not(True),
                    KnowledgeFile.is_active.is_(True),
                    or_(KnowledgeFile.status.is_(None), KnowledgeFile.status != "failed"),
                )
                .limit(1)
            )
            return result.scalar_one_or_none() is not None

    @staticmethod
    def _status_condition(status: str | None):
        if not status or status == "all":
            return None
        if status == "indexed":
            return KnowledgeFile.status.in_(["indexed", "done"])
        if status == "error_indexing":
            return KnowledgeFile.status.in_(["error_indexing", "failed"])
        return KnowledgeFile.status == status

    @staticmethod
    def _parent_condition(parent_id: str | None):
        if parent_id:
            return KnowledgeFile.parent_id == parent_id
        return KnowledgeFile.parent_id.is_(None)

    @staticmethod
    def _visible_version_condition():
        return or_(
            KnowledgeFile.is_active.is_(True),
            and_(
                KnowledgeFile.replacement_target_file_id.is_not(None),
                KnowledgeFile.previous_version_id.is_(None),
                KnowledgeFile.superseded_at.is_(None),
            ),
        )

    @staticmethod
    def _not_failed_replacement_candidate_condition():
        return or_(
            KnowledgeFile.is_active.is_(True),
            KnowledgeFile.replacement_target_file_id.is_(None),
            KnowledgeFile.status.is_(None),
            KnowledgeFile.status.notin_(FAILED_REPLACEMENT_CANDIDATE_STATUSES),
        )

    @staticmethod
    def _normalize_path_prefix(path_prefix: str | None) -> str:
        if not path_prefix:
            return ""
        normalized = path_prefix.strip().replace("\\", "/")
        while normalized.startswith("./"):
            normalized = normalized[2:]
        if normalized.startswith("/"):
            raise ValueError("path_prefix must be relative")

        parts = [part for part in normalized.split("/") if part and part != "."]
        if any(part == ".." for part in parts):
            raise ValueError("path_prefix must not contain parent directory references")
        if not parts:
            return ""
        return "/".join(parts) + "/"

    @staticmethod
    def _like_prefix(value: str) -> str:
        escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        return f"{escaped}%"

    def _document_filters(
        self,
        *,
        kb_id: str,
        parent_id: str | None,
        status: str | None,
        recursive: bool,
        files_only: bool,
    ) -> list:
        filters = [KnowledgeFile.kb_id == kb_id, self._visible_version_condition()]
        if not recursive:
            filters.append(self._parent_condition(parent_id))
        if files_only:
            filters.append(KnowledgeFile.is_folder.is_(False))

        status_condition = self._status_condition(status)
        if status_condition is not None:
            filters.append(KnowledgeFile.is_folder.is_(False))
            filters.append(status_condition)

        return filters

    async def _list_directory_documents(
        self,
        *,
        kb_id: str,
        parent_id: str | None,
        path_prefix: str,
        page: int,
        page_size: int,
        files_only: bool,
    ) -> tuple[list[Any], int]:
        offset = (page - 1) * page_size
        parent_condition = self._parent_condition(parent_id)
        base_filters = [
            KnowledgeFile.kb_id == kb_id,
            self._visible_version_condition(),
            parent_condition,
            KnowledgeFile.filename.is_not(None),
        ]
        if path_prefix:
            base_filters.append(KnowledgeFile.filename.like(self._like_prefix(path_prefix), escape="\\"))

        remainder = func.substr(KnowledgeFile.filename, len(path_prefix) + 1)
        immediate_name = remainder.label("filename")
        segment = func.split_part(remainder, "/", 1)
        virtual_path_prefix = (literal(path_prefix) + segment + literal("/")).label("path_prefix")
        virtual_file_id = (
            literal("__virtual_folder__:") + literal(parent_id or "root") + literal(":") + virtual_path_prefix
        ).label(
            "file_id",
        )

        real_select = select(
            KnowledgeFile.file_id.label("file_id"),
            immediate_name,
            KnowledgeFile.file_type.label("file_type"),
            KnowledgeFile.status.label("status"),
            KnowledgeFile.created_at.label("created_at"),
            KnowledgeFile.updated_at.label("updated_at"),
            KnowledgeFile.file_size.label("file_size"),
            KnowledgeFile.is_folder.label("is_folder"),
            KnowledgeFile.parent_id.label("parent_id"),
            KnowledgeFile.path.label("path"),
            KnowledgeFile.minio_url.label("minio_url"),
            KnowledgeFile.markdown_file.label("markdown_file"),
            KnowledgeFile.cleaning_draft_file.label("cleaning_draft_file"),
            KnowledgeFile.cleaning_version.label("cleaning_version"),
            KnowledgeFile.confirmed_at.label("confirmed_at"),
            KnowledgeFile.confirmed_by.label("confirmed_by"),
            KnowledgeFile.processing_stage.label("processing_stage"),
            KnowledgeFile.processing_progress.label("processing_progress"),
            KnowledgeFile.processing_task_id.label("processing_task_id"),
            KnowledgeFile.replacement_target_file_id.label("replacement_target_file_id"),
            KnowledgeFile.previous_version_id.label("previous_version_id"),
            KnowledgeFile.is_active.label("is_active"),
            KnowledgeFile.superseded_at.label("superseded_at"),
            KnowledgeFile.error_message.label("error_message"),
            literal(False).label("is_virtual_folder"),
            cast(literal(None), String).label("path_prefix"),
            literal(0).label("virtual_children_count"),
        ).where(*base_filters, remainder != "", func.strpos(remainder, "/") == 0)

        virtual_select = (
            select(
                virtual_file_id,
                segment.label("filename"),
                literal("folder").label("file_type"),
                literal("done").label("status"),
                cast(literal(None), DateTime).label("created_at"),
                cast(literal(None), DateTime).label("updated_at"),
                literal(0).label("file_size"),
                literal(True).label("is_folder"),
                cast(literal(parent_id), String).label("parent_id"),
                cast(literal(None), String).label("path"),
                cast(literal(None), String).label("minio_url"),
                cast(literal(None), String).label("markdown_file"),
                cast(literal(None), String).label("cleaning_draft_file"),
                literal(0).label("cleaning_version"),
                cast(literal(None), DateTime).label("confirmed_at"),
                cast(literal(None), String).label("confirmed_by"),
                cast(literal(None), String).label("processing_stage"),
                literal(0).label("processing_progress"),
                cast(literal(None), String).label("processing_task_id"),
                cast(literal(None), String).label("replacement_target_file_id"),
                cast(literal(None), String).label("previous_version_id"),
                literal(True).label("is_active"),
                cast(literal(None), DateTime).label("superseded_at"),
                cast(literal(None), String).label("error_message"),
                literal(True).label("is_virtual_folder"),
                virtual_path_prefix,
                func.count().label("virtual_children_count"),
            )
            .where(*base_filters, remainder != "", func.strpos(remainder, "/") > 0)
            .group_by(segment)
        )

        if files_only:
            directory_query = real_select.where(KnowledgeFile.is_folder.is_(False)).subquery()
        else:
            directory_query = union_all(real_select, virtual_select).subquery()

        async with pg_manager.get_async_session_context() as session:
            total_result = await session.execute(select(func.count()).select_from(directory_query))
            total = int(total_result.scalar_one() or 0)
            result = await session.execute(
                select(directory_query)
                .order_by(
                    directory_query.c.is_folder.desc(),
                    func.lower(directory_query.c.filename).asc(),
                    directory_query.c.created_at.desc().nullslast(),
                    directory_query.c.file_id.asc(),
                )
                .offset(offset)
                .limit(page_size)
            )
            return [SimpleNamespace(**dict(row)) for row in result.mappings().all()], total

    async def list_documents(
        self,
        *,
        kb_id: str,
        parent_id: str | None = None,
        path_prefix: str | None = None,
        status: str | None = None,
        page: int = 1,
        page_size: int = 100,
        recursive: bool = False,
        files_only: bool = False,
    ) -> tuple[list[KnowledgeFile], int]:
        page = max(int(page or 1), 1)
        page_size = min(max(int(page_size or 100), 1), 500)
        offset = (page - 1) * page_size
        normalized_path_prefix = self._normalize_path_prefix(path_prefix)
        has_status_filter = self._status_condition(status) is not None
        effective_recursive = recursive and has_status_filter
        if not effective_recursive and not has_status_filter:
            return await self._list_directory_documents(
                kb_id=kb_id,
                parent_id=parent_id,
                path_prefix=normalized_path_prefix,
                page=page,
                page_size=page_size,
                files_only=files_only,
            )

        filters = self._document_filters(
            kb_id=kb_id,
            parent_id=parent_id,
            status=status,
            recursive=effective_recursive,
            files_only=files_only,
        )

        async with pg_manager.get_async_session_context() as session:
            total_result = await session.execute(select(func.count()).select_from(KnowledgeFile).where(*filters))
            total = int(total_result.scalar_one() or 0)

            result = await session.execute(
                select(KnowledgeFile)
                .where(*filters)
                .order_by(
                    KnowledgeFile.is_folder.desc(),
                    func.lower(KnowledgeFile.filename).asc(),
                    KnowledgeFile.created_at.desc(),
                    KnowledgeFile.file_id.asc(),
                )
                .offset(offset)
                .limit(page_size)
            )
            return list(result.scalars().all()), total

    async def count_children_by_parent_ids(self, *, kb_id: str, parent_ids: list[str]) -> dict[str, int]:
        if not parent_ids:
            return {}

        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(KnowledgeFile.parent_id, func.count())
                .where(
                    KnowledgeFile.kb_id == kb_id,
                    KnowledgeFile.is_active.is_(True),
                    KnowledgeFile.parent_id.in_(parent_ids),
                )
                .group_by(KnowledgeFile.parent_id)
            )
            return {str(parent_id): int(count or 0) for parent_id, count in result.all() if parent_id}

    async def get_kb_file_stats(self, kb_id: str) -> dict[str, int]:
        non_folder = KnowledgeFile.is_folder.is_(False)
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(
                    func.count(KnowledgeFile.file_id).label("row_count"),
                    func.sum(case((non_folder, 1), else_=0)).label("file_count"),
                    func.sum(case((KnowledgeFile.is_folder.is_(True), 1), else_=0)).label("folder_count"),
                    func.coalesce(func.sum(case((non_folder, KnowledgeFile.file_size), else_=0)), 0).label(
                        "total_size"
                    ),
                    func.coalesce(func.sum(case((non_folder, KnowledgeFile.chunk_count), else_=0)), 0).label(
                        "chunk_count"
                    ),
                    func.coalesce(func.sum(case((non_folder, KnowledgeFile.token_count), else_=0)), 0).label(
                        "token_count"
                    ),
                    func.sum(case((non_folder & (KnowledgeFile.status == "uploaded"), 1), else_=0)).label(
                        "pending_parse_count"
                    ),
                    func.sum(
                        case((non_folder & KnowledgeFile.status.in_(["parsed", "error_indexing"]), 1), else_=0)
                    ).label("pending_index_count"),
                    func.sum(
                        case(
                            (
                                non_folder
                                & KnowledgeFile.status.in_(
                                    ["processing", "waiting", "parsing", "cleaning", "indexing"]
                                ),
                                1,
                            ),
                            else_=0,
                        )
                    ).label("processing_count"),
                ).where(KnowledgeFile.kb_id == kb_id, KnowledgeFile.is_active.is_(True))
            )
            row = result.one()

        return {
            "row_count": int(row.row_count or 0),
            "file_count": int(row.file_count or 0),
            "folder_count": int(row.folder_count or 0),
            "total_size": int(row.total_size or 0),
            "chunk_count": int(row.chunk_count or 0),
            "token_count": int(row.token_count or 0),
            "pending_parse_count": int(row.pending_parse_count or 0),
            "pending_index_count": int(row.pending_index_count or 0),
            "processing_count": int(row.processing_count or 0),
        }

    async def upsert(self, file_id: str, data: dict[str, Any]) -> KnowledgeFile:
        sanitized_data = self._sanitize_data(data)
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(select(KnowledgeFile).where(KnowledgeFile.file_id == file_id))
            existing = result.scalar_one_or_none()
            if existing is None:
                record = KnowledgeFile(file_id=file_id, **sanitized_data)
                session.add(record)
                return record
            for key, value in sanitized_data.items():
                setattr(existing, key, value)
            return existing

    async def update_fields(
        self,
        *,
        file_id: str,
        data: dict[str, Any],
        kb_id: str | None = None,
    ) -> KnowledgeFile | None:
        sanitized_data = self._sanitize_data(data)
        if not sanitized_data:
            return await self.get_by_file_id(file_id)

        filters = [KnowledgeFile.file_id == file_id]
        if kb_id:
            filters.append(KnowledgeFile.kb_id == kb_id)

        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(select(KnowledgeFile).where(*filters))
            record = result.scalar_one_or_none()
            if record is None:
                return None
            for key, value in sanitized_data.items():
                setattr(record, key, value)
            return record

    async def update_fields_if_status(
        self,
        *,
        kb_id: str,
        file_id: str,
        allowed_statuses: set[str],
        data: dict[str, Any],
    ) -> KnowledgeFile | None:
        sanitized_data = self._sanitize_data(data)
        if not sanitized_data:
            return await self.get_by_file_id(file_id)

        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                update(KnowledgeFile)
                .where(
                    KnowledgeFile.kb_id == kb_id,
                    KnowledgeFile.file_id == file_id,
                    KnowledgeFile.status.in_(sorted(allowed_statuses)),
                )
                .values(**sanitized_data)
                .returning(KnowledgeFile)
            )
            return result.scalar_one_or_none()

    async def update_cleaning_fields_with_version(
        self,
        *,
        kb_id: str,
        file_id: str,
        expected_version: int,
        data: dict[str, Any],
        increment_version: bool,
        allowed_statuses: set[str] | None = None,
    ) -> KnowledgeFile | None:
        """Conditionally update a cleaning draft to prevent lost concurrent edits."""
        sanitized_data = self._sanitize_data(data)
        if increment_version:
            sanitized_data.pop("cleaning_version", None)
            sanitized_data["cleaning_version"] = KnowledgeFile.cleaning_version + 1
        filters = [
            KnowledgeFile.kb_id == kb_id,
            KnowledgeFile.file_id == file_id,
            KnowledgeFile.cleaning_version == max(0, int(expected_version)),
        ]
        if allowed_statuses:
            filters.append(KnowledgeFile.status.in_(sorted(allowed_statuses)))
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                update(KnowledgeFile).where(*filters).values(**sanitized_data).returning(KnowledgeFile)
            )
            return result.scalar_one_or_none()

    async def update_enrichment_fields_with_version(
        self,
        *,
        kb_id: str,
        file_id: str,
        expected_version: int,
        expected_cleaning_version: int,
        data: dict[str, Any],
        increment_version: bool,
        require_active: bool = True,
    ) -> KnowledgeFile | None:
        """Conditionally update enrichment without overwriting a newer body or edit."""
        sanitized_data = self._sanitize_data(data)
        if increment_version:
            sanitized_data.pop("enrichment_version", None)
            sanitized_data["enrichment_version"] = KnowledgeFile.enrichment_version + 1
        filters = [
            KnowledgeFile.kb_id == kb_id,
            KnowledgeFile.file_id == file_id,
            KnowledgeFile.enrichment_version == max(0, int(expected_version)),
            KnowledgeFile.cleaning_version == max(0, int(expected_cleaning_version)),
        ]
        if require_active:
            filters.append(KnowledgeFile.is_active.is_(True))
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                update(KnowledgeFile).where(*filters).values(**sanitized_data).returning(KnowledgeFile)
            )
            return result.scalar_one_or_none()

    async def find_successor_version(self, *, kb_id: str, previous_version_id: str) -> KnowledgeFile | None:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(KnowledgeFile)
                .where(
                    KnowledgeFile.kb_id == kb_id,
                    KnowledgeFile.previous_version_id == previous_version_id,
                )
                .order_by(KnowledgeFile.created_at.desc(), KnowledgeFile.file_id.asc())
                .limit(1)
            )
            return result.scalar_one_or_none()

    async def create_cleaning_replacement_candidate(
        self,
        *,
        file_id: str,
        kb_id: str,
        target_file_id: str,
        data: dict[str, Any],
        target_restore_data: dict[str, Any] | None = None,
    ) -> tuple[KnowledgeFile, bool]:
        """Create one inactive cleaning candidate under the replacement-target lock."""
        sanitized_data = self._sanitize_data(data)
        async with pg_manager.get_async_session_context() as session:
            lock_key = stable_advisory_lock_key(
                "knowledge-file-replacement-target",
                f"{kb_id}\0{target_file_id}",
            )
            await session.execute(text("SELECT pg_advisory_xact_lock(:lock_key)"), {"lock_key": lock_key})
            target = (
                await session.execute(
                    select(KnowledgeFile)
                    .where(
                        KnowledgeFile.kb_id == kb_id,
                        KnowledgeFile.file_id == target_file_id,
                        KnowledgeFile.is_folder.is_(False),
                    )
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if target is None:
                raise ValueError("Document version not found")
            if not target.is_active:
                successor = (
                    await session.execute(
                        select(KnowledgeFile)
                        .where(
                            KnowledgeFile.kb_id == kb_id,
                            KnowledgeFile.previous_version_id == target_file_id,
                        )
                        .order_by(KnowledgeFile.created_at.desc())
                        .limit(1)
                    )
                ).scalar_one_or_none()
                if successor is not None:
                    return successor, False
                raise ValueError("Document version is no longer active")

            existing = (
                await session.execute(
                    select(KnowledgeFile)
                    .where(
                        KnowledgeFile.kb_id == kb_id,
                        KnowledgeFile.replacement_target_file_id == target_file_id,
                        KnowledgeFile.is_active.is_(False),
                        or_(
                            KnowledgeFile.status.is_(None),
                            KnowledgeFile.status.notin_(FAILED_REPLACEMENT_CANDIDATE_STATUSES),
                        ),
                    )
                    .order_by(KnowledgeFile.created_at.asc(), KnowledgeFile.file_id.asc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if existing is not None:
                return existing, False

            sanitized_data.update(
                {
                    "kb_id": kb_id,
                    "replacement_target_file_id": target_file_id,
                    "previous_version_id": None,
                    "is_active": False,
                    "superseded_at": None,
                }
            )
            record = KnowledgeFile(file_id=file_id, **sanitized_data)
            session.add(record)
            if target_restore_data:
                for key, value in self._sanitize_data(target_restore_data).items():
                    setattr(target, key, value)
            await session.flush()
            return record, True

    async def delete(self, file_id: str) -> None:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(select(KnowledgeFile).where(KnowledgeFile.file_id == file_id))
            record = result.scalar_one_or_none()
            if record is not None:
                await session.delete(record)

    async def delete_by_kb_id(self, kb_id: str) -> None:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(select(KnowledgeFile).where(KnowledgeFile.kb_id == kb_id))
            for record in result.scalars().all():
                await session.delete(record)
