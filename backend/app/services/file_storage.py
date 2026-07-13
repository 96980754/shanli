from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Protocol
from uuid import uuid4


@dataclass(frozen=True)
class StoredFile:
    storage_key: str
    original_filename: str
    content_type: str
    file_size: int


class FileStorageService(Protocol):
    def save(
        self,
        content: bytes,
        original_filename: str,
        content_type: str,
        kb_id: str,
    ) -> StoredFile: ...

    def open(self, storage_key: str, mode: str = "rb") -> BinaryIO: ...

    def path_for(self, storage_key: str) -> Path: ...

    def exists(self, storage_key: str) -> bool: ...

    def delete(self, storage_key: str) -> None: ...


class LocalFileStorageService:
    def __init__(self, root: str | Path) -> None:
        self._root = Path(root).resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def save(
        self,
        content: bytes,
        original_filename: str,
        content_type: str,
        kb_id: str,
    ) -> StoredFile:
        safe_filename = self._safe_filename(original_filename)
        storage_key = f"knowledge-bases/{kb_id}/documents/{uuid4()}-{safe_filename}"
        path = self.path_for(storage_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = path.with_name(f".{path.name}.tmp")
        try:
            temporary_path.write_bytes(content)
            temporary_path.replace(path)
        except Exception:
            temporary_path.unlink(missing_ok=True)
            raise
        return StoredFile(
            storage_key=storage_key,
            original_filename=original_filename,
            content_type=content_type,
            file_size=len(content),
        )

    def open(self, storage_key: str, mode: str = "rb") -> BinaryIO:
        return self.path_for(storage_key).open(mode)

    def path_for(self, storage_key: str) -> Path:
        return self._resolve_storage_path(storage_key)

    def exists(self, storage_key: str) -> bool:
        return self.path_for(storage_key).exists()

    def delete(self, storage_key: str) -> None:
        self.path_for(storage_key).unlink(missing_ok=True)

    def _resolve_storage_path(self, storage_key: str) -> Path:
        if not storage_key:
            raise ValueError("Invalid storage key")

        candidate = Path(storage_key)
        if candidate.is_absolute() or any(part == ".." for part in candidate.parts):
            raise ValueError("Invalid storage key")

        resolved = (self._root / candidate).resolve()
        try:
            resolved.relative_to(self._root)
        except ValueError as exc:
            raise ValueError("Invalid storage key") from exc
        return resolved

    def _safe_filename(self, original_filename: str) -> str:
        filename = Path(original_filename).name
        filename = re.sub(r"[^A-Za-z0-9._-]+", "-", filename).strip("-.")
        return filename or "uploaded"
