from pathlib import Path
from tempfile import TemporaryDirectory

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.db import Base
from app.models import ContentBlock, DocumentChunk, KnowledgeBase, ParseTask, Role, User
from app.services.db_document_service import DbDocumentService
from app.services.db_kb_service import DbKnowledgeBaseService
from app.services.ingestion_service import IngestionService


def build_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    return Session()


def test_ingestion_service_saves_file_and_creates_parse_task():
    session = build_session()
    role = Role(name="管理员", level=3)
    owner = User(username="admin", password_hash="hash", role=role)
    session.add_all([role, owner])
    session.commit()

    kb_service = DbKnowledgeBaseService(session)
    doc_service = DbDocumentService(session)
    kb = kb_service.create(name="入库知识库", owner_id=owner.id)
    document = doc_service.upload(kb_id=kb.id, filename="manual.txt", content=b"SOS alarm")

    with TemporaryDirectory() as tmpdir:
        service = IngestionService(session=session, upload_root=Path(tmpdir))
        result = service.stage_uploaded_document(document=document, content=b"SOS alarm")

        assert result["task_id"] is not None
        assert result["file_path"].endswith("manual.txt")
        assert Path(result["file_path"]).exists()

        task = session.query(ParseTask).one()
        assert task.document_id == document.id
        assert task.kb_id == kb.id
        assert task.status == "pending"


def test_ingestion_service_parses_text_file_into_content_blocks():
    session = build_session()
    role = Role(name="管理员", level=3)
    owner = User(username="admin", password_hash="hash", role=role)
    session.add_all([role, owner])
    session.commit()

    kb_service = DbKnowledgeBaseService(session)
    doc_service = DbDocumentService(session)
    kb = kb_service.create(name="入库知识库", owner_id=owner.id)
    document = doc_service.upload(kb_id=kb.id, filename="manual.txt", content=b"SOS alarm")

    with TemporaryDirectory() as tmpdir:
        service = IngestionService(session=session, upload_root=Path(tmpdir))
        result = service.ingest_uploaded_document(document=document, content="SOS alarm".encode())

        assert result["task_id"] is not None
        assert result["block_count"] == 1
        task = session.query(ParseTask).one()
        assert task.status == "parsed"
        block = session.query(ContentBlock).one()
        assert block.parse_task_id == task.id
        assert block.content_type == "text"
        assert block.raw_text == "SOS alarm"


def test_ingestion_service_creates_ordered_document_chunks_from_content_blocks():
    session = build_session()
    role = Role(name="管理员", level=3)
    owner = User(username="admin", password_hash="hash", role=role)
    session.add_all([role, owner])
    session.commit()

    kb_service = DbKnowledgeBaseService(session)
    doc_service = DbDocumentService(session)
    kb = kb_service.create(name="入库知识库", owner_id=owner.id)
    document = doc_service.upload(kb_id=kb.id, filename="manual.txt", content=b"")
    text = "第一段产品说明。\n\n第二段安装步骤。"

    with TemporaryDirectory() as tmpdir:
        service = IngestionService(session=session, upload_root=Path(tmpdir))
        result = service.ingest_uploaded_document(document=document, content=text.encode())

        assert result["chunk_count"] == 2
        chunks = session.query(DocumentChunk).order_by(DocumentChunk.chunk_index).all()
        assert [chunk.chunk_index for chunk in chunks] == [0, 1]
        assert [chunk.content for chunk in chunks] == ["第一段产品说明。", "第二段安装步骤。"]
        assert all(chunk.document_id == document.id for chunk in chunks)
