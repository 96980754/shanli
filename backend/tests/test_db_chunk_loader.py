from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.db import Base
from app.models import DocumentChunk, Role, User
from app.services.db_chunk_loader import DbChunkLoader
from app.services.db_document_service import DbDocumentService
from app.services.db_kb_service import DbKnowledgeBaseService
from app.services.document_filter_service import EffectiveDocumentFilter


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


def test_db_chunk_loader_only_returns_chunks_matching_effective_filter():
    session = build_session()
    role = Role(name="管理员", level=3)
    owner = User(username="admin", password_hash="hash", role=role)
    session.add_all([role, owner])
    session.commit()

    kb = DbKnowledgeBaseService(session).create(name="检索知识库", owner_id=owner.id)
    doc_service = DbDocumentService(session)
    allowed = doc_service.upload(kb_id=kb.id, filename="allowed.txt", content=b"")
    allowed.scope = "I"
    allowed.document_type = "UM"
    allowed.product = "MC"
    allowed.priority = "P0"
    allowed.department = "售后"
    allowed.security_level = 2
    allowed.acl_roles = '["sales"]'

    blocked = doc_service.upload(kb_id=kb.id, filename="blocked.txt", content=b"")
    blocked.scope = "R"
    blocked.document_type = "WP"
    blocked.product = "MS"
    blocked.priority = "P2"
    blocked.department = "研发"
    blocked.security_level = 3
    blocked.acl_roles = '["support"]'
    session.add_all([
        DocumentChunk(document_id=allowed.id, chunk_index=0, content="allowed"),
        DocumentChunk(document_id=blocked.id, chunk_index=0, content="blocked"),
    ])
    session.commit()

    document_filter = EffectiveDocumentFilter(
        allow_all=True,
        allowed_scopes={"I"},
        allowed_departments={"售后"},
        allowed_products={"MC"},
        allowed_roles={"sales"},
        max_security_level=2,
    )

    chunks = DbChunkLoader(session).load_chunks(
        kb_id=kb.id,
        document_filter=document_filter,
    )

    assert chunks == [
        {
            "chunk_id": "1",
            "document_id": allowed.id,
            "content": "allowed",
            "score": 0.0,
            "doc_title": "allowed.txt",
            "chunk_index": 0,
            "scope": "I",
            "document_type": "UM",
            "product": "MC",
            "priority": "P0",
            "security_level": 2,
            "acl_roles": '["sales"]',
        }
    ]
