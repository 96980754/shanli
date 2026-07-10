from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.db import Base
from app.models import KnowledgeBase, Role, User
from app.services.db_kb_service import DbKnowledgeBaseService


def build_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    return Session()


def test_db_kb_service_creates_and_lists_knowledge_bases():
    session = build_session()
    role = Role(name="管理员", level=3)
    owner = User(username="admin", password_hash="hash", role=role)
    session.add_all([role, owner])
    session.commit()

    service = DbKnowledgeBaseService(session)
    created = service.create(name="数据库知识库", owner_id=owner.id, description="desc", visibility="department")

    assert created.name == "数据库知识库"
    assert created.doc_count == 0
    assert service.get(created.id).id == created.id
    assert service.list()[0].name == "数据库知识库"


def test_db_kb_service_increment_doc_count_persists_to_database():
    session = build_session()
    role = Role(name="管理员", level=3)
    owner = User(username="admin", password_hash="hash", role=role)
    session.add_all([role, owner])
    session.commit()

    service = DbKnowledgeBaseService(session)
    created = service.create(name="数据库知识库", owner_id=owner.id)
    service.increment_doc_count(created.id)

    refreshed = service.get(created.id)
    assert refreshed.doc_count == 1
