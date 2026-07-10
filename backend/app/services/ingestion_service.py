from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from app.models import ContentBlock, DocumentChunk, ParseTask
from app.services.parser_service import parse_file_to_elements


class IngestionService:
    """Stage uploaded files and create parse task records."""

    def __init__(self, session, upload_root: Path) -> None:
        self.session = session
        self.upload_root = upload_root

    def stage_uploaded_document(self, document, content: bytes) -> dict[str, str | int]:
        self.upload_root.mkdir(parents=True, exist_ok=True)
        filename = document.title or f"document-{document.id}.bin"
        staged_name = f"{document.id}-{uuid4().hex}-{filename}"
        file_path = self.upload_root / staged_name
        file_path.write_bytes(content)

        task = ParseTask(document_id=document.id, kb_id=document.kb_id, status="pending")
        self.session.add(task)
        self.session.commit()
        self.session.refresh(task)

        return {
            "task_id": task.id,
            "file_path": str(file_path),
            "staged_filename": file_path.name,
        }

    def ingest_uploaded_document(self, document, content: bytes) -> dict[str, str | int]:
        staged = self.stage_uploaded_document(document=document, content=content)
        task = self.session.get(ParseTask, staged["task_id"])
        try:
            elements = parse_file_to_elements(Path(staged["file_path"]))
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
        self.session.commit()

        return {
            **staged,
            "block_count": len(elements),
            "chunk_count": chunk_index,
        }

    def _split_text(self, text: str) -> list[str]:
        return [part.strip() for part in text.split("\n\n") if part.strip()]
