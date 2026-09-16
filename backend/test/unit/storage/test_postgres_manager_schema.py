from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import Integer
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateIndex

from yuxi.storage.postgres.manager import PostgresManager
from yuxi.storage.postgres.models_business import MessageFeedback
from yuxi.storage.postgres.models_knowledge import KnowledgeBaseCategory


def test_category_name_index_references_name_column():
    index = next(
        index
        for index in KnowledgeBaseCategory.__table__.indexes
        if index.name == "uq_knowledge_base_categories_lower_name"
    )

    sql = str(CreateIndex(index).compile(dialect=postgresql.dialect()))

    assert "UNIQUE INDEX" in sql
    assert "lower(name)" in sql
    assert "lower('name')" not in sql


class _RecordingConnection:
    def __init__(self):
        self.statements: list[str] = []

    async def execute(self, statement):
        self.statements.append(str(statement))


class _RecordingBegin:
    def __init__(self, connection: _RecordingConnection):
        self.connection = connection

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _RecordingEngine:
    def __init__(self, connection: _RecordingConnection):
        self.connection = connection

    def begin(self):
        return _RecordingBegin(self.connection)


def test_message_feedback_has_user_message_unique_constraint():
    constraint = next(
        constraint
        for constraint in MessageFeedback.__table__.constraints
        if constraint.name == "uq_message_feedback_message_uid"
    )

    assert [column.name for column in constraint.columns] == ["message_id", "uid"]


def test_udesk_models_unique_keys_and_candidate_review_defaults():
    from yuxi.storage.postgres.models_curated_qa import CuratedQAPair
    from yuxi.storage.postgres.models_udesk import CuratedQACandidate, UdeskConversation, UdeskMessage

    conv_constraint = next(
        constraint
        for constraint in UdeskConversation.__table__.constraints
        if constraint.name == "uq_udesk_conversations_conversation_id"
    )
    assert [column.name for column in conv_constraint.columns] == ["conversation_id"]

    msg_constraint = next(
        constraint
        for constraint in UdeskMessage.__table__.constraints
        if constraint.name == "uq_udesk_messages_message_id"
    )
    assert [column.name for column in msg_constraint.columns] == ["message_id"]

    # 候选知识幂等键：同一会话 + 同一问题只允许一条（增量重拉由唯一键兜底）
    candidate_constraint = next(
        constraint
        for constraint in CuratedQACandidate.__table__.constraints
        if constraint.name == "uq_curated_qa_candidates_conv_question"
    )
    assert [column.name for column in candidate_constraint.columns] == [
        "source_conversation_id",
        "question_hash",
    ]
    # 候选默认待审 + 待查重（C6：审核前不落启用问答对）
    assert CuratedQACandidate.__table__.c.review_status.default.arg == "pending"
    assert CuratedQACandidate.__table__.c.dedup_status.default.arg == "pending"
    # 问答对可溯源 Udesk 会话
    assert "source_conversation_id" in CuratedQAPair.__table__.c


@pytest.mark.asyncio
async def test_ensure_business_schema_cleans_duplicate_feedback_before_unique_constraint():
    manager = PostgresManager()
    original_initialized = manager._initialized
    original_engine = manager.async_engine
    connection = _RecordingConnection()

    manager._initialized = True
    manager.async_engine = _RecordingEngine(connection)
    try:
        await manager.ensure_business_schema()
    finally:
        manager._initialized = original_initialized
        manager.async_engine = original_engine

    statements = "\n".join(connection.statements)

    assert "WITH ranked_feedbacks AS" in statements
    assert "ADD CONSTRAINT uq_message_feedback_message_uid UNIQUE (message_id, uid)" in statements
    assert statements.index("WITH ranked_feedbacks AS") < statements.index(
        "ADD CONSTRAINT uq_message_feedback_message_uid UNIQUE (message_id, uid)"
    )


