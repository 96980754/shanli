from __future__ import annotations
from typing import Any
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_knowledge import DocumentQAPair
from yuxi.utils.datetime_utils import utc_now_naive


class DocumentQARepository:
    async def get_by_qa_id(self, qa_id: str) -> DocumentQAPair | None:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(select(DocumentQAPair).where(DocumentQAPair.qa_id == qa_id))
            return result.scalar_one_or_none()

    async def list_by_file_id(
        self,
        *,
        kb_id: str,
        file_id: str,
        include_rejected: bool = False,
    ) -> list[DocumentQAPair]:
        filters = [DocumentQAPair.kb_id == kb_id, DocumentQAPair.file_id == file_id]
        if not include_rejected:
            filters.append(DocumentQAPair.status != "rejected")
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(DocumentQAPair)
                .where(*filters)
                .order_by(DocumentQAPair.created_at.asc(), DocumentQAPair.id.asc())
            )
            return list(result.scalars().all())

    async def list_by_qa_ids(self, qa_ids: list[str]) -> list[DocumentQAPair]:
        normalized = list(dict.fromkeys(qa_id for qa_id in qa_ids if qa_id))
        if not normalized:
            return []
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(select(DocumentQAPair).where(DocumentQAPair.qa_id.in_(normalized)))
            by_id = {record.qa_id: record for record in result.scalars().all()}
            return [by_id[qa_id] for qa_id in normalized if qa_id in by_id]

    async def find_by_identity(
        self,
        *,
        file_id: str,
        content_hash: str,
        question_hash: str,
    ) -> DocumentQAPair | None:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(DocumentQAPair).where(
                    DocumentQAPair.file_id == file_id,
                    DocumentQAPair.content_hash == content_hash,
                    DocumentQAPair.question_hash == question_hash,
                )
            )
            return result.scalar_one_or_none()

    async def create(self, data: dict[str, Any]) -> DocumentQAPair:
        async with pg_manager.get_async_session_context() as session:
            record = DocumentQAPair(**data)
            session.add(record)
            await session.flush()
            return record

    async def create_or_get(self, data: dict[str, Any]) -> tuple[DocumentQAPair, bool]:
        """Create one generated draft, or return the concurrent winner."""
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                insert(DocumentQAPair)
                .values(**data)
                .on_conflict_do_nothing(
                    index_elements=["file_id", "content_hash", "question_hash"],
                )
                .returning(DocumentQAPair)
            )
            created = result.scalar_one_or_none()
            if created is not None:
                return created, True
            existing = await session.execute(
                select(DocumentQAPair).where(
                    DocumentQAPair.file_id == data["file_id"],
                    DocumentQAPair.content_hash == data["content_hash"],
                    DocumentQAPair.question_hash == data["question_hash"],
                )
            )
            return existing.scalar_one(), False

    async def update_with_version(
        self,
        *,
        kb_id: str,
        file_id: str,
        qa_id: str,
        expected_version: int,
        data: dict[str, Any],
    ) -> DocumentQAPair | None:
        values = {**data, "version": DocumentQAPair.version + 1, "updated_at": utc_now_naive()}
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                update(DocumentQAPair)
                .where(
                    DocumentQAPair.kb_id == kb_id,
                    DocumentQAPair.file_id == file_id,
                    DocumentQAPair.qa_id == qa_id,
                    DocumentQAPair.version == max(1, int(expected_version)),
                )
                .values(**values)
                .returning(DocumentQAPair)
            )
            return result.scalar_one_or_none()

    async def mark_outdated_by_file_id(self, *, kb_id: str, file_id: str) -> int:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                update(DocumentQAPair)
                .where(
                    DocumentQAPair.kb_id == kb_id,
                    DocumentQAPair.file_id == file_id,
                    DocumentQAPair.status != "rejected",
                )
                .values(
                    possibly_outdated=True,
                    version=DocumentQAPair.version + 1,
                    updated_at=utc_now_naive(),
                )
            )
            return int(result.rowcount or 0)
