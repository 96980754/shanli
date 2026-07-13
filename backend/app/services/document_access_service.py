from app.models import Document
from app.services.document_filter_service import DocumentFilterService


class DocumentAccessService:
    def __init__(self, document_filter_service: DocumentFilterService) -> None:
        self.document_filter_service = document_filter_service

    def can_access_document(self, kb_id: int, user_id: int, document: Document) -> bool:
        if document.kb_id != kb_id:
            return False
        document_filter = self.document_filter_service.build_filter(
            kb_id=kb_id,
            user_id=user_id,
            user_roles=self.document_filter_service.roles_for_user(user_id),
        )
        return document_filter.matches(document)

    def filter_accessible_documents(
        self,
        kb_id: int,
        user_id: int,
        documents: list[Document],
    ) -> list[Document]:
        return [
            document
            for document in documents
            if self.can_access_document(kb_id, user_id, document)
        ]
