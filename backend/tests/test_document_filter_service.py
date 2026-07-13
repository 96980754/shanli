from __future__ import annotations

from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.db import Base
from app.models import Document, Role, User
from app.services.db_kb_service import DbKnowledgeBaseService
from app.services.document_filter_service import DocumentFilterService
from app.services.view_rule_service import KnowledgeViewRuleService


def build_context():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    role = Role(name="sales", level=1)
    owner = User(username="admin", password_hash="hash", role=role)
    viewer = User(username="viewer", password_hash="hash", role=role)
    session.add_all([role, owner, viewer])
    session.commit()

    kb_service = DbKnowledgeBaseService(session)
    kb = kb_service.create(name="过滤知识库", owner_id=owner.id)
    rules = KnowledgeViewRuleService(session)
    service = DocumentFilterService(kb_service, rules)
    return SimpleNamespace(
        session=session,
        kb=kb,
        owner=owner,
        viewer=viewer,
        kb_service=kb_service,
        rules=rules,
        service=service,
    )


def grant_view(context, can_grant=False):
    context.kb_service.set_permission(
        context.kb.id,
        context.viewer.id,
        {
            "can_view": True,
            "can_upload": False,
            "can_delete": False,
            "can_grant": can_grant,
        },
    )


def test_effective_filter_allows_matching_scope_department_product_security_and_role():
    context = build_context()
    grant_view(context)
    context.rules.set_rule(
        context.kb.id,
        context.viewer.id,
        allowed_departments=["售后"],
        allowed_product_lines=["MC"],
        allowed_visibilities=["internal"],
        max_security_level=2,
    )
    document_filter = context.service.build_filter(context.kb.id, context.viewer.id, user_roles={"sales"})
    document = Document(
        kb_id=context.kb.id,
        title="manual.txt",
        file_type="txt",
        scope="I",
        department="售后",
        product="MC",
        security_level=2,
        acl_roles='["sales"]',
    )

    assert document_filter.matches(document) is True


def test_effective_filter_rejects_scope_security_product_and_role_mismatches():
    context = build_context()
    grant_view(context)
    context.rules.set_rule(
        context.kb.id,
        context.viewer.id,
        allowed_departments=["售后"],
        allowed_product_lines=["MC"],
        allowed_visibilities=["internal"],
        max_security_level=2,
    )
    document_filter = context.service.build_filter(context.kb.id, context.viewer.id, user_roles={"sales"})
    base = {
        "kb_id": context.kb.id,
        "title": "manual.txt",
        "file_type": "txt",
        "scope": "I",
        "department": "售后",
        "product": "MC",
        "security_level": 2,
        "acl_roles": '["sales"]',
    }

    assert document_filter.matches(Document(**{**base, "scope": "R"})) is False
    assert document_filter.matches(Document(**{**base, "security_level": 3})) is False
    assert document_filter.matches(Document(**{**base, "product": "MS"})) is False
    assert document_filter.matches(Document(**{**base, "acl_roles": '["support"]'})) is False


def test_effective_filter_uses_legacy_product_line_when_product_code_is_unknown():
    context = build_context()
    grant_view(context)
    context.rules.set_rule(
        context.kb.id,
        context.viewer.id,
        allowed_departments=[],
        allowed_product_lines=["MCSTARS"],
        allowed_visibilities=[],
        max_security_level=None,
    )
    document_filter = context.service.build_filter(context.kb.id, context.viewer.id, user_roles=set())
    document = Document(
        kb_id=context.kb.id,
        title="legacy.txt",
        file_type="txt",
        product="GEN",
        product_line="MCSTARS",
    )

    assert document_filter.matches(document) is True


def test_effective_filter_normalizes_legacy_product_rule_to_v2_product_code():
    context = build_context()
    grant_view(context)
    context.rules.set_rule(
        context.kb.id,
        context.viewer.id,
        allowed_departments=[],
        allowed_product_lines=["MCSTARS"],
        allowed_visibilities=[],
        max_security_level=None,
    )
    document_filter = context.service.build_filter(context.kb.id, context.viewer.id, user_roles=set())
    document = Document(kb_id=context.kb.id, title="normalized.txt", file_type="txt", product="MC")

    assert document_filter.matches(document) is True


def test_can_grant_bypasses_user_rule_but_not_system_security_limit():
    context = build_context()
    grant_view(context, can_grant=True)
    context.rules.set_rule(
        context.kb.id,
        context.viewer.id,
        allowed_departments=["售后"],
        allowed_product_lines=["MC"],
        allowed_visibilities=["internal"],
        max_security_level=1,
    )
    document = Document(
        kb_id=context.kb.id,
        title="restricted.txt",
        file_type="txt",
        scope="R",
        department="研发",
        product="MS",
        security_level=3,
        acl_roles='["support"]',
    )

    bypass_filter = context.service.build_filter(context.kb.id, context.viewer.id, user_roles={"sales"})
    forced_filter = context.service.build_filter(
        context.kb.id,
        context.viewer.id,
        user_roles={"sales"},
        system_max_security_level=2,
    )

    assert bypass_filter.matches(document) is True
    assert forced_filter.matches(document) is False


def test_effective_filter_for_user_without_can_view_rejects_every_document():
    context = build_context()
    document_filter = context.service.build_filter(context.kb.id, context.viewer.id, user_roles={"sales"})
    document = Document(kb_id=context.kb.id, title="manual.txt", file_type="txt")

    assert document_filter.matches(document) is False
