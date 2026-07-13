from __future__ import annotations

from collections import defaultdict
from typing import Any
from uuid import uuid4

from app.services.kb_service import InMemoryKnowledgeBaseService


class InMemoryDocumentService:
    """In-memory document metadata service used by MVP skeleton."""

    def __init__(self, kb_service: InMemoryKnowledgeBaseService) -> None:
        self.kb_service = kb_service
        self._documents_by_kb: dict[str, list[dict[str, Any]]] = defaultdict(list)

    def upload(
        self,
        kb_id: str,
        filename: str,
        content: bytes,
        department: str = "",
        product_line: str = "",
        visibility: str = "internal",
        security_level: int = 1,
        tags: str = "",
        scope: str = "I",
        document_type: str = "OTH",
        product: str = "GEN",
        priority: str = "P2",
    ) -> dict[str, Any] | None:
        if self.kb_service.get(kb_id) is None:
            return None

        file_type = filename.rsplit(".", 1)[-1] if "." in filename else "unknown"
        doc = {
            "id": str(uuid4()),
            "kb_id": kb_id,
            "title": filename,
            "file_type": file_type,
            "file_size": len(content),
            "status": "pending",
            "department": department,
            "product_line": product_line,
            "visibility": visibility,
            "security_level": security_level,
            "tags": tags,
            "scope": scope,
            "document_type": document_type,
            "product": product,
            "priority": priority,
        }
        self._documents_by_kb[kb_id].append(doc)
        self.kb_service.increment_doc_count(kb_id)
        return doc

    def list(self, kb_id: str) -> list[dict[str, Any]] | None:
        if self.kb_service.get(kb_id) is None:
            return None
        return self._documents_by_kb.get(kb_id, [])

    def get(self, kb_id: str, doc_id: str) -> dict[str, Any] | None:
        if self.kb_service.get(kb_id) is None:
            return None
        for item in self._documents_by_kb.get(kb_id, []):
            if item["id"] == doc_id:
                return item
        return None

    def delete(self, kb_id: str, doc_id: str) -> dict[str, Any] | None:
        if self.kb_service.get(kb_id) is None:
            return None
        items = self._documents_by_kb.get(kb_id, [])
        for index, item in enumerate(items):
            if item["id"] == doc_id:
                deleted = items.pop(index)
                kb = self.kb_service.get(kb_id)
                if kb is not None:
                    kb["doc_count"] = max(0, kb["doc_count"] - 1)
                return deleted
        return None
