from app.services.document_service import InMemoryDocumentService
from app.services.kb_service import InMemoryKnowledgeBaseService


def test_document_service_uploads_document_and_updates_kb_count():
    kb_service = InMemoryKnowledgeBaseService()
    document_service = InMemoryDocumentService(kb_service)
    kb = kb_service.create("产品知识库")

    doc = document_service.upload(kb["id"], filename="manual.pdf", content=b"PDF")

    assert doc["title"] == "manual.pdf"
    assert doc["file_type"] == "pdf"
    assert doc["status"] == "pending"
    assert kb_service.get(kb["id"])["doc_count"] == 1


def test_document_service_lists_documents_by_kb():
    kb_service = InMemoryKnowledgeBaseService()
    document_service = InMemoryDocumentService(kb_service)
    kb = kb_service.create("FAQ知识库")
    uploaded = document_service.upload(kb["id"], filename="faq.txt", content=b"faq")

    assert document_service.list(kb["id"])[0]["id"] == uploaded["id"]
