from pathlib import Path
from tempfile import TemporaryDirectory

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.db import Base
from app.models import ContentBlock, DocumentChunk, KnowledgeBase, ParseTask, Role, User
from app.services.db_document_service import DbDocumentService
from app.services.db_kb_service import DbKnowledgeBaseService
from app.services.file_storage import LocalFileStorageService
from app.services.ingestion_service import IngestionService


def build_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    return Session()


def test_db_document_service_persists_stored_file_metadata():
    session = build_session()
    role = Role(name="管理员", level=3)
    owner = User(username="admin", password_hash="hash", role=role)
    session.add_all([role, owner])
    session.commit()
    kb = DbKnowledgeBaseService(session).create(name="入库知识库", owner_id=owner.id)

    document = DbDocumentService(session).upload(
        kb_id=kb.id,
        filename="manual.txt",
        content=b"ignored-by-service",
        storage_key="knowledge-bases/1/documents/file.txt",
        original_filename="原始手册.txt",
        content_type="text/plain",
        file_size=9,
    )

    assert document.storage_key == "knowledge-bases/1/documents/file.txt"
    assert document.original_filename == "原始手册.txt"
    assert document.content_type == "text/plain"
    assert document.file_size == 9


def test_ingestion_reads_the_already_saved_original_file():
    session = build_session()
    role = Role(name="管理员", level=3)
    owner = User(username="admin", password_hash="hash", role=role)
    session.add_all([role, owner])
    session.commit()
    kb = DbKnowledgeBaseService(session).create(name="入库知识库", owner_id=owner.id)

    with TemporaryDirectory() as tmpdir:
        storage = LocalFileStorageService(Path(tmpdir))
        stored = storage.save(b"SOS alarm", "manual.txt", "text/plain", kb.id)
        document = DbDocumentService(session).upload(
            kb_id=kb.id,
            filename=stored.original_filename,
            content=b"",
            storage_key=stored.storage_key,
            original_filename=stored.original_filename,
            content_type=stored.content_type,
            file_size=stored.file_size,
        )

        result = IngestionService(session, storage).ingest_uploaded_document(document)

    assert result["block_count"] == 1
    assert session.query(ContentBlock).one().raw_text == "SOS alarm"


def test_ingestion_service_saves_file_and_creates_parse_task():
    session = build_session()
    role = Role(name="管理员", level=3)
    owner = User(username="admin", password_hash="hash", role=role)
    session.add_all([role, owner])
    session.commit()
    kb = DbKnowledgeBaseService(session).create(name="入库知识库", owner_id=owner.id)

    with TemporaryDirectory() as tmpdir:
        storage = LocalFileStorageService(Path(tmpdir))
        stored = storage.save(b"SOS alarm", "manual.txt", "text/plain", kb.id)
        document = DbDocumentService(session).upload(
            kb_id=kb.id,
            filename=stored.original_filename,
            content=b"SOS alarm",
            storage_key=stored.storage_key,
            original_filename=stored.original_filename,
            content_type=stored.content_type,
            file_size=stored.file_size,
        )
        service = IngestionService(session=session, file_storage=storage)
        result = service.stage_uploaded_document(document=document)

    assert result["task_id"] is not None
    assert result["storage_key"] == stored.storage_key
    assert result["staged_filename"] == Path(stored.storage_key).name
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

    kb = DbKnowledgeBaseService(session).create(name="入库知识库", owner_id=owner.id)

    with TemporaryDirectory() as tmpdir:
        storage = LocalFileStorageService(Path(tmpdir))
        stored = storage.save(b"SOS alarm", "manual.txt", "text/plain", kb.id)
        document = DbDocumentService(session).upload(
            kb_id=kb.id,
            filename=stored.original_filename,
            content=b"SOS alarm",
            storage_key=stored.storage_key,
            original_filename=stored.original_filename,
            content_type=stored.content_type,
            file_size=stored.file_size,
        )
        service = IngestionService(session=session, file_storage=storage)
        result = service.ingest_uploaded_document(document=document)

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

    kb = DbKnowledgeBaseService(session).create(name="入库知识库", owner_id=owner.id)
    text = "第一段产品说明。\n\n第二段安装步骤。"

    with TemporaryDirectory() as tmpdir:
        storage = LocalFileStorageService(Path(tmpdir))
        stored = storage.save(text.encode(), "manual.txt", "text/plain", kb.id)
        document = DbDocumentService(session).upload(
            kb_id=kb.id,
            filename=stored.original_filename,
            content=b"",
            storage_key=stored.storage_key,
            original_filename=stored.original_filename,
            content_type=stored.content_type,
            file_size=stored.file_size,
        )
        service = IngestionService(session=session, file_storage=storage)
        result = service.ingest_uploaded_document(document=document)

    assert result["chunk_count"] == 2
    chunks = session.query(DocumentChunk).order_by(DocumentChunk.chunk_index).all()
    assert [chunk.chunk_index for chunk in chunks] == [0, 1]
    assert [chunk.content for chunk in chunks] == ["第一段产品说明。", "第二段安装步骤。"]
    assert all(chunk.document_id == document.id for chunk in chunks)