@pytest.mark.asyncio
async def test_ensure_business_schema_backfills_subagent_thread_columns_before_dropping_legacy_columns():
    manager = PostgresManager()
    original_initialized = manager._initialized
    original_engine = manager.async_engine
    connection = _RecordingConnection()

    manager._initialized = True
    manager.async_engine = _RecordingEngine(connection)
    try:
        await manager.ensure_business_schema()
    finally:
        manager._initialized = original_initialized
        manager.async_engine = original_engine

    statements = "\n".join(connection.statements)

    assert "SET agent_slug = agent_id" in statements
    assert "SET conversation_thread_id = thread_id" in statements
    assert "SET created_by_run_id = COALESCE(parent_agent_run_id, parent_run_id)" in statements
    assert "SET subagent_slug = c.agent_id" in statements
    assert "SET created_by_run_id = created_by_parent_run_id::VARCHAR" in statements
    assert "ALTER COLUMN subagent_slug SET NOT NULL" in statements
    assert "ALTER COLUMN created_by_run_id SET NOT NULL" in statements
    assert statements.index("SET agent_slug = agent_id") < statements.index("DROP COLUMN IF EXISTS agent_id")
    assert statements.index("SET conversation_thread_id = thread_id") < statements.index(
        "DROP COLUMN IF EXISTS thread_id"
    )
    assert statements.index("COALESCE(parent_agent_run_id, parent_run_id)") < statements.index(
        "DROP COLUMN IF EXISTS parent_agent_run_id"
    )
    assert statements.index("created_by_parent_run_id") < statements.index(
        "DROP COLUMN IF EXISTS created_by_parent_run_id"
    )


@pytest.mark.asyncio
async def test_ensure_business_schema_cleans_duplicate_active_agent_runs_before_unique_index():
    manager = PostgresManager()
    original_initialized = manager._initialized
    original_engine = manager.async_engine
    connection = _RecordingConnection()

    manager._initialized = True
    manager.async_engine = _RecordingEngine(connection)
    try:
        await manager.ensure_business_schema()
    finally:
        manager._initialized = original_initialized
        manager.async_engine = original_engine

    statements = "\n".join(connection.statements)

    assert "WITH duplicated_active_runs AS" in statements
    assert "active_run_migration_conflict" in statements
    assert "CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_runs_one_active_per_thread" in statements
    assert statements.index("WITH duplicated_active_runs AS") < statements.index(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_runs_one_active_per_thread"
    )


@pytest.mark.asyncio
async def test_ensure_business_schema_creates_user_config_table():
    manager = PostgresManager()
    original_initialized = manager._initialized
    original_engine = manager.async_engine
    connection = _RecordingConnection()

    manager._initialized = True
    manager.async_engine = _RecordingEngine(connection)
    try:
        await manager.ensure_business_schema()
    finally:
        manager._initialized = original_initialized
        manager.async_engine = original_engine

    statements = "\n".join(connection.statements)

    assert "CREATE TABLE IF NOT EXISTS user_config" in statements
    assert "enable_memory BOOLEAN NOT NULL DEFAULT FALSE" in statements


@pytest.mark.asyncio
async def test_ensure_business_schema_removes_unbound_api_keys_before_requiring_user_id():
    manager = PostgresManager()
    original_initialized = manager._initialized
    original_engine = manager.async_engine
    connection = _RecordingConnection()

    manager._initialized = True
    manager.async_engine = _RecordingEngine(connection)
    try:
        await manager.ensure_business_schema()
    finally:
        manager._initialized = original_initialized
        manager.async_engine = original_engine

    statements = "\n".join(connection.statements)

    assert "UPDATE cli_auth_sessions" in statements
    assert "DELETE FROM api_keys WHERE user_id IS NULL" in statements
    assert "ALTER TABLE IF EXISTS api_keys ALTER COLUMN user_id SET NOT NULL" in statements
    assert statements.index("UPDATE cli_auth_sessions") < statements.index("DELETE FROM api_keys WHERE user_id IS NULL")
    assert statements.index("DELETE FROM api_keys WHERE user_id IS NULL") < statements.index(
        "ALTER TABLE IF EXISTS api_keys ALTER COLUMN user_id SET NOT NULL"
    )


