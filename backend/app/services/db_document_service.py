from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import ContentBlock, Document, DocumentChunk, KnowledgeBase, ParseTask


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
        scope: str = "I",
        document_type: str = "OTH",
        product: str = "GEN",
        priority: str = "P2",
        storage_key: str = "",
        original_filename: str = "",
        content_type: str = "",
        file_size: int = 0,
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
            scope=scope,
            document_type=document_type,
            product=product,
            priority=priority,
            storage_key=storage_key,
            original_filename=original_filename,
            content_type=content_type,
            file_size=file_size,
        )
        self.session.add(document)

        kb = self.session.get(KnowledgeBase, kb_id)
        if kb is not None:
            kb.doc_count += 1

        self.session.flush()
        return document

    def list(self, kb_id: int) -> list[Document]:
        return (
            self.session.query(Document)
            .filter(Document.kb_id == kb_id)
            .order_by(Document.id)
            .all()
        )

    def delete(self, kb_id: int, doc_id: int) -> Document | None:
        document = self.get(kb_id, doc_id)
        if document is None:
            return None
        task_ids = [
            task_id
            for task_id, in self.session.query(ParseTask.id)
            .filter(ParseTask.document_id == document.id, ParseTask.kb_id == kb_id)
            .all()
        ]
        if task_ids:
            self.session.query(ContentBlock).filter(ContentBlock.parse_task_id.in_(task_ids)).delete(
                synchronize_session=False,
            )
            self.session.query(ParseTask).filter(ParseTask.id.in_(task_ids)).delete(synchronize_session=False)
        self.session.query(DocumentChunk).filter(DocumentChunk.document_id == document.id).delete(
            synchronize_session=False,
        )
        kb = self.session.get(KnowledgeBase, kb_id)
        if kb is not None:
            kb.doc_count = max(0, kb.doc_count - 1)
        self.session.delete(document)
        self.session.commit()
        return document

    def get(self, kb_id: int, doc_id: int) -> Document | None:
        return (
            self.session.query(Document)
            .filter(Document.kb_id == kb_id, Document.id == doc_id)
            .one_or_none()
        )
