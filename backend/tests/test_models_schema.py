import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models import Base, Conversation, KnowledgeBase, KnowledgeViewRule, Message, MessageFeedback, Role, User
from app.models.document import Document, DocumentChunk
from app.models.kg_ops import ParseTask, ContentBlock, ReviewQueue, ConflictLog, KnowledgeIssue, AuditLog
from app.models.user import KnowledgeBasePermission


def test_all_bootstrap_tables_are_declared():
    expected_tables = {
        "users",
        "roles",
        "knowledge_bases",
        "kb_permissions",
        "documents",
        "document_chunks",
        "parse_tasks",
        "content_blocks",
        "review_queue",
        "conflict_log",
        "knowledge_issues",
        "audit_log",
    }

    assert expected_tables.issubset(set(Base.metadata.tables.keys()))


def test_models_create_tables_and_persist_minimal_records():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    role = Role(name="知识库管理员", level=3)
    user = User(username="admin", password_hash="hash", role=role)
    kb = KnowledgeBase(name="产品知识库", visibility="department", owner=user)
    doc = Document(kb=kb, title="manual.pdf", file_type="pdf", status="pending")
    chunk = DocumentChunk(document=doc, chunk_index=0, content="SOS报警说明")
    parse_task = ParseTask(document=doc, kb=kb, status="pending")
    block = ContentBlock(parse_task=parse_task, content_type="text", raw_text="SOS报警说明")
    review = ReviewQueue(entity_type="Product", suggested_value="P368", status="pending")
    conflict = ConflictLog(entity_id="P368", field_name="battery", status="pending")
    issue = KnowledgeIssue(user_query="P368怎么关SOS", classification="missing_doc", status="open")
    audit = AuditLog(user=user, action="create_kb", target_type="knowledge_base")
    permission = KnowledgeBasePermission(
        kb=kb,
        user=user,
        can_view=True,
        can_upload=True,
        can_delete=False,
        can_grant=False,
    )

    session.add_all([role, user, kb, doc, chunk, parse_task, block, review, conflict, issue, audit, permission])
    session.commit()

    inspector = inspect(engine)
    assert "knowledge_bases" in inspector.get_table_names()
    assert session.query(KnowledgeBase).one().name == "产品知识库"
    assert session.query(DocumentChunk).one().content == "SOS报警说明"
    assert session.query(ParseTask).one().status == "pending"


def test_kb_permissions_reject_duplicate_kb_user_pairs():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    role = Role(name="知识库管理员", level=3)
    user = User(username="admin", password_hash="hash", role=role)
    kb = KnowledgeBase(name="产品知识库", visibility="department", owner=user)
    first_permission = KnowledgeBasePermission(kb=kb, user=user, can_view=True)
    duplicate_permission = KnowledgeBasePermission(kb=kb, user=user, can_upload=True)

    session.add_all([role, user, kb, first_permission, duplicate_permission])

    try:
        session.commit()
    except IntegrityError:
        session.rollback()
    else:
        raise AssertionError("expected duplicate kb permission commit to fail")


def test_document_model_persists_metadata_fields_with_defaults():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    role = Role(name="知识库管理员", level=3)
    user = User(username="admin", password_hash="hash", role=role)
    kb = KnowledgeBase(name="产品知识库", visibility="department", owner=user)
    doc = Document(
        kb=kb,
        title="manual.txt",
        file_type="txt",
        status="pending",
        department="售后",
        product_line="P368",
        visibility="internal",
        security_level=2,
        tags="FAQ,报警",
    )
    session.add_all([role, user, kb, doc])
    session.commit()

    saved = session.query(Document).one()
    assert saved.department == "售后"
    assert saved.product_line == "P368"
    assert saved.visibility == "internal"
    assert saved.security_level == 2
    assert saved.tags == "FAQ,报警"


def test_document_and_audit_log_persist_file_storage_and_download_fields():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    role = Role(name="管理员", level=3)
    user = User(username="admin", password_hash="hash", role=role)
    kb = KnowledgeBase(name="产品知识库", visibility="department", owner=user)
    document = Document(
        kb=kb,
        title="manual.txt",
        file_type="txt",
        storage_key="knowledge-bases/1/documents/manual.txt",
        original_filename="产品手册.txt",
        content_type="text/plain",
        file_size=9,
    )
    audit = AuditLog(
        user=user,
        action="download_document",
        target_type="document",
        target_id="1",
        kb_id=1,
        detail="manual.txt",
    )
    session.add_all([role, user, kb, document, audit])
    session.commit()

    saved_document = session.query(Document).one()
    saved_audit = session.query(AuditLog).one()
    assert saved_document.storage_key == "knowledge-bases/1/documents/manual.txt"
    assert saved_document.original_filename == "产品手册.txt"
    assert saved_document.content_type == "text/plain"
    assert saved_document.file_size == 9
    assert saved_audit.target_id == "1"
    assert saved_audit.kb_id == 1
    assert saved_audit.detail == "manual.txt"
    assert saved_audit.created_at is not None


