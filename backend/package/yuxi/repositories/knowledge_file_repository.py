from __future__ import annotations

import hashlib
import os
import re
import unicodedata
from collections.abc import Iterator
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from sqlalchemy import (
    ARRAY,
    DateTime,
    String,
    and_,
    case,
    cast,
    func,
    lateral,
    literal,
    or_,
    select,
    text,
    union_all,
    update,
)
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_knowledge import KnowledgeChunk, KnowledgeFile
from yuxi.storage.postgres.models_business import User
from yuxi.utils.datetime_utils import utc_now_naive

# 替换候选失败状态：这些状态的候选不再占用目标，允许再次 replace
FAILED_REPLACEMENT_CANDIDATE_STATUSES = {
    "cancelled",
    "canceled",
    "parse_failed",
    "index_failed",
    "error_parsing",
    "error_indexing",
}


class FolderNameConflictError(ValueError):
    pass


class InvalidFolderNameError(ValueError):
    pass


class ParentFolderNotFoundError(ValueError):
    pass


class ParentIsNotFolderError(ValueError):
    pass


def normalize_folder_name(folder_name: str) -> str:
    normalized_name = unicodedata.normalize("NFKC", str(folder_name or "")).strip()
    if not normalized_name:
        raise InvalidFolderNameError("Folder name must not be empty")
    if normalized_name in {".", ".."} or "/" in normalized_name or "\\" in normalized_name:
        raise InvalidFolderNameError("Folder name contains invalid characters")
    if any(ord(character) < 32 or ord(character) == 127 for character in normalized_name):
        raise InvalidFolderNameError("Folder name contains invalid characters")
    return normalized_name


def normalize_document_filename(filename: str) -> str:
    return unicodedata.normalize("NFKC", str(filename or "")).strip().replace("\\", "/").casefold()


def normalize_document_base_name(filename: str) -> str:
    """提取去版本号的基础名，用于"同一逻辑文档不同版本"的自动预判。

    规则：剥掉文件名主名（不含扩展名）末尾的版本号后缀，再归一化。
    例如 sglang-v1.1.docx -> sglang、sglang_v2 -> sglang、report-2024 -> report、
    测试1 -> 测试、测试2 -> 测试（"测试1/测试2"是版本关系，自动预判为同一逻辑文档的不同版本）。
    支持的版本后缀形态：`v1.1`、`-1.1`、`_v2`、`_2`、`-2024`、纯数字结尾（测试1）等。
    """
    stem = str(filename or "").strip().rsplit(".", 1)[0] if "." in str(filename or "") else str(filename or "")
    base = re.sub(r"[-_.]?v?\d+([.-]\d+)*$", "", stem, flags=re.IGNORECASE)
    return normalize_document_filename(base) if base else normalize_document_filename(stem)


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


# asyncpg 单条 SQL 参数上限为 32767；按 file_id 批量查询时统一分批，避免
# mindmap_file_ids 等大尺寸传入触发 `too many parameters` 报错。
SQL_IN_BATCH_SIZE = 10_000


