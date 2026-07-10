from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models import DocumentChunk


class DbChunkLoader:
    """Load persisted document chunks into retrieval-ready dictionaries."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def load_chunks(self, kb_id: int) -> list[dict[str, Any]]:
        chunks = (
            self.session.query(DocumentChunk)
            .filter(DocumentChunk.document.has(kb_id=kb_id))
            .order_by(DocumentChunk.id)
            .all()
        )
        return [
            {
                "chunk_id": str(chunk.id),
                "content": chunk.content,
                "score": 0.0,
                "doc_title": chunk.document.title,
                "chunk_index": chunk.chunk_index,
            }
            for chunk in chunks
        ]