@pytest.mark.asyncio
async def test_ensure_business_schema_creates_udesk_tables_in_dependency_order():
    manager = PostgresManager()
    original_initialized = manager._initialized
    original_engine = manager.async_engine
    connection = _RecordingConnection()

    manager._initialized = True
    manager.async_engine = _RecordingEngine(connection)
    try:
        await manager.ensure_business_schema()
    finally:
        manager._initialized = original_initialized
        manager.async_engine = original_engine

    statements = "\n".join(connection.statements)

    assert "CREATE TABLE IF NOT EXISTS udesk_conversations" in statements
    assert "CREATE TABLE IF NOT EXISTS udesk_messages" in statements
    assert "CREATE TABLE IF NOT EXISTS curated_qa_candidates" in statements
    assert "CREATE TABLE IF NOT EXISTS udesk_sync_state" in statements
    assert "CONSTRAINT uq_udesk_messages_message_id UNIQUE (message_id)" in statements
    assert "REFERENCES udesk_conversations(conversation_id) ON DELETE CASCADE" in statements
    assert "CONSTRAINT uq_curated_qa_candidates_conv_question" in statements
    assert "review_status VARCHAR(32) NOT NULL DEFAULT 'pending'" in statements
    # 会话表先于消息表（外键依赖）、候选表在最后；问答对溯源列幂等追加
    assert statements.index("CREATE TABLE IF NOT EXISTS udesk_conversations") < statements.index(
        "CREATE TABLE IF NOT EXISTS udesk_messages"
    )
    assert statements.index("CREATE TABLE IF NOT EXISTS udesk_messages") < statements.index(
        "CREATE TABLE IF NOT EXISTS curated_qa_candidates"
    )
    assert (
        "ALTER TABLE IF EXISTS curated_qa_pairs ADD COLUMN IF NOT EXISTS source_conversation_id VARCHAR(128)"
        in statements
    )
    # LLM 结构化完成标记：NULL = 待总结（筛选判定无价值的会话也会打标）
    assert (
        "ALTER TABLE IF EXISTS udesk_conversations ADD COLUMN IF NOT EXISTS summarized_at TIMESTAMPTZ"
        in statements
    )
    # 同步状态单行表：建表先于单行种子 INSERT（幂等，id 恒为 1）
    assert statements.index("CREATE TABLE IF NOT EXISTS udesk_sync_state") < statements.index(
        "INSERT INTO udesk_sync_state (id, last_run_conversations"
    )
    # 进度与总结运行状态：老库靠 ADD COLUMN IF NOT EXISTS 补列，不新建表
    for column_ddl in (
        "ALTER TABLE IF EXISTS udesk_sync_state ADD COLUMN IF NOT EXISTS progress_done INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE IF EXISTS udesk_sync_state ADD COLUMN IF NOT EXISTS progress_total INTEGER NOT NULL DEFAULT 0",
        "ALTER TABLE IF EXISTS udesk_sync_state ADD COLUMN IF NOT EXISTS summarize_lease_expires_at TIMESTAMPTZ",
        "ALTER TABLE IF EXISTS udesk_sync_state ADD COLUMN IF NOT EXISTS summarize_status VARCHAR(32)",
        "ALTER TABLE IF EXISTS udesk_sync_state ADD COLUMN IF NOT EXISTS summarize_last_error TEXT",
        "ALTER TABLE IF EXISTS udesk_sync_state ADD COLUMN IF NOT EXISTS summarize_last_run_at TIMESTAMPTZ",
    ):
        assert column_ddl in statements
    # 种子必须在这批 ADD COLUMN **之后**：第五批之前建的库还没有 progress_done，
    # 种子写在前面会以 UndefinedColumn 失败，而同批共用一个事务，那两条 ADD COLUMN
    # 会被一起回滚——迁移从此无法自行收敛（全新库因 create_all 建齐全部列而掩盖此问题）。
    assert statements.index(
        "ALTER TABLE IF EXISTS udesk_sync_state ADD COLUMN IF NOT EXISTS progress_done INTEGER NOT NULL DEFAULT 0"
    ) < statements.index("INSERT INTO udesk_sync_state (id, last_run_conversations")


def test_udesk_datetime_columns_are_timezone_aware():
    """D 的回归：库内实际类型是 TIMESTAMPTZ，模型若声明成 naive DateTime，
    SQLAlchemy 会按无时区绑定，Postgres 再按会话时区（Asia/Shanghai）解释，
    写进去的 UTC 时刻被当成北京时间 → 整体偏 -8 小时。
    """
    from yuxi.storage.postgres.models_udesk import (
        CuratedQACandidate,
        UdeskConversation,
        UdeskMessage,
        UdeskSyncState,
    )

    for model in (UdeskConversation, UdeskMessage, CuratedQACandidate, UdeskSyncState):
        for column in model.__table__.columns:
            if column.type.python_type is datetime:
                assert column.type.timezone is True, f"{model.__name__}.{column.name} 必须带时区"
                # 默认值/自动更新同样必须是 aware：naive 一律视为 bug。
                # ColumnDefault 会把可调用默认值包成接收执行上下文的签名，
                # 故按 SQLAlchemy 自己的调用方式传一个占位 ctx。
                for default in (column.default, column.onupdate):
                    if default is None or not callable(default.arg):
                        continue
                    value = default.arg(None)
                    assert value.tzinfo is not None, f"{model.__name__}.{column.name} 默认值不是 aware"


