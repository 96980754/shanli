from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import Document, KnowledgeBase


class DbDocumentService:
    """Database-backed document metadata service."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def upload(
        self,
        kb_id: int,
        filename: str,
        content: bytes,
        department: str = "",
        product_line: str = "",
        visibility: str = "internal",
        security_level: int = 1,
        tags: str = "",
    ) -> Document:
        file_type = filename.rsplit(".", 1)[-1] if "." in filename else "unknown"
        document = Document(
            kb_id=kb_id,
            title=filename,
            file_type=file_type,
            status="pending",
            department=department,
            product_line=product_line,
            visibility=visibility,
            security_level=security_level,
            tags=tags,
        )
        self.session.add(document)

        kb = self.session.get(KnowledgeBase, kb_id)
        if kb is not None:
            kb.doc_count += 1

        self.session.commit()
        self.session.refresh(document)
        return document

    def list(self, kb_id: int) -> list[Document]:
        return (
            self.session.query(Document)
            .filter(Document.kb_id == kb_id)
            .order_by(Document.id)
            .all()
        )

    def get(self, kb_id: int, doc_id: int) -> Document | None:
        return (
            self.session.query(Document)
            .filter(Document.kb_id == kb_id, Document.id == doc_id)
            .one_or_none()
        )
