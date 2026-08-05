"""PostgreSQL 知识库模型 - KnowledgeBase、KnowledgeFile、评估相关表"""

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from yuxi.storage.postgres.models_business import Base
from yuxi.utils.datetime_utils import utc_now_naive

JSON_VALUE = JSON().with_variant(JSONB, "postgresql")


class KnowledgeBase(Base):
    """知识库模型"""

    __tablename__ = "knowledge_bases"
    __table_args__ = (UniqueConstraint("kb_id", name="uq_knowledge_bases_kb_id"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    kb_id = Column(String(80), unique=True, nullable=False, index=True)
    name = Column(String(255), nullable=False, index=True)
    description = Column(Text)
    kb_type = Column(String(32), nullable=False, index=True)
    embedding_model_spec = Column(String(512))
    llm_model_spec = Column(String(512))
    query_params = Column(JSON_VALUE)
    additional_params = Column(JSON_VALUE)
    share_config = Column(JSON_VALUE)
    mindmap = Column(JSON_VALUE)
    mindmap_file_ids = Column(JSON_VALUE)
    mindmap_metadata = Column(JSON_VALUE)
    sample_questions = Column(JSON_VALUE)
    created_by = Column(String(64))
    created_at = Column(DateTime(timezone=True), default=utc_now_naive)
    updated_at = Column(DateTime(timezone=True), default=utc_now_naive, onupdate=utc_now_naive)


class KnowledgeBasePermission(Base):
    """单企业私有化知识库操作级授权。"""

    __tablename__ = "knowledge_base_permissions"
    __table_args__ = (
        UniqueConstraint("kb_id", "subject_type", "subject_id", name="uq_knowledge_base_permissions_subject"),
        Index("ix_knowledge_base_permissions_kb_id", "kb_id"),
        Index("ix_knowledge_base_permissions_subject", "subject_type", "subject_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    kb_id = Column(String(80), ForeignKey("knowledge_bases.kb_id", ondelete="CASCADE"), nullable=False)
    subject_type = Column(String(32), nullable=False)
    subject_id = Column(String(128), nullable=False)
    can_view = Column(Boolean, nullable=False, default=False)
    can_search = Column(Boolean, nullable=False, default=False)
    can_upload = Column(Boolean, nullable=False, default=False)
    can_download = Column(Boolean, nullable=False, default=False)
    can_delete = Column(Boolean, nullable=False, default=False)
    can_manage = Column(Boolean, nullable=False, default=False)
    can_grant = Column(Boolean, nullable=False, default=False)
    can_export = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), default=utc_now_naive)
    updated_at = Column(DateTime(timezone=True), default=utc_now_naive, onupdate=utc_now_naive)


class KnowledgeFile(Base):
    """知识文件模型"""

    __tablename__ = "knowledge_files"
    __table_args__ = (
        UniqueConstraint("file_id", name="uq_knowledge_files_file_id"),
        CheckConstraint(
            "processing_progress >= 0 AND processing_progress <= 100",
            name="ck_knowledge_files_processing_progress",
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    file_id = Column(String(64), unique=True, nullable=False, index=True)
    kb_id = Column(String(80), ForeignKey("knowledge_bases.kb_id", ondelete="CASCADE"), nullable=False, index=True)
    parent_id = Column(String(64), ForeignKey("knowledge_files.file_id", ondelete="SET NULL"), index=True)
    filename = Column(String(512), nullable=False)
    normalized_name = Column(String(512))
    original_filename = Column(String(512))
    file_type = Column(String(64))
    path = Column(String(1024))
    minio_url = Column(String(1024))
    markdown_file = Column(String(1024))
    status = Column(String(32), default="uploaded", index=True)
    content_hash = Column(String(128), index=True)
    file_size = Column(BigInteger)
    chunk_count = Column(Integer, default=0)
    token_count = Column(BigInteger, default=0)
    content_type = Column(String(64))
    processing_params = Column(JSON_VALUE)
    parse_metadata = Column(JSON_VALUE)
    original_markdown_file = Column(String(1024))
    cleaning_draft_file = Column(String(1024))
    cleaning_metadata = Column(JSON_VALUE)
    cleaning_version = Column(Integer, nullable=False, default=0)
    confirmed_at = Column(DateTime(timezone=True))
    confirmed_by = Column(String(64))
    enrichment_data = Column(JSON_VALUE)
    enrichment_status = Column(String(32), index=True)
    enrichment_version = Column(Integer, nullable=False, default=0)
    enrichment_content_hash = Column(String(64))
    enrichment_generated_at = Column(DateTime(timezone=True))
    enrichment_error = Column(Text)
    enrichment_possibly_outdated = Column(Boolean, nullable=False, default=False)
    processing_stage = Column(String(64))
    processing_progress = Column(Integer, nullable=False, default=0)
    processing_task_id = Column(String(64))
    processing_task_attempt = Column(Integer, nullable=False, default=0)
    processing_task_updated_at = Column(DateTime(timezone=True))
    processing_task_lease_expires_at = Column(DateTime(timezone=True))
    replacement_target_file_id = Column(String(64), index=True)
    previous_version_id = Column(String(64), index=True)
    is_active = Column(Boolean, nullable=False, default=True, index=True)
    superseded_at = Column(DateTime(timezone=True))
    is_folder = Column(Boolean, default=False)
    error_message = Column(Text)
    created_by = Column(String(64))
    updated_by = Column(String(64))
    created_at = Column(DateTime(timezone=True), default=utc_now_naive)
    updated_at = Column(DateTime(timezone=True), default=utc_now_naive, onupdate=utc_now_naive)


class KnowledgeChunk(Base):
    """知识库 Chunk 模型"""

    __tablename__ = "knowledge_chunks"
    __table_args__ = (
        UniqueConstraint("chunk_id", name="uq_knowledge_chunks_chunk_id"),
        Index("ix_knowledge_chunks_file_id", "file_id"),
        Index("ix_knowledge_chunks_kb_id", "kb_id"),
        Index("ix_knowledge_chunks_graph_indexed", "graph_indexed"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    chunk_id = Column(String(128), nullable=False)
    file_id = Column(String(64), ForeignKey("knowledge_files.file_id", ondelete="CASCADE"), nullable=False)
    kb_id = Column(String(80), ForeignKey("knowledge_bases.kb_id", ondelete="CASCADE"), nullable=False)
    chunk_index = Column(Integer, nullable=False)
    content = Column(Text, nullable=False)
    start_char_pos = Column(Integer)
    end_char_pos = Column(Integer)
    start_token_pos = Column(Integer)
    end_token_pos = Column(Integer)
    graph_indexed = Column(Boolean, default=False)
    ent_ids = Column(JSON_VALUE)
    tags = Column(JSON_VALUE)
    extraction_result = Column(JSON_VALUE)
    source_metadata = Column(JSON_VALUE)
    created_at = Column(DateTime(timezone=True), default=utc_now_naive)
    updated_at = Column(DateTime(timezone=True), default=utc_now_naive, onupdate=utc_now_naive)


class KnowledgeGraphEntity(Base):
    """知识图谱实体"""

    __tablename__ = "knowledge_graph_entities"
    __table_args__ = (
        UniqueConstraint("entity_id", name="uq_knowledge_graph_entities_entity_id"),
        UniqueConstraint("kb_id", "normalized_name", "label", name="uq_knowledge_graph_entities_identity"),
        Index("ix_knowledge_graph_entities_kb_id", "kb_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    entity_id = Column(String(64), nullable=False)
    kb_id = Column(String(80), ForeignKey("knowledge_bases.kb_id", ondelete="CASCADE"), nullable=False)
    normalized_name = Column(String(512), nullable=False)
    label = Column(String(128), nullable=False)
    name = Column(String(512), nullable=False)
    attributes = Column(JSON_VALUE)
    created_at = Column(DateTime(timezone=True), default=utc_now_naive)
    updated_at = Column(DateTime(timezone=True), default=utc_now_naive, onupdate=utc_now_naive)


class KnowledgeGraphEntityMention(Base):
    """知识图谱实体在 chunk 中的引用"""

    __tablename__ = "knowledge_graph_entity_mentions"
    __table_args__ = (
        UniqueConstraint("entity_id", "chunk_id", name="uq_knowledge_graph_entity_mentions_entity_chunk"),
        Index("ix_knowledge_graph_entity_mentions_kb_id", "kb_id"),
        Index("ix_knowledge_graph_entity_mentions_file_id", "file_id"),
        Index("ix_knowledge_graph_entity_mentions_chunk_id", "chunk_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    entity_id = Column(String(64), ForeignKey("knowledge_graph_entities.entity_id", ondelete="CASCADE"), nullable=False)
    kb_id = Column(String(80), ForeignKey("knowledge_bases.kb_id", ondelete="CASCADE"), nullable=False)
    file_id = Column(String(64), ForeignKey("knowledge_files.file_id", ondelete="CASCADE"), nullable=False)
    chunk_id = Column(String(128), ForeignKey("knowledge_chunks.chunk_id", ondelete="CASCADE"), nullable=False)
    created_at = Column(DateTime(timezone=True), default=utc_now_naive)


class KnowledgeGraphTriple(Base):
    """知识图谱三元组"""

    __tablename__ = "knowledge_graph_triples"
    __table_args__ = (
        UniqueConstraint("triple_id", name="uq_knowledge_graph_triples_triple_id"),
        Index("ix_knowledge_graph_triples_kb_id", "kb_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    triple_id = Column(String(64), nullable=False)
    kb_id = Column(String(80), ForeignKey("knowledge_bases.kb_id", ondelete="CASCADE"), nullable=False)
    source_entity_id = Column(
        String(64), ForeignKey("knowledge_graph_entities.entity_id", ondelete="CASCADE"), nullable=False
    )
    target_entity_id = Column(
        String(64), ForeignKey("knowledge_graph_entities.entity_id", ondelete="CASCADE"), nullable=False
    )
    relation_type = Column(String(256), nullable=False)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), default=utc_now_naive)
    updated_at = Column(DateTime(timezone=True), default=utc_now_naive, onupdate=utc_now_naive)


class KnowledgeGraphTripleMention(Base):
    """知识图谱三元组在 chunk 中的引用"""

    __tablename__ = "knowledge_graph_triple_mentions"
    __table_args__ = (
        UniqueConstraint("triple_id", "chunk_id", name="uq_knowledge_graph_triple_mentions_triple_chunk"),
        Index("ix_knowledge_graph_triple_mentions_kb_id", "kb_id"),
        Index("ix_knowledge_graph_triple_mentions_file_id", "file_id"),
        Index("ix_knowledge_graph_triple_mentions_chunk_id", "chunk_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    triple_id = Column(String(64), ForeignKey("knowledge_graph_triples.triple_id", ondelete="CASCADE"), nullable=False)
    kb_id = Column(String(80), ForeignKey("knowledge_bases.kb_id", ondelete="CASCADE"), nullable=False)
    file_id = Column(String(64), ForeignKey("knowledge_files.file_id", ondelete="CASCADE"), nullable=False)
    chunk_id = Column(String(128), ForeignKey("knowledge_chunks.chunk_id", ondelete="CASCADE"), nullable=False)
    text = Column(Text)
    extractor_type = Column(String(128))
    created_at = Column(DateTime(timezone=True), default=utc_now_naive)


class KnowledgeAssertion(Base):
    """A version-bound candidate or reviewed business assertion."""

    __tablename__ = "knowledge_assertions"
    __table_args__ = (
        UniqueConstraint("assertion_id", name="uq_knowledge_assertions_assertion_id"),
        Index("ix_knowledge_assertions_kb_entity", "kb_id", "linked_entity_id"),
        Index("ix_knowledge_assertions_file_chunk", "file_id", "chunk_id"),
        Index("ix_knowledge_assertions_status", "status"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    assertion_id = Column(String(64), nullable=False)
    kb_id = Column(String(80), ForeignKey("knowledge_bases.kb_id", ondelete="CASCADE"), nullable=False)
    entity_type = Column(String(128), nullable=False)
    entity_name = Column(String(512), nullable=False)
    linked_entity_id = Column(
        String(64),
        ForeignKey("knowledge_graph_entities.entity_id", ondelete="SET NULL"),
        nullable=True,
    )
    predicate = Column(String(128), nullable=False)
    raw_value = Column(JSON_VALUE, nullable=False)
    normalized_value = Column(JSON_VALUE)
    value_type = Column(String(32), nullable=False)
    unit = Column(String(32))
    valid_from = Column(DateTime(timezone=True))
    valid_to = Column(DateTime(timezone=True))
    product_version = Column(String(128))
    file_id = Column(String(64), ForeignKey("knowledge_files.file_id", ondelete="CASCADE"), nullable=False)
    chunk_id = Column(String(128), ForeignKey("knowledge_chunks.chunk_id", ondelete="CASCADE"), nullable=False)
    evidence = Column(Text, nullable=False)
    cleaning_version = Column(Integer, nullable=False)
    content_hash = Column(String(128), nullable=False)
    extraction_method = Column(String(64), nullable=False)
    confidence = Column(Float)
    status = Column(String(32), nullable=False, default="candidate")
    source = Column(String(32), nullable=False, default="generated")
    published_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), default=utc_now_naive)
    updated_at = Column(DateTime(timezone=True), default=utc_now_naive, onupdate=utc_now_naive)


class EntityLinkCandidate(Base):
    """A deterministic entity-link candidate for one assertion."""

    __tablename__ = "entity_link_candidates"
    __table_args__ = (
        UniqueConstraint("link_id", name="uq_entity_link_candidates_link_id"),
        Index("ix_entity_link_candidates_assertion_id", "assertion_id"),
        Index("ix_entity_link_candidates_kb_id", "kb_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    link_id = Column(String(64), nullable=False)
    assertion_id = Column(
        String(64),
        ForeignKey("knowledge_assertions.assertion_id", ondelete="CASCADE"),
        nullable=False,
    )
    kb_id = Column(String(80), ForeignKey("knowledge_bases.kb_id", ondelete="CASCADE"), nullable=False)
    candidate_name = Column(String(512), nullable=False)
    normalized_name = Column(String(512), nullable=False)
    target_entity_id = Column(
        String(64),
        ForeignKey("knowledge_graph_entities.entity_id", ondelete="SET NULL"),
        nullable=True,
    )
    target_entity_name = Column(String(512))
    matching_rules = Column(JSON_VALUE, nullable=False)
    similarity = Column(Float)
    aliases = Column(JSON_VALUE)
    status = Column(String(32), nullable=False)
    resolved_by = Column(String(64))
    resolved_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), default=utc_now_naive)
    updated_at = Column(DateTime(timezone=True), default=utc_now_naive, onupdate=utc_now_naive)


class KnowledgeConflict(Base):
    """A review record produced by deterministic assertion comparison."""

    __tablename__ = "knowledge_conflicts"
    __table_args__ = (
        UniqueConstraint("conflict_id", name="uq_knowledge_conflicts_conflict_id"),
        UniqueConstraint("incoming_assertion_id", name="uq_knowledge_conflicts_incoming_assertion"),
        Index("ix_knowledge_conflicts_kb_status", "kb_id", "status"),
        Index("ix_knowledge_conflicts_entity_predicate", "entity_id", "predicate"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    conflict_id = Column(String(64), nullable=False)
    kb_id = Column(String(80), ForeignKey("knowledge_bases.kb_id", ondelete="CASCADE"), nullable=False)
    entity_id = Column(
        String(64),
        ForeignKey("knowledge_graph_entities.entity_id", ondelete="SET NULL"),
        nullable=True,
    )
    predicate = Column(String(128), nullable=False)
    existing_assertion_ids = Column(JSON_VALUE, nullable=False)
    incoming_assertion_id = Column(
        String(64),
        ForeignKey("knowledge_assertions.assertion_id", ondelete="CASCADE"),
        nullable=False,
    )
    conflict_type = Column(String(64), nullable=False)
    classification = Column(String(32), nullable=False)
    existing_value = Column(JSON_VALUE)
    incoming_value = Column(JSON_VALUE, nullable=False)
    normalized_existing_value = Column(JSON_VALUE)
    normalized_incoming_value = Column(JSON_VALUE)
    detection_rules = Column(JSON_VALUE, nullable=False)
    severity = Column(String(16), nullable=False)
    requires_review = Column(Boolean, nullable=False, default=True)
    status = Column(String(32), nullable=False, default="pending")
    resolution = Column(String(64))
    resolution_reason = Column(Text)
    resolved_by = Column(String(64))
    resolved_at = Column(DateTime(timezone=True))
    publish_status = Column(String(32), nullable=False, default="not_requested")
    publish_error = Column(Text)
    created_at = Column(DateTime(timezone=True), default=utc_now_naive)
    updated_at = Column(DateTime(timezone=True), default=utc_now_naive, onupdate=utc_now_naive)
    version = Column(Integer, nullable=False, default=1)


class KnowledgeConflictPublishTask(Base):
    """Durable outbox task for publishing one reviewed assertion version."""

    __tablename__ = "knowledge_conflict_publish_tasks"
    __table_args__ = (
        UniqueConstraint("task_id", name="uq_knowledge_conflict_publish_tasks_task_id"),
        UniqueConstraint(
            "conflict_id",
            "expected_version",
            name="uq_knowledge_conflict_publish_tasks_conflict_version",
        ),
        Index("ix_knowledge_conflict_publish_tasks_status_retry", "status", "next_attempt_at"),
        Index("ix_knowledge_conflict_publish_tasks_kb_id", "kb_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(String(64), nullable=False)
    conflict_id = Column(
        String(64),
        ForeignKey("knowledge_conflicts.conflict_id", ondelete="CASCADE"),
        nullable=False,
    )
    assertion_id = Column(
        String(64),
        ForeignKey("knowledge_assertions.assertion_id", ondelete="CASCADE"),
        nullable=False,
    )
    kb_id = Column(String(80), ForeignKey("knowledge_bases.kb_id", ondelete="CASCADE"), nullable=False)
    resolution_id = Column(String(64), nullable=False)
    entity_id = Column(String(64), nullable=True)
    expected_version = Column(Integer, nullable=False)
    status = Column(String(32), nullable=False, default="pending")
    neo4j_status = Column(String(32), nullable=False, default="pending")
    vector_status = Column(String(32), nullable=False, default="pending")
    attempt_count = Column(Integer, nullable=False, default=0)
    max_attempts = Column(Integer, nullable=False, default=5)
    error_code = Column(String(64))
    last_error = Column(Text)
    next_attempt_at = Column(DateTime(timezone=True))
    lease_expires_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), default=utc_now_naive)
    updated_at = Column(DateTime(timezone=True), default=utc_now_naive, onupdate=utc_now_naive)
    completed_at = Column(DateTime(timezone=True))


class DocumentQAPair(Base):
    """Document-bound QA draft and confirmed answer."""

    __tablename__ = "document_qa_pairs"
    __table_args__ = (
        UniqueConstraint("qa_id", name="uq_document_qa_pairs_qa_id"),
        UniqueConstraint(
            "file_id",
            "content_hash",
            "question_hash",
            name="uq_document_qa_pairs_file_content_question",
        ),
        Index("ix_document_qa_pairs_kb_id", "kb_id"),
        Index("ix_document_qa_pairs_file_id", "file_id"),
        Index("ix_document_qa_pairs_status", "status"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    qa_id = Column(String(64), nullable=False)
    kb_id = Column(String(80), ForeignKey("knowledge_bases.kb_id", ondelete="CASCADE"), nullable=False)
    file_id = Column(String(64), ForeignKey("knowledge_files.file_id", ondelete="CASCADE"), nullable=False)
    question = Column(Text, nullable=False)
    question_hash = Column(String(64), nullable=False)
    answer = Column(Text, nullable=False)
    source_chunk_ids = Column(JSON_VALUE, nullable=False)
    evidence = Column(JSON_VALUE, nullable=False)
    source = Column(String(32), nullable=False, default="generated")
    status = Column(String(32), nullable=False, default="draft")
    sync_status = Column(String(32), nullable=False, default="pending")
    sync_error = Column(Text)
    version = Column(Integer, nullable=False, default=1)
    cleaning_version = Column(Integer, nullable=False)
    content_hash = Column(String(64), nullable=False)
    model_name = Column(String(512))
    model_version = Column(String(64))
    generated_at = Column(DateTime(timezone=True))
    updated_at = Column(DateTime(timezone=True), default=utc_now_naive, onupdate=utc_now_naive)
    updated_by = Column(String(64))
    confirmed_at = Column(DateTime(timezone=True))
    confirmed_by = Column(String(64))
    possibly_outdated = Column(Boolean, nullable=False, default=False)
    deleted_by_user = Column(Boolean, nullable=False, default=False)
    error = Column(Text)
    created_at = Column(DateTime(timezone=True), default=utc_now_naive)


class EvaluationDataset(Base):
    """评估数据集模型"""

    __tablename__ = "evaluation_datasets"
    __table_args__ = (UniqueConstraint("dataset_id", name="uq_evaluation_datasets_dataset_id"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    dataset_id = Column(String(64), unique=True, nullable=False, index=True)
    kb_id = Column(String(80), ForeignKey("knowledge_bases.kb_id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    description = Column(Text)
    item_count = Column(Integer, default=0)
    has_gold_chunks = Column(Boolean, default=False)
    has_gold_answers = Column(Boolean, default=False)
    build_metadata = Column(JSON_VALUE)
    created_by = Column(String(64))
    created_at = Column(DateTime(timezone=True), default=utc_now_naive)
    updated_at = Column(DateTime(timezone=True), default=utc_now_naive, onupdate=utc_now_naive)


class EvaluationDatasetItem(Base):
    """评估数据集题目模型"""

    __tablename__ = "evaluation_dataset_items"
    __table_args__ = (
        UniqueConstraint("item_id", name="uq_evaluation_dataset_items_item_id"),
        UniqueConstraint("dataset_id", "item_index", name="uq_evaluation_dataset_items_dataset_index"),
        Index("ix_evaluation_dataset_items_dataset_index", "dataset_id", "item_index"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    item_id = Column(String(64), unique=True, nullable=False, index=True)
    dataset_id = Column(
        String(64),
        ForeignKey("evaluation_datasets.dataset_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    kb_id = Column(String(80), ForeignKey("knowledge_bases.kb_id", ondelete="CASCADE"), nullable=False, index=True)
    item_index = Column(Integer, nullable=False)
    query_text = Column(Text, nullable=False)
    gold_chunk_ids = Column(JSON_VALUE)
    gold_answer = Column(Text)
    created_at = Column(DateTime(timezone=True), default=utc_now_naive)


class EvaluationRun(Base):
    """评估运行模型"""

    __tablename__ = "evaluation_runs"
    __table_args__ = (UniqueConstraint("run_id", name="uq_evaluation_runs_run_id"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String(64), unique=True, nullable=False, index=True)
    name = Column(String(255), nullable=False)
    kb_id = Column(String(80), ForeignKey("knowledge_bases.kb_id", ondelete="CASCADE"), nullable=False, index=True)
    dataset_id = Column(
        String(64),
        ForeignKey("evaluation_datasets.dataset_id", ondelete="SET NULL"),
        index=True,
    )
    status = Column(String(32), default="running", index=True)
    retrieval_config = Column(JSON_VALUE)
    metrics = Column(JSON_VALUE)
    overall_score = Column(Float)
    total_items = Column(Integer, default=0)
    completed_items = Column(Integer, default=0)
    started_at = Column(DateTime(timezone=True), default=utc_now_naive, index=True)
    completed_at = Column(DateTime(timezone=True))
    created_by = Column(String(64))


class EvaluationRunItem(Base):
    """评估逐题结果模型"""

    __tablename__ = "evaluation_run_items"
    __table_args__ = (
        UniqueConstraint("run_id", "item_index", name="uq_evaluation_run_items_run_index"),
        Index("ix_evaluation_run_items_run_index", "run_id", "item_index"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(
        String(64),
        ForeignKey("evaluation_runs.run_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    dataset_item_id = Column(
        String(64), ForeignKey("evaluation_dataset_items.item_id", ondelete="SET NULL"), index=True
    )
    item_index = Column(Integer, nullable=False)
    query_text = Column(Text, nullable=False)
    gold_chunk_ids = Column(JSON_VALUE)
    gold_answer = Column(Text)
    generated_answer = Column(Text)
    retrieved_chunks = Column(JSON_VALUE)
    metrics = Column(JSON_VALUE)
    created_at = Column(DateTime(timezone=True), default=utc_now_naive)