@pytest.mark.asyncio
async def test_ensure_knowledge_schema_backfills_document_versions_before_unique_indexes():
    manager = PostgresManager()
    original_initialized = manager._initialized
    original_engine = manager.async_engine
    connection = _RecordingConnection()

    manager._initialized = True
    manager.async_engine = _RecordingEngine(connection)
    try:
        await manager.ensure_knowledge_schema()
    finally:
        manager._initialized = original_initialized
        manager.async_engine = original_engine

    statements = "\n".join(connection.statements)

    assert "ADD COLUMN IF NOT EXISTS logical_document_id VARCHAR(64)" in statements
    assert "ADD COLUMN IF NOT EXISTS document_version INTEGER" in statements
    assert "ADD COLUMN IF NOT EXISTS is_current BOOLEAN NOT NULL DEFAULT TRUE" in statements
    assert "SET logical_document_id = file_id" in statements
    assert "CREATE TABLE IF NOT EXISTS knowledge_conflicts" in statements
    assert "CREATE UNIQUE INDEX IF NOT EXISTS uq_knowledge_files_document_version" in statements
    assert "CREATE UNIQUE INDEX IF NOT EXISTS uq_knowledge_files_current_version" in statements
    assert statements.index("SET logical_document_id = file_id") < statements.index(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_knowledge_files_document_version"
    )
    assert statements.index("SET logical_document_id = file_id") < statements.index(
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_knowledge_files_current_version"
    )


@pytest.mark.asyncio
async def test_ensure_knowledge_schema_creates_validation_report_tables():
    manager = PostgresManager()
    original_initialized = manager._initialized
    original_engine = manager.async_engine
    connection = _RecordingConnection()

    manager._initialized = True
    manager.async_engine = _RecordingEngine(connection)
    try:
        await manager.ensure_knowledge_schema()
    finally:
        manager._initialized = original_initialized
        manager.async_engine = original_engine

    statements = "\n".join(connection.statements)

    assert "CREATE TABLE IF NOT EXISTS knowledge_validation_reports" in statements
    assert "CREATE TABLE IF NOT EXISTS knowledge_validation_items" in statements
    assert "uq_knowledge_validation_reports_candidate UNIQUE (candidate_file_id)" in statements
    assert "REFERENCES knowledge_validation_reports(report_id) ON DELETE CASCADE" in statements
    assert "old_file_id VARCHAR(64) NOT NULL" in statements
    assert "candidate_file_id VARCHAR(64) NOT NULL" in statements
    assert "ix_knowledge_validation_reports_status" in statements
    assert "ix_knowledge_validation_items_change_type" in statements
    assert statements.index("CREATE TABLE IF NOT EXISTS knowledge_validation_reports") < statements.index(
        "CREATE TABLE IF NOT EXISTS knowledge_validation_items"
    )


@pytest.mark.asyncio
async def test_ensure_knowledge_schema_creates_enterprise_permission_table():
    manager = PostgresManager()
    original_initialized = manager._initialized
    original_engine = manager.async_engine
    connection = _RecordingConnection()

    manager._initialized = True
    manager.async_engine = _RecordingEngine(connection)
    try:
        await manager.ensure_knowledge_schema()
    finally:
        manager._initialized = original_initialized
        manager.async_engine = original_engine

    statements = "\n".join(connection.statements)

    assert "CREATE TABLE IF NOT EXISTS knowledge_base_permissions" in statements
    assert "subject_type VARCHAR(32) NOT NULL" in statements
    assert "subject_id VARCHAR(128) NOT NULL" in statements
    assert "can_search BOOLEAN NOT NULL DEFAULT FALSE" in statements
    assert "can_grant BOOLEAN NOT NULL DEFAULT FALSE" in statements
    assert "uq_knowledge_base_permissions_subject" in statements
    assert "ix_knowledge_base_permissions_kb_id" in statements


def _required_insert_columns(model) -> set[str]:
    """裸 SQL INSERT 必须显式提供的列：NOT NULL 且库侧无默认值。

    库侧默认值 = `server_default`，以及单列整型主键（create_all 会把它建成 SERIAL）。
    `Column(default=...)` 是 **Python 侧**默认值，只对 Core/ORM 插入生效，裸 text()
    拿不到——恰恰是这一类列最容易被漏掉。
    """
    pk_columns = list(model.__table__.primary_key.columns)
    serial_pk = pk_columns[0].name if len(pk_columns) == 1 and isinstance(pk_columns[0].type, Integer) else None
    return {
        column.name
        for column in model.__table__.columns
        if not column.nullable and column.server_default is None and column.name != serial_pk
    }


