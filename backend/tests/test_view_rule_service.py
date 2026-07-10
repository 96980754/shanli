from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.db import Base
from app.models import Document, KnowledgeBase, KnowledgeViewRule, Role, User
from app.services.view_rule_service import KnowledgeViewRuleService


def build_service_context():
    engine = create_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    role = Role(name="管理员", level=3)
    user = User(username="viewer", password_hash="hash", role=role)
    kb = KnowledgeBase(name="产品知识库", visibility="department", owner=user)
    session.add_all([role, user, kb])
    session.commit()
    return session, kb, user, KnowledgeViewRuleService(session)


def test_set_rule_serializes_scopes_and_get_rule_deserializes_them():
    _, kb, user, service = build_service_context()

    saved = service.set_rule(
        kb_id=kb.id,
        user_id=user.id,
        allowed_departments=["售后"],
        allowed_product_lines=["P368", "MCSTARS"],
        allowed_visibilities=["public", "internal"],
        max_security_level=2,
    )

    payload = service.serialize_rule(saved)
    assert payload["allowed_departments"] == ["售后"]
    assert payload["allowed_product_lines"] == ["P368", "MCSTARS"]
    assert payload["allowed_visibilities"] == ["public", "internal"]
    assert payload["max_security_level"] == 2


def test_set_rule_overwrites_existing_rule_instead_of_creating_duplicate():
    session, kb, user, service = build_service_context()

    service.set_rule(kb.id, user.id, ["售后"], [], [], None)
    service.set_rule(kb.id, user.id, ["交付"], [], [], 1)

    assert session.query(KnowledgeViewRule).count() == 1
    rule = service.get_rule(kb.id, user.id)
    assert service.serialize_rule(rule)["allowed_departments"] == ["交付"]
    assert rule.max_security_level == 1


def test_delete_rule_restores_no_rule_state():
    _, kb, user, service = build_service_context()
    service.set_rule(kb.id, user.id, [], [], [], None)

    assert service.delete_rule(kb.id, user.id) is not None
    assert service.get_rule(kb.id, user.id) is None


def test_no_rule_allows_document():
    _, kb, _, service = build_service_context()
    document = Document(kb_id=kb.id, title="manual.txt", file_type="txt", department="研发", security_level=3)

    assert service.can_view_document(document, None) is True


def test_empty_rule_dimensions_do_not_restrict_document():
    _, kb, user, service = build_service_context()
    rule = service.set_rule(kb.id, user.id, [], [], [], None)
    document = Document(
        kb_id=kb.id,
        title="manual.txt",
        file_type="txt",
        department="研发",
        product_line="X1000",
        visibility="restricted",
        security_level=3,
    )

    assert service.can_view_document(document, rule) is True


def test_rule_uses_or_within_dimension_and_and_across_dimensions():
    _, kb, user, service = build_service_context()
    rule = service.set_rule(
        kb.id,
        user.id,
        ["售后", "交付"],
        ["P368", "MCSTARS"],
        ["public", "internal"],
        2,
    )
    allowed = Document(
        kb_id=kb.id,
        title="allowed.txt",
        file_type="txt",
        department="交付",
        product_line="P368",
        visibility="internal",
        security_level=2,
    )
    blocked = Document(
        kb_id=kb.id,
        title="blocked.txt",
        file_type="txt",
        department="研发",
        product_line="P368",
        visibility="internal",
        security_level=2,
    )

    assert service.can_view_document(allowed, rule) is True
    assert service.can_view_document(blocked, rule) is False


def test_rule_blocks_document_above_max_security_level():
    _, kb, user, service = build_service_context()
    rule = service.set_rule(kb.id, user.id, [], [], [], 2)
    document = Document(kb_id=kb.id, title="secret.txt", file_type="txt", security_level=3)

    assert service.can_view_document(document, rule) is False
