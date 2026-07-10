from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.db import Base
from app.models import DocumentChunk, Role, User
from app.services.db_chunk_loader import DbChunkLoader
from app.services.db_document_service import DbDocumentService
from app.services.db_kb_service import DbKnowledgeBaseService


def build_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    return Session()


def test_db_chunk_loader_returns_retrieval_ready_chunks_for_kb():
    session = build_session()
    role = Role(name="管理员", level=3)
    owner = User(username="admin", password_hash="hash", role=role)
    session.add_all([role, owner])
    session.commit()

    kb_service = DbKnowledgeBaseService(session)
    doc_service = DbDocumentService(session)
    kb = kb_service.create(name="检索知识库", owner_id=owner.id)
    document = doc_service.upload(kb_id=kb.id, filename="manual.txt", content=b"")
    session.add(
        DocumentChunk(
            document_id=document.id,
            chunk_index=0,
            content="SOS alarm can be disabled in settings.",
        )
    )
    session.commit()

    chunks = DbChunkLoader(session).load_chunks(kb_id=kb.id)

    assert chunks == [
        {
            "chunk_id": "1",
            "content": "SOS alarm can be disabled in settings.",
            "score": 0.0,
            "doc_title": "manual.txt",
            "chunk_index": 0,
        }
    ]