class KnowledgeFileRepository:
    _writable_fields = {
        "kb_id",
        "parent_id",
        "logical_document_id",
        "document_version",
        "is_current",
        "supersedes_file_id",
        "activated_at",
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
        "is_folder",
        "error_message",
        "created_by",
        "updated_by",
        "normalized_name",
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
        "enrichment_data",
        "enrichment_status",
        "enrichment_version",
        "enrichment_content_hash",
        "enrichment_generated_at",
        "enrichment_error",
        "enrichment_possibly_outdated",
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

    async def list_current_file_ids(self, file_ids: list[str]) -> set[str]:
        normalized_ids = [file_id for file_id in file_ids if file_id]
        if not normalized_ids:
            return set()

        current_ids: set[str] = set()
        async with pg_manager.get_async_session_context() as session:
            for batch in self._iter_batches(normalized_ids):
                result = await session.execute(
                    select(KnowledgeFile.file_id).where(
                        KnowledgeFile.file_id.in_(batch),
                        KnowledgeFile.is_current.is_(True),
                    )
                )
                current_ids.update(str(file_id) for file_id in result.scalars().all())
        return current_ids

    async def list_versions(self, *, kb_id: str, file_id: str) -> list[KnowledgeFile]:
        async with pg_manager.get_async_session_context() as session:
            logical_document_id = await session.scalar(
                select(KnowledgeFile.logical_document_id).where(
                    KnowledgeFile.kb_id == kb_id,
                    KnowledgeFile.file_id == file_id,
                )
            )
            if logical_document_id:
                result = await session.execute(
                    select(KnowledgeFile)
                    .where(
                        KnowledgeFile.kb_id == kb_id,
                        KnowledgeFile.logical_document_id == logical_document_id,
                    )
                    .order_by(KnowledgeFile.document_version.desc(), KnowledgeFile.created_at.desc())
                )
            else:
                # 存量数据兼容：旧版自身可能缺失 logical_document_id（锚点断裂），
                # 按 自身 file_id / 后代 logical_document_id / 指向自身的 supersedes 关联拉全版本链，
                # 使历史候选与验证报告无需数据迁移即可恢复可见。
                result = await session.execute(
                    select(KnowledgeFile)
                    .where(
                        KnowledgeFile.kb_id == kb_id,
                        or_(
                            KnowledgeFile.file_id == file_id,
                            KnowledgeFile.logical_document_id == file_id,
                            KnowledgeFile.supersedes_file_id == file_id,
                        ),
                    )
                    .order_by(KnowledgeFile.document_version.desc(), KnowledgeFile.created_at.desc())
                )
            return list(result.scalars().all())

    async def list_version_chains_for_current_files(
        self,
        *,
        kb_id: str,
        file_ids: list[str],
    ) -> dict[str, list[KnowledgeFile]]:
        """批量读取当前文件及其已生效历史版本，避免来源卡片逐文件查询。"""
        normalized_ids = list(dict.fromkeys(file_id for file_id in file_ids if file_id))
        if not normalized_ids:
            return {}

        async with pg_manager.get_async_session_context() as session:
            current_result = await session.execute(
                select(KnowledgeFile).where(
                    KnowledgeFile.kb_id == kb_id,
                    KnowledgeFile.file_id.in_(normalized_ids),
                    KnowledgeFile.is_current.is_(True),
                    KnowledgeFile.is_active.is_(True),
                    KnowledgeFile.is_folder.is_(False),
                )
            )
            current_records = list(current_result.scalars().all())
            chains = {record.file_id: [record] for record in current_records}

            replacement_currents = [record for record in current_records if record.previous_version_id]
            if replacement_currents:
                replacement_chain = (
                    select(
                        KnowledgeFile.file_id.label("source_file_id"),
                        KnowledgeFile.previous_version_id.label("file_id"),
                    )
                    .where(
                        KnowledgeFile.kb_id == kb_id,
                        KnowledgeFile.file_id.in_([record.file_id for record in replacement_currents]),
                    )
                    .cte("knowledge_file_replacement_chain", recursive=True)
                )
                replacement_chain = replacement_chain.union_all(
                    select(
                        replacement_chain.c.source_file_id,
                        KnowledgeFile.previous_version_id.label("file_id"),
                    ).join(
                        KnowledgeFile,
                        and_(
                            KnowledgeFile.kb_id == kb_id,
                            KnowledgeFile.file_id == replacement_chain.c.file_id,
                        ),
                    )
                )
                replacement_result = await session.execute(
                    select(replacement_chain.c.source_file_id, KnowledgeFile).join(
                        KnowledgeFile,
                        and_(
                            KnowledgeFile.kb_id == kb_id,
                            KnowledgeFile.file_id == replacement_chain.c.file_id,
                        ),
                    )
                )
                for source_file_id, record in replacement_result.all():
                    chains[str(source_file_id)].append(record)

            logical_currents = [
                record
                for record in current_records
                if not record.previous_version_id and record.logical_document_id
            ]
            logical_ids = {record.logical_document_id for record in logical_currents}
            if logical_ids:
                logical_result = await session.execute(
                    select(KnowledgeFile).where(
                        KnowledgeFile.kb_id == kb_id,
                        KnowledgeFile.logical_document_id.in_(logical_ids),
                        KnowledgeFile.activated_at.is_not(None),
                        KnowledgeFile.is_folder.is_(False),
                    )
                )
                records_by_logical_id: dict[str, list[KnowledgeFile]] = {}
                for record in logical_result.scalars().all():
                    records_by_logical_id.setdefault(str(record.logical_document_id), []).append(record)
                for current in logical_currents:
                    seen = {record.file_id for record in chains[current.file_id]}
                    chains[current.file_id].extend(
                        record
                        for record in records_by_logical_id.get(str(current.logical_document_id), [])
                        if record.file_id not in seen
                    )

            return chains

    async def create_candidate_version(
        self,
        *,
        kb_id: str,
        current_file_id: str,
        data: dict[str, Any],
        session: AsyncSession,
    ) -> KnowledgeFile:
        result = await session.execute(
            select(KnowledgeFile)
            .where(KnowledgeFile.kb_id == kb_id, KnowledgeFile.file_id == current_file_id)
            .with_for_update()
        )
        current = result.scalar_one_or_none()
        if current is None:
            raise ValueError("当前文档不存在")
        if current.is_folder:
            raise ValueError("文件夹不能创建文档版本")
        if not current.is_current:
            raise ValueError("VERSION_CHANGED")

        logical_document_id = current.logical_document_id or current.file_id
        # 首版升级时补齐版本链锚点：旧版自身缺失 logical_document_id 会导致
        # list_versions 按锚点查不到任何版本（候选/历史/验证报告全部不可见）。
        # 仅当 None 时回填为旧版自身 file_id，已是链中间节点则保持原锚点不变。
        if getattr(current, "logical_document_id", None) is None:
            current.logical_document_id = logical_document_id
            if getattr(current, "document_version", None) is None:
                current.document_version = 1
        existing_candidate = await session.scalar(
            select(KnowledgeFile.file_id)
            .where(
                KnowledgeFile.kb_id == kb_id,
                KnowledgeFile.logical_document_id == logical_document_id,
                KnowledgeFile.supersedes_file_id == current.file_id,
                KnowledgeFile.is_current.is_(False),
                KnowledgeFile.status.in_(
                    [
                        "uploaded",
                        "parsing",
                        "parsed",
                        "indexing",
                        "indexed",
                        "done",
                        "validation_processing",
                        "validation_accepted",
                        "validation_review",
                        "conflict_detecting",
                        "conflict_clear",
                        "conflict_review",
                        "conflict_inconclusive",
                        "conflict_detection_failed",
                    ]
                ),
            )
            .limit(1)
        )
        if existing_candidate:
            raise ValueError("UPDATE_IN_PROGRESS")

        latest_version = await session.scalar(
            select(func.max(KnowledgeFile.document_version)).where(
                KnowledgeFile.kb_id == kb_id,
                KnowledgeFile.logical_document_id == logical_document_id,
            )
        )
        raw_candidate_data = {
            **data,
            "kb_id": kb_id,
            "logical_document_id": logical_document_id,
            "document_version": int(latest_version or 1) + 1,
            "is_current": False,
            "supersedes_file_id": current.file_id,
            "activated_at": None,
        }
        candidate_data = self._sanitize_data(raw_candidate_data)
        candidate = KnowledgeFile(file_id=str(data["file_id"]), **candidate_data)
        session.add(candidate)
        await session.flush()
        return candidate

    async def activate_candidate(
        self,
        *,
        kb_id: str,
        candidate_file_id: str,
        expected_current_file_id: str,
        operator_id: str,
        session: AsyncSession,
    ) -> tuple[KnowledgeFile, KnowledgeFile]:
        candidate_result = await session.execute(
            select(KnowledgeFile)
            .where(KnowledgeFile.kb_id == kb_id, KnowledgeFile.file_id == candidate_file_id)
            .with_for_update()
        )
        candidate = candidate_result.scalar_one_or_none()
        if candidate is None or not candidate.logical_document_id:
            raise ValueError("候选版本不存在")
        if candidate.is_current:
            raise ValueError("候选版本已经生效")
        if candidate.status not in {"conflict_clear", "conflict_review", "validation_accepted", "validation_review"}:
            raise ValueError("候选版本尚未完成知识变更分析")

        versions_result = await session.execute(
            select(KnowledgeFile)
            .where(
                KnowledgeFile.kb_id == kb_id,
                or_(
                    KnowledgeFile.logical_document_id == candidate.logical_document_id,
                    KnowledgeFile.file_id == expected_current_file_id,
                ),
            )
            .with_for_update()
        )
        versions = list(versions_result.scalars().all())
        current = next((version for version in versions if version.is_current), None)
        if current is None or current.file_id != expected_current_file_id:
            raise ValueError("VERSION_CHANGED")
        if candidate.supersedes_file_id != current.file_id:
            raise ValueError("VERSION_CHANGED")
        # 存量数据兼容：旧当前文件可能缺失 logical_document_id（锚点断裂），
        # 激活时补齐，使激活后 list_versions 仍能按锚点拉全版本链
        if getattr(current, "logical_document_id", None) is None:
            current.logical_document_id = candidate.logical_document_id
            if getattr(current, "document_version", None) is None:
                current.document_version = 1

        now = utc_now_naive()
        # 归档旧版：与替换流程 switch_active_version 一致，旧版不再参与检索/图谱/统计，
        # 否则会出现 is_current=false 但 is_active=true 的半归档记录（同 Bug2 孤儿问题同构）
        current.is_current = False
        current.is_active = False
        current.superseded_at = now
        current.updated_by = operator_id
        current.updated_at = now
        candidate.is_current = True
        candidate.status = "done"
        candidate.error_message = None
        candidate.activated_at = now
        candidate.updated_by = operator_id
        candidate.updated_at = now
        await session.flush()
        return current, candidate

    async def list_by_kb_id(self, kb_id: str) -> list[KnowledgeFile]:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(KnowledgeFile).where(
                    KnowledgeFile.kb_id == kb_id,
                    KnowledgeFile.is_current.is_(True),
                )
            )
            return list(result.scalars().all())

    async def search_documents(
        self,
        *,
        kb_ids: list[str],
        keyword: str | None = None,
        search_type: str = "filename",
        updated_from=None,
        updated_to=None,
        created_by: str | None = None,
        page: int = 1,
        page_size: int = 30,
    ) -> tuple[list[dict], int]:
        if not kb_ids:
            return [], 0
        # 搜索方式：filename=只按文件名（路径末段）匹配；folder=按文件夹名匹配返回文件夹；content=按正文匹配
        if search_type not in {"filename", "folder", "content"}:
            search_type = "filename"
        if search_type == "folder":
            return await self._search_folders(
                kb_ids=kb_ids,
                keyword=(keyword or "").strip(),
                updated_from=updated_from,
                updated_to=updated_to,
                created_by=(created_by or "").strip(),
                page=max(page, 1),
                page_size=min(max(page_size, 1), 100),
            )
        filters = [
            KnowledgeFile.kb_id.in_(kb_ids),
            KnowledgeFile.is_current.is_(True),
        ]
        if keyword and keyword.strip():
            escaped = keyword.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            pattern = f"%{escaped.lower()}%"
            if search_type == "content":
                filters.append(func.lower(KnowledgeChunk.content).like(pattern, escape="\\"))
            else:
                filters.append(
                    or_(
                        # filename 可能是完整相对路径（如 目录/子目录/xxx.pdf），只匹配最后一段
                        # （文件名本身），避免命中祖先目录路径前缀导致整目录文件被带出。
                        func.lower(func.regexp_replace(KnowledgeFile.filename, "^.*/", "")).like(
                            pattern, escape="\\"
                        ),
                        func.lower(func.coalesce(KnowledgeFile.original_filename, "")).like(pattern, escape="\\"),
                    )
                )
        if updated_from:
            filters.append(KnowledgeFile.updated_at >= updated_from)
        if updated_to:
            filters.append(KnowledgeFile.updated_at <= updated_to)
        if created_by and created_by.strip():
            filters.append(KnowledgeFile.created_by == created_by.strip())

        statement = select(
            KnowledgeFile.file_id,
            KnowledgeFile.kb_id,
            KnowledgeFile.filename,
            KnowledgeFile.file_type,
            KnowledgeFile.status,
            KnowledgeFile.is_folder,
            KnowledgeFile.created_by,
            KnowledgeFile.updated_at,
            KnowledgeFile.created_at,
            KnowledgeFile.view_count,
            User.username.label("publisher_name"),
        ).outerjoin(User, User.uid == KnowledgeFile.created_by)
        if search_type == "content":
            # 正文匹配需要 join chunk 并去重（一个文件多个 chunk），其余模式单文件单行无需分组
            statement = statement.outerjoin(
                KnowledgeChunk, KnowledgeChunk.file_id == KnowledgeFile.file_id
            ).group_by(
                KnowledgeFile.file_id,
                KnowledgeFile.kb_id,
                KnowledgeFile.filename,
                KnowledgeFile.file_type,
                KnowledgeFile.status,
                KnowledgeFile.is_folder,
                KnowledgeFile.created_by,
                KnowledgeFile.updated_at,
                KnowledgeFile.created_at,
                KnowledgeFile.view_count,
                User.username,
            )
        statement = statement.where(*filters)
        normalized_page_size = min(max(page_size, 1), 100)
        async with pg_manager.get_async_session_context() as session:
            total = int((await session.execute(select(func.count()).select_from(statement.subquery()))).scalar_one())
            result = await session.execute(
                statement.order_by(KnowledgeFile.updated_at.desc(), KnowledgeFile.file_id.asc())
                .offset((max(page, 1) - 1) * normalized_page_size)
                .limit(normalized_page_size)
            )
            return [dict(row) for row in result.mappings().all()], total

    async def _search_folders(
        self,
        *,
        kb_ids: list[str],
        keyword: str,
        updated_from=None,
        updated_to=None,
        created_by: str = "",
        page: int = 1,
        page_size: int = 30,
    ) -> tuple[list[dict], int]:
        """文件夹搜索：按文件夹名匹配，返回去重后的文件夹（真实文件夹 + 路径派生虚拟目录）。

        虚拟目录从文件路径的全部祖先段派生：文件 `a/b/c.pdf` 隐含目录 `a`、`a/b`，
        每个目录用其名字（末段）参与匹配；目录路径去重返回一次，避免一个目录把其中全部文件带出。
        真实文件夹（is_folder）的 filename 即文件夹名，按自身名匹配。
        """
        base_filters = [
            KnowledgeFile.kb_id.in_(kb_ids),
            KnowledgeFile.is_current.is_(True),
        ]
        if updated_from:
            base_filters.append(KnowledgeFile.updated_at >= updated_from)
        if updated_to:
            base_filters.append(KnowledgeFile.updated_at <= updated_to)
        if created_by:
            base_filters.append(KnowledgeFile.created_by == created_by)

        segs = cast(
            func.string_to_array(func.regexp_replace(KnowledgeFile.filename, "/[^/]*$", ""), "/"), ARRAY(String)
        )
        path_base = (
            select(
                KnowledgeFile.kb_id.label("kb_id"),
                segs.label("segs"),
                KnowledgeFile.updated_at.label("updated_at"),
                KnowledgeFile.created_at.label("created_at"),
                KnowledgeFile.created_by.label("created_by"),
                KnowledgeFile.view_count.label("view_count"),
            )
            .where(*base_filters, KnowledgeFile.filename.like("%/%"))
            .subquery()
        )
        # 对每个文件的目录段序列，按深度 1..cardinality 横向展开，得到全部祖先目录
        depth = func.generate_series(literal(1), func.cardinality(path_base.c.segs)).label("depth")
        derived = lateral(select(depth))
        dir_path = func.array_to_string(path_base.c.segs[1 : derived.c.depth], "/")
        folder_name = path_base.c.segs[derived.c.depth]
        virtual_select = (
            select(
                (literal("__virtual_folder__:") + path_base.c.kb_id + literal(":") + dir_path).label("file_id"),
                path_base.c.kb_id.label("kb_id"),
                dir_path.label("filename"),
                literal("folder").label("file_type"),
                literal("done").label("status"),
                literal(True).label("is_folder"),
                literal(True).label("is_virtual_folder"),
                func.count().label("virtual_children_count"),
                func.max(path_base.c.updated_at).label("updated_at"),
                func.max(path_base.c.created_at).label("created_at"),
                func.max(path_base.c.created_by).label("created_by"),
                func.max(path_base.c.view_count).label("view_count"),
            )
            .select_from(path_base, derived)
            .group_by(path_base.c.kb_id, dir_path)
        )

        real_select = (
            select(
                KnowledgeFile.file_id,
                KnowledgeFile.kb_id,
                KnowledgeFile.filename,
                KnowledgeFile.file_type,
                KnowledgeFile.status,
                KnowledgeFile.is_folder,
                literal(False).label("is_virtual_folder"),
                literal(0).label("virtual_children_count"),
                KnowledgeFile.updated_at,
                KnowledgeFile.created_at,
                KnowledgeFile.created_by,
                KnowledgeFile.view_count,
            )
            .where(KnowledgeFile.is_folder.is_(True), *base_filters)
        )

        if keyword:
            escaped = keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            pattern = f"%{escaped.lower()}%"
            virtual_select = virtual_select.where(func.lower(folder_name).like(pattern, escape="\\"))
            real_select = real_select.where(
                func.lower(func.coalesce(KnowledgeFile.filename, "")).like(pattern, escape="\\")
            )

        folders = union_all(real_select, virtual_select).subquery()
        async with pg_manager.get_async_session_context() as session:
            total = int((await session.execute(select(func.count()).select_from(folders))).scalar_one())
            result = await session.execute(
                select(folders)
                .order_by(func.lower(folders.c.filename).asc(), folders.c.file_id.asc())
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
            return [dict(row) for row in result.mappings().all()], total

    async def list_hot_documents(self, *, kb_ids: list[str], limit: int = 10) -> list[dict]:
        if not kb_ids:
            return []
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(
                    KnowledgeFile.file_id,
                    KnowledgeFile.kb_id,
                    KnowledgeFile.filename,
                    KnowledgeFile.created_by,
                    KnowledgeFile.updated_at,
                    KnowledgeFile.view_count,
                    User.username.label("publisher_name"),
                )
                .outerjoin(User, User.uid == KnowledgeFile.created_by)
                .where(
                    KnowledgeFile.kb_id.in_(kb_ids),
                    KnowledgeFile.is_folder.is_(False),
                    KnowledgeFile.is_current.is_(True),
                )
                .order_by(KnowledgeFile.view_count.desc(), KnowledgeFile.updated_at.desc(), KnowledgeFile.file_id.asc())
                .limit(min(max(limit, 1), 30))
            )
            return [dict(row) for row in result.mappings().all()]

    async def increment_view_count(self, file_id: str) -> None:
        async with pg_manager.get_async_session_context() as session:
            await session.execute(
                update(KnowledgeFile)
                .where(KnowledgeFile.file_id == file_id, KnowledgeFile.is_folder.is_(False))
                .values(view_count=KnowledgeFile.view_count + 1)
            )
            await session.commit()

    async def list_by_kb_id_after(
        self,
        kb_id: str,
        *,
        after_file_id: str | None = None,
        limit: int = 500,
        files_only: bool = False,
    ) -> list[KnowledgeFile]:
        filters = [KnowledgeFile.kb_id == kb_id, KnowledgeFile.is_current.is_(True)]
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

    async def list_children(self, *, kb_id: str, parent_id: str | None) -> list[KnowledgeFile]:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(KnowledgeFile)
                .where(
                    KnowledgeFile.kb_id == kb_id,
                    KnowledgeFile.is_current.is_(True),
                    self._parent_condition(parent_id),
                )
                .order_by(KnowledgeFile.is_folder.desc(), func.lower(KnowledgeFile.filename).asc())
            )
            return list(result.scalars().all())

    async def list_same_name_files(
        self,
        *,
        kb_id: str,
        parent_id: str | None,
        filename: str,
    ) -> list[KnowledgeFile]:
        normalized_filename = normalize_document_filename(filename)
        if not normalized_filename:
            return []

        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(KnowledgeFile)
                .where(
                    KnowledgeFile.kb_id == kb_id,
                    KnowledgeFile.is_folder.is_(False),
                    self._parent_condition(parent_id),
                    KnowledgeFile.is_current.is_(True),
                    or_(
                        KnowledgeFile.normalized_name == normalized_filename,
                        and_(
                            KnowledgeFile.normalized_name.is_(None),
                            func.lower(func.trim(KnowledgeFile.filename)) == normalized_filename,
                        ),
                        func.lower(KnowledgeFile.filename) == normalized_filename.lower(),
                    ),
                )
                .order_by(KnowledgeFile.created_at.desc(), KnowledgeFile.file_id.asc())
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
                    KnowledgeFile.is_current.is_(True),
                    KnowledgeFile.content_hash == normalized_hash,
                    self._not_failed_replacement_candidate_condition(),
                )
                .order_by(KnowledgeFile.created_at.desc(), KnowledgeFile.file_id.asc())
            )
            return list(result.scalars().all())

    async def list_version_candidate_files(
        self,
        *,
        kb_id: str,
        parent_id: str | None,
        filename: str,
    ) -> list[KnowledgeFile]:
        """按"去版本号基础名"匹配可能的同文档其他版本，用于上传时自动预判版本候选。

        与 list_same_name_files（精确同名，用于重复检测）不同，这里忽略版本号后缀，
        例如上传 sglang-v1.1.docx 时返回基础名同为 sglang 的 sglang-v1.0.docx（当前版本）。
        只匹配正式当前版本（is_current=True），失败的候选不会被当作目标。
        """
        base_name = normalize_document_base_name(filename)
        if not base_name:
            return []

        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(KnowledgeFile)
                .where(
                    KnowledgeFile.kb_id == kb_id,
                    KnowledgeFile.is_folder.is_(False),
                    self._parent_condition(parent_id),
                    KnowledgeFile.is_active.is_(True),
                    KnowledgeFile.is_current.is_(True),
                    KnowledgeFile.status.in_(["indexed", "done", "parsed", "error_indexing"]),
                )
                .order_by(KnowledgeFile.created_at.desc(), KnowledgeFile.file_id.asc())
            )
            records = list(result.scalars().all())
        # Python 侧按去版本号基础名过滤，避免 SQL like 前缀误配（如 sglang-v2-beta）
        return [
            record
            for record in records
            if normalize_document_base_name(record.normalized_name or record.filename) == base_name
        ]

    async def validate_parent_folder(self, *, kb_id: str, parent_id: str | None) -> None:
        if not parent_id:
            return
        async with pg_manager.get_async_session_context() as session:
            parent = (
                await session.execute(
                    select(KnowledgeFile).where(
                        KnowledgeFile.kb_id == kb_id,
                        KnowledgeFile.file_id == parent_id,
                        KnowledgeFile.is_folder.is_(True),
                    )
                )
            ).scalar_one_or_none()
            if parent is None:
                raise ParentFolderNotFoundError("Parent folder not found")

    async def list_pending_replacement_candidates(
        self,
        *,
        kb_id: str,
        replacement_target_file_id: str,
    ) -> list[KnowledgeFile]:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(KnowledgeFile).where(
                    KnowledgeFile.kb_id == kb_id,
                    KnowledgeFile.replacement_target_file_id == replacement_target_file_id,
                    KnowledgeFile.is_active.is_(False),
                    KnowledgeFile.status.notin_(FAILED_REPLACEMENT_CANDIDATE_STATUSES),
                )
            )
            return list(result.scalars().all())

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

    async def build_document_display_paths(self, records: list[KnowledgeFile]) -> dict[str, str]:
        paths: dict[str, str] = {}
        async with pg_manager.get_async_session_context() as session:
            folder_cache: dict[str, KnowledgeFile | None] = {}
            for record in records:
                parts = [str(record.filename or "")]
                parent_id = record.parent_id
                visited: set[str] = set()
                while parent_id and parent_id not in visited and len(visited) < 64:
                    visited.add(parent_id)
                    if parent_id not in folder_cache:
                        folder_cache[parent_id] = (
                            await session.execute(
                                select(KnowledgeFile).where(
                                    KnowledgeFile.kb_id == record.kb_id,
                                    KnowledgeFile.file_id == parent_id,
                                    KnowledgeFile.is_folder.is_(True),
                                )
                            )
                        ).scalar_one_or_none()
                    folder = folder_cache[parent_id]
                    if folder is None:
                        break
                    parts.append(str(folder.filename or ""))
                    parent_id = folder.parent_id
                paths[record.file_id] = "/".join(reversed([part for part in parts if part]))
        return paths

    async def get_folder_chain(
        self, *, kb_id: str, folder_id: str
    ) -> list[dict] | None:
        """返回真实文件夹（is_folder=True）的祖先链（top-down，含目标自身）。

        用于全库搜索等入口从文件夹结果深链进入文件浏览：前端需要完整面包屑。
        沿 parent_id 上溯，visited-set + 64 跳上限防环路/深链；祖先缺失（悬空
        parent_id）即视为到达根边界。目标文件夹不存在返回 None。
        """
        async with pg_manager.get_async_session_context() as session:
            target = (
                await session.execute(
                    select(KnowledgeFile).where(
                        KnowledgeFile.kb_id == kb_id,
                        KnowledgeFile.file_id == folder_id,
                        KnowledgeFile.is_folder.is_(True),
                    )
                )
            ).scalar_one_or_none()
            if target is None:
                return None
            chain: list[dict] = []
            current: KnowledgeFile | None = target
            visited: set[str] = set()
            while current is not None and current.file_id not in visited and len(visited) < 64:
                visited.add(current.file_id)
                chain.append({"file_id": current.file_id, "filename": current.filename})
                parent_id = current.parent_id
                if not parent_id:
                    break
                current = (
                    await session.execute(
                        select(KnowledgeFile).where(
                            KnowledgeFile.kb_id == kb_id,
                            KnowledgeFile.file_id == parent_id,
                            KnowledgeFile.is_folder.is_(True),
                        )
                    )
                ).scalar_one_or_none()
            return list(reversed(chain))

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
                    KnowledgeFile.is_current.is_(True),
                    func.lower(KnowledgeFile.filename).like(f"%{escaped_pattern}%", escape="\\"),
                )
                .order_by(KnowledgeFile.file_id.asc())
                .limit(min(max(int(limit or 100), 1), 10_000))
            )
            return [str(file_id) for file_id in result.scalars().all()]

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
                    KnowledgeFile.is_current.is_(True),
                    KnowledgeFile.content_hash == normalized_hash,
                    or_(KnowledgeFile.status.is_(None), KnowledgeFile.status != "failed"),
                )
                .limit(1)
            )
            return result.scalar_one_or_none() is not None

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
            KnowledgeFile.is_current.is_(True),
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
                    KnowledgeFile.filename == filename,
                    KnowledgeFile.is_folder.is_not(True),
                    KnowledgeFile.is_current.is_(True),
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
    def _normalize_parent_id(parent_id: object) -> str | None:
        normalized = str(parent_id or "").strip()
        return normalized or None

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
            if normalize_document_filename(candidate) not in existing_lower_names:
                return candidate
            counter += 1

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
        parent_id = self._normalize_parent_id(sanitized_data.get("parent_id"))
        normalized_filename = normalize_document_filename(filename)
        if not kb_id or not content_hash or not filename:
            raise ValueError("kb_id, content_hash and filename are required")
        sanitized_data["parent_id"] = parent_id
        sanitized_data["normalized_name"] = normalized_filename

        lock_keys = {
            stable_advisory_lock_key("knowledge-file-content", f"{kb_id}\0{content_hash}"),
            stable_advisory_lock_key(
                "knowledge-file-name",
                f"{kb_id}\0{parent_id or '<root>'}\0{normalized_filename}",
            ),
        }
        if duplicate_strategy == "keep_both":
            lock_keys.add(
                stable_advisory_lock_key(
                    "knowledge-file-name-allocation",
                    f"{kb_id}\0{parent_id or '<root>'}",
                )
            )
        if duplicate_strategy == "replace" and replace_file_id:
            lock_keys.add(stable_advisory_lock_key("knowledge-file-replacement-target", f"{kb_id}\0{replace_file_id}"))

        async with pg_manager.get_async_session_context() as session:
            for lock_key in sorted(lock_keys):
                await session.execute(text("SELECT pg_advisory_xact_lock(:lock_key)"), {"lock_key": lock_key})

            if parent_id:
                parent = (
                    await session.execute(
                        select(KnowledgeFile).where(
                            KnowledgeFile.kb_id == kb_id,
                            KnowledgeFile.file_id == parent_id,
                            KnowledgeFile.is_folder.is_(True),
                        )
                    )
                ).scalar_one_or_none()
                if parent is None:
                    raise ParentFolderNotFoundError("Parent folder not found")

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
                            self._parent_condition(parent_id),
                            KnowledgeFile.is_folder.is_(False),
                            KnowledgeFile.is_active.is_(True),
                            or_(
                                KnowledgeFile.normalized_name == normalized_filename,
                                and_(
                                    KnowledgeFile.normalized_name.is_(None),
                                    func.lower(func.trim(KnowledgeFile.filename)) == normalized_filename,
                                ),
                            ),
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
                            self._parent_condition(parent_id),
                            KnowledgeFile.is_folder.is_(False),
                            KnowledgeFile.is_active.is_(True),
                            or_(
                                KnowledgeFile.normalized_name == normalized_filename,
                                and_(
                                    KnowledgeFile.normalized_name.is_(None),
                                    func.lower(func.trim(KnowledgeFile.filename)) == normalized_filename,
                                ),
                            ),
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
            elif duplicate_strategy == "keep_both" and same_name_records:
                sibling_names = (
                    await session.execute(
                        select(KnowledgeFile.filename).where(
                            KnowledgeFile.kb_id == kb_id,
                            self._parent_condition(parent_id),
                            KnowledgeFile.is_folder.is_(False),
                            KnowledgeFile.is_active.is_(True),
                        )
                    )
                ).scalars()
                all_names = {normalize_document_filename(name) for name in sibling_names}
                sanitized_data["filename"] = self._next_available_filename(filename, all_names)
                sanitized_data["normalized_name"] = normalize_document_filename(sanitized_data["filename"])

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
            # 同步翻转 is_current，让基于 is_current 的列表/查询自动隐藏被替换旧版
            new_record.is_current = True
            new_record.updated_at = now
            old_record.is_active = False
            old_record.superseded_at = now
            old_record.is_current = False
            old_record.updated_at = now
            await session.flush()
            return new_record

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
        filters = [
            KnowledgeFile.kb_id == kb_id,
            KnowledgeFile.is_current.is_(True),
        ]
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
            KnowledgeFile.is_current.is_(True),
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
                    KnowledgeFile.parent_id.in_(parent_ids),
                    KnowledgeFile.is_current.is_(True),
                )
                .group_by(KnowledgeFile.parent_id)
            )
            return {str(parent_id): int(count or 0) for parent_id, count in result.all() if parent_id}

    async def get_kb_file_stats(self, kb_id: str) -> dict[str, int]:
        non_folder = KnowledgeFile.is_folder.is_(False)
        current = KnowledgeFile.is_current.is_(True)
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
                                non_folder & KnowledgeFile.status.in_(["processing", "waiting", "parsing", "indexing"]),
                                1,
                            ),
                            else_=0,
                        )
                    ).label("processing_count"),
                ).where(KnowledgeFile.kb_id == kb_id, current)
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

    async def delete(self, file_id: str) -> None:
        """删除文件记录，并级联清理指向它的未完成版本/替换候选。

        版本候选（supersedes_file_id）与替换候选（replacement_target_file_id）在
        被替换的当前版本删除后若不清理，会留下 is_current=false 但 is_active 的
        孤儿记录：列表不可见，却仍会被重复上传检测按文件名/is_active 匹配到，
        导致“知识库中没有该文档却提示重复”。
        """
        async with pg_manager.get_async_session_context() as session:
            pending = list(
                (
                    await session.execute(
                        select(KnowledgeFile).where(
                            KnowledgeFile.is_current.is_(False),
                            or_(
                                KnowledgeFile.supersedes_file_id == file_id,
                                KnowledgeFile.replacement_target_file_id == file_id,
                            ),
                        )
                    )
                )
                .scalars()
                .all()
            )
            for record in pending:
                await session.delete(record)
            result = await session.execute(select(KnowledgeFile).where(KnowledgeFile.file_id == file_id))
            record = result.scalar_one_or_none()
            if record is not None:
                await session.delete(record)

    async def list_pending_candidate_file_ids(self, *, file_id: str) -> list[str]:
        """列出指向某文件的未完成版本/替换候选 file_id（删除时用于级联清理 chunks）。"""
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(KnowledgeFile.file_id).where(
                    KnowledgeFile.is_current.is_(False),
                    or_(
                        KnowledgeFile.supersedes_file_id == file_id,
                        KnowledgeFile.replacement_target_file_id == file_id,
                    ),
                )
            )
            return [row[0] for row in result.all()]

    async def delete_by_kb_id(self, kb_id: str) -> None:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(select(KnowledgeFile).where(KnowledgeFile.kb_id == kb_id))
            for record in result.scalars().all():
                await session.delete(record)
