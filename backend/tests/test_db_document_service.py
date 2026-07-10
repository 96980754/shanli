from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.db import Base
from app.models import Document, KnowledgeBase, Role, User
from app.services.db_document_service import DbDocumentService
from app.services.db_kb_service import DbKnowledgeBaseService


def build_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    return Session()


def setup_kb(session):
    role = Role(name="管理员", level=3)
    owner = User(username="admin", password_hash="hash", role=role)
    session.add_all([role, owner])
    session.commit()
    kb = DbKnowledgeBaseService(session).create(name="知识库", owner_id=owner.id)
    return kb


def test_db_document_service_uploads_document_and_updates_db_count():
    session = build_session()
    kb = setup_kb(session)
    service = DbDocumentService(session)

    document = service.upload(kb_id=kb.id, filename="manual.pdf", content=b"PDF")

    assert document.title == "manual.pdf"
    assert document.file_type == "pdf"
    assert document.status == "pending"
    assert session.get(KnowledgeBase, kb.id).doc_count == 1


def test_db_document_service_lists_documents_for_kb():
    session = build_session()
    kb = setup_kb(session)
    service = DbDocumentService(session)
    uploaded = service.upload(kb_id=kb.id, filename="faq.txt", content=b"faq")

    items = service.list(kb.id)

    assert len(items) == 1
    assert items[0].id == uploaded.id
