from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from yuxi.knowledge.utils import is_minio_url, parse_minio_url
from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository
from yuxi.storage.minio import get_minio_client
from yuxi.utils.datetime_utils import utc_isoformat


class KnowledgeSourceVersionService:
    def __init__(
        self,
        *,
        repository: KnowledgeFileRepository | None = None,
        original_exists: Callable[[Any], Awaitable[bool]] | None = None,
    ) -> None:
        self.repository = repository or KnowledgeFileRepository()
        self._original_exists = original_exists or self._minio_original_exists

    async def list_for_current_files(self, *, kb_id: str, file_ids: list[str]) -> list[dict[str, Any]]:
        normalized_ids = list(dict.fromkeys(file_id for file_id in file_ids if file_id))
        chains = await self.repository.list_version_chains_for_current_files(
            kb_id=kb_id,
            file_ids=normalized_ids,
        )

        items = []
        for file_id in normalized_ids:
            chain = chains.get(file_id)
            if not chain:
                continue
            ordered_chain = self._order_chain(chain)
            current = ordered_chain[0]
            history_candidates = ordered_chain[1:]
            availability = await asyncio.gather(
                *(self._original_exists(record) for record in history_candidates)
            )
            histories = [
                record
                for record, original_exists in zip(history_candidates, availability, strict=True)
                if original_exists
            ]
            items.append(
                {
                    "file_id": current.file_id,
                    "filename": current.filename,
                    "document_version": self._version_number(current, ordered_chain),
                    "history_versions": [
                        {
                            "file_id": record.file_id,
                            "filename": record.filename,
                            "document_version": self._version_number(record, ordered_chain),
                            "updated_at": self._timestamp(record),
                        }
                        for record in histories
                    ],
                }
            )
        return items

    @staticmethod
    def _order_chain(chain: list[Any]) -> list[Any]:
        current = next((record for record in chain if record.is_current and record.is_active), chain[0])
        records_by_id = {record.file_id: record for record in chain}
        if current.previous_version_id:
            ordered = [current]
            previous_id = current.previous_version_id
            while previous_id and previous_id in records_by_id:
                previous = records_by_id[previous_id]
                ordered.append(previous)
                previous_id = previous.previous_version_id
            return ordered

        histories = [record for record in chain if record.file_id != current.file_id]
        histories.sort(
            key=lambda record: (
                int(record.document_version or 0),
                (record.activated_at or record.created_at).isoformat()
                if record.activated_at or record.created_at
                else "",
            ),
            reverse=True,
        )
        return [current, *histories]

    @staticmethod
    def _version_number(record: Any, ordered_chain: list[Any]) -> int:
        if ordered_chain[0].previous_version_id:
            return len(ordered_chain) - ordered_chain.index(record)
        if record.document_version:
            return int(record.document_version)
        return len(ordered_chain) - ordered_chain.index(record)

    @staticmethod
    def _timestamp(record: Any) -> str | None:
        value = record.activated_at or record.superseded_at or record.updated_at or record.created_at
        return utc_isoformat(value) if value else None

    @staticmethod
    async def _minio_original_exists(record: Any) -> bool:
        file_path = record.minio_url or record.path
        if not file_path or not is_minio_url(file_path):
            return False
        bucket_name, object_name = parse_minio_url(file_path)
        try:
            return await get_minio_client().astat_file(bucket_name, object_name) is not None
        except Exception:
            return False