def test_knowledge_view_rule_persists_json_scopes_and_security_level():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    role = Role(name="管理员", level=3)
    user = User(username="viewer", password_hash="hash", role=role)
    kb = KnowledgeBase(name="产品知识库", visibility="department", owner=user)
    rule = KnowledgeViewRule(
        kb=kb,
        user=user,
        allowed_departments='["售后"]',
        allowed_product_lines='["P368"]',
        allowed_visibilities='["public", "internal"]',
        max_security_level=2,
    )
    session.add_all([role, user, kb, rule])
    session.commit()

    saved = session.query(KnowledgeViewRule).one()
    assert saved.allowed_departments == '["售后"]'
    assert saved.allowed_product_lines == '["P368"]'
    assert saved.allowed_visibilities == '["public", "internal"]'
    assert saved.max_security_level == 2
    assert saved.created_at is not None
    assert saved.updated_at is not None


def test_knowledge_view_rules_reject_duplicate_kb_user_pairs():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    role = Role(name="管理员", level=3)
    user = User(username="viewer", password_hash="hash", role=role)
    kb = KnowledgeBase(name="产品知识库", visibility="department", owner=user)
    first = KnowledgeViewRule(kb=kb, user=user)
    duplicate = KnowledgeViewRule(kb=kb, user=user)
    session.add_all([role, user, kb, first, duplicate])

    with pytest.raises(IntegrityError):
        session.commit()


def test_document_persists_v2_metadata_with_compatible_defaults():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    role = Role(name="管理员", level=3)
    user = User(username="admin", password_hash="hash", role=role)
    kb = KnowledgeBase(name="产品库", visibility="department", owner=user)
    explicit = Document(
        kb=kb,
        title="whitepaper.pdf",
        file_type="pdf",
        scope="I",
        document_type="WP",
        product="MC",
        priority="P0",
        acl_roles='["sales"]',
    )
    legacy = Document(kb=kb, title="legacy.txt", file_type="txt")
    session.add_all([role, user, kb, explicit, legacy])
    session.commit()

    assert explicit.scope == "I"
    assert explicit.document_type == "WP"
    assert explicit.product == "MC"
    assert explicit.priority == "P0"
    assert explicit.acl_roles == '["sales"]'
    assert legacy.scope == "I"
    assert legacy.document_type == "OTH"
    assert legacy.product == "GEN"
    assert legacy.priority == "P2"
    assert legacy.acl_roles == "[]"


def test_qa_ops_models_can_persist_conversation_message_feedback_and_issue():
    engine = create_engine("sqlite://", poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db_session = Session()

    role = Role(name="管理员", level=3)
    user = User(username="admin", password_hash="hash", role=role)
    kb = KnowledgeBase(name="问答知识库", owner_id=1, visibility="department")
    db_session.add_all([role, user, kb])
    db_session.commit()

    conversation = Conversation(kb_id=kb.id, user_id=user.id, title="SOS 报警怎么关闭")
    db_session.add(conversation)
    db_session.commit()

    message = Message(
        conversation_id=conversation.id,
        kb_id=kb.id,
        user_id=user.id,
        question="SOS 报警怎么关闭",
        answer="在设置中关闭。",
        sources="[]",
    )
    db_session.add(message)
    db_session.commit()

    feedback = MessageFeedback(
        message_id=message.id,
        user_id=user.id,
        is_helpful=False,
        feedback_text="没有菜单路径",
    )
    db_session.add(feedback)
    db_session.commit()

    issue = KnowledgeIssue(
        kb_id=kb.id,
        message_id=message.id,
        question=message.question,
        reason="answer_missing_path",
        feedback_text=feedback.feedback_text,
        user_query=message.question,
        classification="answer_quality",
        status="open",
    )
    db_session.add(issue)
    db_session.commit()

    duplicate_feedback = MessageFeedback(
        message_id=message.id,
        user_id=user.id,
        is_helpful=True,
        feedback_text="重复反馈",
    )
    db_session.add(duplicate_feedback)

    try:
        db_session.commit()
    except IntegrityError:
        db_session.rollback()
    else:
        raise AssertionError("expected duplicate message feedback commit to fail")

    inspector = inspect(engine)
    constraints = inspector.get_unique_constraints("message_feedback")
    constraint_names = {constraint["name"] for constraint in constraints}

    assert conversation.id is not None
    assert conversation.created_at is not None
    assert conversation.updated_at is not None
    assert message.id is not None
    assert message.created_at is not None
    assert feedback.id is not None
    assert feedback.created_at is not None
    assert issue.id is not None
    assert issue.created_at is not None
    assert issue.updated_at is not None
    assert issue.question == "SOS 报警怎么关闭"
    assert issue.reason == "answer_missing_path"
    assert issue.feedback_text == "没有菜单路径"
    assert "uq_message_feedback_message_id_user_id" in constraint_names
