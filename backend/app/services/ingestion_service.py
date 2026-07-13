from __future__ import annotations

from pathlib import Path

from app.models import ContentBlock, DocumentChunk, ParseTask
from app.services.file_storage import FileStorageService
from app.services.parser_service import parse_file_to_elements


class IngestionService:
    """Create parse records from an already persisted original file."""

    def __init__(self, session, file_storage: FileStorageService) -> None:
        self.session = session
        self.file_storage = file_storage

    def stage_uploaded_document(self, document) -> dict[str, str | int]:
        if not document.storage_key or not self.file_storage.exists(document.storage_key):
            raise FileNotFoundError("File not found")

        task = ParseTask(document_id=document.id, kb_id=document.kb_id, status="pending")
        self.session.add(task)
        self.session.flush()

        return {
            "task_id": task.id,
            "storage_key": document.storage_key,
            "staged_filename": Path(document.storage_key).name,
        }

    def ingest_uploaded_document(self, document) -> dict[str, str | int]:
        staged = self.stage_uploaded_document(document=document)
        task = self.session.get(ParseTask, staged["task_id"])
        try:
            elements = parse_file_to_elements(self.file_storage.path_for(document.storage_key))
        except ValueError:
            return {
                **staged,
                "block_count": 0,
                "chunk_count": 0,
            }

        chunk_index = 0
        for element in elements:
            block = ContentBlock(
                parse_task_id=task.id,
                content_type=element["type"],
                raw_text=element["content"],
            )
            self.session.add(block)
            for chunk_text in self._split_text(element["content"]):
                chunk = DocumentChunk(
                    document_id=document.id,
                    chunk_index=chunk_index,
                    content=chunk_text,
                )
                self.session.add(chunk)
                chunk_index += 1

        task.status = "parsed"

        return {
            **staged,
            "block_count": len(elements),
            "chunk_count": chunk_index,
        }

    def _split_text(self, text: str) -> list[str]:
        return [part.strip() for part in text.split("\n\n") if part.strip()]
