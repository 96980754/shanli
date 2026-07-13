from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models import DocumentChunk
from app.services.document_filter_service import EffectiveDocumentFilter


class DbChunkLoader:
    """Load persisted document chunks into retrieval-ready dictionaries."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def load_chunks(
        self,
        kb_id: int,
        document_filter: EffectiveDocumentFilter | None = None,
    ) -> list[dict[str, Any]]:
        chunks = (
            self.session.query(DocumentChunk)
            .filter(DocumentChunk.document.has(kb_id=kb_id))
            .order_by(DocumentChunk.id)
            .all()
        )
        if document_filter is not None:
            chunks = [chunk for chunk in chunks if document_filter.matches(chunk.document)]

        return [
            self._serialize_chunk(chunk, include_metadata=document_filter is not None)
            for chunk in chunks
        ]

    def _serialize_chunk(self, chunk: DocumentChunk, include_metadata: bool) -> dict[str, Any]:
        item = {
            "chunk_id": str(chunk.id),
            "content": chunk.content,
            "score": 0.0,
            "doc_title": chunk.document.title,
            "chunk_index": chunk.chunk_index,
        }
        if include_metadata:
            item.update(
                {
                    "document_id": chunk.document_id,
                    "scope": chunk.document.scope,
                    "document_type": chunk.document.document_type,
                    "product": chunk.document.product,
                    "priority": chunk.document.priority,
                    "security_level": chunk.document.security_level,
                    "acl_roles": chunk.document.acl_roles,
                }
            )
        return item
