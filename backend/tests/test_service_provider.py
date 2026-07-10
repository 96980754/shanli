from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.db import Base
from app.main import build_app_state_services
from app.models import Role, User


def test_build_app_state_services_uses_in_memory_services_by_default():
    services = build_app_state_services()

    assert services["mode"] == "memory"
    assert services["kb_service"].__class__.__name__ == "InMemoryKnowledgeBaseService"
    assert services["document_service"].__class__.__name__ == "InMemoryDocumentService"


def test_build_app_state_services_can_switch_to_database_services():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    role = Role(name="管理员", level=3)
    owner = User(username="admin", password_hash="hash", role=role)
    session.add_all([role, owner])
    session.commit()

    services = build_app_state_services(mode="database", session=session)
    kb = services["kb_service"].create(name="数据库知识库", owner_id=owner.id)
    doc = services["document_service"].upload(kb_id=kb.id, filename="manual.pdf", content=b"PDF")

    assert services["mode"] == "database"
    assert kb.name == "数据库知识库"
    assert doc.title == "manual.pdf"
