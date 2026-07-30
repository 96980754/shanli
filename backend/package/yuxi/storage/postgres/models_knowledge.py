"""PostgreSQL 知识库模型 - KnowledgeBase、KnowledgeFile、评估相关表"""

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    column,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from yuxi.storage.postgres.models_business import Base
from yuxi.utils.datetime_utils import utc_now_naive

JSON_VALUE = JSON().with_variant(JSONB, "postgresql")
EXTRACTION_RESULT_VALUE = JSON(none_as_null=True).with_variant(
    JSONB(none_as_null=True),
    "postgresql",
)


class KnowledgeBaseCategory(Base):
    __tablename__ = "knowledge_base_categories"
    __table_args__ = (
        Index("uq_knowledge_base_categories_lower_name", func.lower(column("name")), unique=True),
        Index(
            "uq_knowledge_base_categories_default",
            "is_default",
            unique=True,
            postgresql_where=text("is_default IS TRUE"),
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(64), nullable=False)
    sort_order = Column(Integer, nullable=False, default=0)
    is_default = Column(Boolean, nullable=False, default=False)
    is_protected = Column(Boolean, nullable=False, default=False)
    created_by = Column(String(64))
    updated_by = Column(String(64))
    created_at = Column(DateTime(timezone=True), default=utc_now_naive)
    updated_at = Column(DateTime(timezone=True), default=utc_now_naive, onupdate=utc_now_naive)


class KnowledgeBase(Base):
    """知识库模型"""

    __tablename__ = "knowledge_bases"
    __table_args__ = (UniqueConstraint("kb_id", name="uq_knowledge_bases_kb_id"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    kb_id = Column(String(80), unique=True, nullable=False, index=True)
    name = Column(String(255), nullable=False, index=True)
    description = Column(Text)
    kb_type = Column(String(32), nullable=False, index=True)
    category_id = Column(
        Integer,
        ForeignKey("knowledge_base_categories.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
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
        UniqueConstraint(
            "kb_id",
            "logical_document_id",
            "document_version",
            name="uq_knowledge_files_document_version",
        ),
        Index("ix_knowledge_files_logical_document_id", "logical_document_id"),
        Index(
            "uq_knowledge_files_current_version",
            "kb_id",
            "logical_document_id",
            unique=True,
            postgresql_where=text("is_current IS TRUE AND is_folder IS NOT TRUE"),
            sqlite_where=text("is_current IS TRUE AND is_folder IS NOT TRUE"),
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    file_id = Column(String(64), unique=True, nullable=False, index=True)
    kb_id = Column(String(80), ForeignKey("knowledge_bases.kb_id", ondelete="CASCADE"), nullable=False, index=True)
    parent_id = Column(String(64), ForeignKey("knowledge_files.file_id", ondelete="SET NULL"), index=True)
    logical_document_id = Column(String(64))
    document_version = Column(Integer)
    is_current = Column(Boolean, nullable=False, default=True)
    supersedes_file_id = Column(String(64), ForeignKey("knowledge_files.file_id", ondelete="SET NULL"), index=True)
    activated_at = Column(DateTime(timezone=True))
    filename = Column(String(512), nullable=False)
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
    view_count = Column(BigInteger, default=0, nullable=False)
    content_type = Column(String(64))
    processing_params = Column(JSON_VALUE)
    is_folder = Column(Boolean, default=False)
    error_message = Column(Text)
    created_by = Column(String(64))
    updated_by = Column(String(64))
    created_at = Column(DateTime(timezone=True), default=utc_now_naive)
    updated_at = Column(DateTime(timezone=True), default=utc_now_naive, onupdate=utc_now_naive)


class KnowledgeConflict(Base):
    """同一逻辑文档相邻版本之间的结构化知识冲突。"""

    __tablename__ = "knowledge_conflicts"
    __table_args__ = (
        UniqueConstraint("new_file_id", "conflict_type", "conflict_key", name="uq_knowledge_conflicts_candidate"),
        Index("ix_knowledge_conflicts_kb_id", "kb_id"),
        Index("ix_knowledge_conflicts_logical_document_id", "logical_document_id"),
        Index("ix_knowledge_conflicts_new_file_id", "new_file_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    conflict_id = Column(String(64), unique=True, nullable=False, index=True)
    kb_id = Column(String(80), ForeignKey("knowledge_bases.kb_id", ondelete="CASCADE"), nullable=False)
    logical_document_id = Column(String(64), nullable=False)
    old_file_id = Column(String(64), ForeignKey("knowledge_files.file_id", ondelete="SET NULL"))
    new_file_id = Column(String(64), ForeignKey("knowledge_files.file_id", ondelete="CASCADE"), nullable=False)
    conflict_type = Column(String(64), nullable=False)
    conflict_key = Column(String(512), nullable=False)
    old_fact = Column(JSON_VALUE, nullable=False)
    new_fact = Column(JSON_VALUE, nullable=False)
    status = Column(String(32), nullable=False, default="open", index=True)
    resolved_by = Column(String(64))
    resolved_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), default=utc_now_naive)


class KnowledgeValidationReport(Base):
    """候选文档的持久化知识变更验证报告。"""

    __tablename__ = "knowledge_validation_reports"
    __table_args__ = (
        UniqueConstraint("candidate_file_id", name="uq_knowledge_validation_reports_candidate"),
        Index("ix_knowledge_validation_reports_kb_id", "kb_id"),
        Index("ix_knowledge_validation_reports_logical_document_id", "logical_document_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    report_id = Column(String(64), unique=True, nullable=False, index=True)
    kb_id = Column(String(80), ForeignKey("knowledge_bases.kb_id", ondelete="CASCADE"), nullable=False)
    logical_document_id = Column(String(64), nullable=False)
    old_file_id = Column(String(64), nullable=False)
    old_filename = Column(String(512))
    old_document_version = Column(Integer)
    candidate_file_id = Column(String(64), nullable=False)
    candidate_filename = Column(String(512))
    candidate_document_version = Column(Integer)
    ontology_registry_id = Column(String(128))
    ontology_version = Column(String(64))
    ontology_digest = Column(String(128))
    extraction_schema_version = Column(Integer)
    status = Column(String(32), nullable=False, default="processing", index=True)
    decision = Column(String(32), nullable=False, default="pending")
    new_count = Column(Integer, nullable=False, default=0)
    changed_count = Column(Integer, nullable=False, default=0)
    removed_count = Column(Integer, nullable=False, default=0)
    conflict_count = Column(Integer, nullable=False, default=0)
    inconclusive = Column(Boolean, nullable=False, default=False)
    summary = Column(JSON_VALUE)
    failure_message = Column(Text)
    reviewed_by = Column(String(64))
    reviewed_at = Column(DateTime(timezone=True))
    completed_at = Column(DateTime(timezone=True))
    published_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), default=utc_now_naive)
    updated_at = Column(DateTime(timezone=True), default=utc_now_naive, onupdate=utc_now_naive)


class KnowledgeValidationItem(Base):
    """验证报告中的单条知识变更及其新旧证据快照。"""

    __tablename__ = "knowledge_validation_items"
    __table_args__ = (
        UniqueConstraint("report_id", "item_index", name="uq_knowledge_validation_items_report_index"),
        Index("ix_knowledge_validation_items_report_id", "report_id"),
        Index("ix_knowledge_validation_items_change_type", "change_type"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    item_id = Column(String(64), unique=True, nullable=False, index=True)
    report_id = Column(
        String(64),
        ForeignKey("knowledge_validation_reports.report_id", ondelete="CASCADE"),
        nullable=False,
    )
    item_index = Column(Integer, nullable=False)
    change_type = Column(String(32), nullable=False)
    severity = Column(String(32), nullable=False)
    decision = Column(String(32), nullable=False, default="pending")
    fact_key = Column(String(512), nullable=False)
    relation = Column(String(256))
    old_fact = Column(JSON_VALUE)
    new_fact = Column(JSON_VALUE)
    old_evidence = Column(JSON_VALUE)
    new_evidence = Column(JSON_VALUE)
    review_required = Column(Boolean, nullable=False, default=False)
    reason = Column(Text)
    created_at = Column(DateTime(timezone=True), default=utc_now_naive)


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
    extraction_result = Column(EXTRACTION_RESULT_VALUE)
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