def _missing_insert_columns(insert_sql: str, model) -> list[str]:
    provided = {
        name.strip() for name in insert_sql[insert_sql.index("(") + 1 : insert_sql.index(")")].split(",")
    }
    return sorted(_required_insert_columns(model) - provided)


class _ScalarOnlyResult:
    def scalar(self):
        return True


class _RecordingSession:
    """只收语句、不碰真库，用于断言裸 SQL 的列清单。"""

    def __init__(self):
        self.sql = ""

    async def execute(self, statement, params=None):
        self.sql = str(statement)
        return _ScalarOnlyResult()


@pytest.mark.asyncio
async def test_udesk_raw_inserts_list_every_not_null_column_without_db_default():
    """回归：两处裸 SQL INSERT 都曾在生产上漏列炸 NotNullViolation。

    - `udesk_sync_state` 的单行种子只给 id → worker 启动即崩进 restart 循环；
    - `udesk_conversations` 的 upsert 漏 created_at → 整轮拉取失败。

    共同成因：表由 ORM 的 create_all 建出（手写 DDL 里的 DEFAULT 因表已存在而不生效），
    而 ORM 的 default 只是 Python 侧默认值，裸 text() 绕过它。走 pg_insert 的写入点
    （消息、候选）不受影响，故不在本用例范围内。
    """
    from yuxi.services.udesk.pull_service import UdeskPullService
    from yuxi.storage.postgres.models_udesk import UdeskConversation, UdeskSyncState

    manager = PostgresManager()
    original_initialized = manager._initialized
    original_engine = manager.async_engine
    connection = _RecordingConnection()
    manager._initialized = True
    manager.async_engine = _RecordingEngine(connection)
    try:
        await manager.ensure_business_schema()
    finally:
        manager._initialized = original_initialized
        manager.async_engine = original_engine

    seed_sql = next(s for s in connection.statements if s.startswith("INSERT INTO udesk_sync_state"))
    assert _missing_insert_columns(seed_sql, UdeskSyncState) == []

    # client / session_factory 在本用例里都用不到：只取语句文本，不触网不落库
    service = UdeskPullService(
        client=None,
        session_factory=lambda: None,
        now=lambda: datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    session = _RecordingSession()
    await service._upsert_conversation(session, {"conversation_id": "probe-conversation"})
    assert _missing_insert_columns(session.sql, UdeskConversation) == []


@pytest.mark.asyncio
async def test_ensure_knowledge_schema_backfills_categories_before_requiring_category_id():
    manager = PostgresManager()
    original_initialized = manager._initialized
    original_engine = manager.async_engine
    connection = _RecordingConnection()

    manager._initialized = True
    manager.async_engine = _RecordingEngine(connection)
    try:
        await manager.ensure_knowledge_schema()
    finally:
        manager._initialized = original_initialized
        manager.async_engine = original_engine

    statements = "\n".join(connection.statements)

    assert "CREATE TABLE IF NOT EXISTS knowledge_base_categories" in statements
    assert "pg_get_expr(index_info.indexprs, index_info.indrelid)" in statements
    assert "lower(''name''::text)" in statements
    assert "DROP INDEX uq_knowledge_base_categories_lower_name" in statements
    assert "ON knowledge_base_categories(lower(name))" in statements
    assert "uq_knowledge_base_categories_lower_name" in statements
    assert "uq_knowledge_base_categories_default" in statements
    assert "SELECT '其他', 10000, TRUE, TRUE" in statements
    assert "SET category_id = (SELECT id FROM knowledge_base_categories" in statements
    assert "FOREIGN KEY (category_id) REFERENCES knowledge_base_categories(id) ON DELETE RESTRICT" in statements
    assert "ALTER TABLE IF EXISTS knowledge_bases ALTER COLUMN category_id SET NOT NULL" in statements
    assert statements.index("pg_get_expr(index_info.indexprs, index_info.indrelid)") < statements.index(
        "ON knowledge_base_categories(lower(name))"
    )
    assert statements.index("SELECT '其他', 10000, TRUE, TRUE") < statements.index(
        "SET category_id = (SELECT id FROM knowledge_base_categories"
    )
    assert statements.index("SET category_id = (SELECT id FROM knowledge_base_categories") < statements.index(
        "ALTER TABLE IF EXISTS knowledge_bases ALTER COLUMN category_id SET NOT NULL"
    )
