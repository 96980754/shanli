from __future__ import annotations

import pytest

from yuxi.storage.postgres.manager import PostgresManager


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
    assert "processing_stage VARCHAR(64)" in statements
    assert "processing_progress INTEGER NOT NULL DEFAULT 0" in statements
    assert "ck_knowledge_files_processing_progress" in statements
    assert "replacement_target_file_id VARCHAR(64)" in statements
    assert "previous_version_id VARCHAR(64)" in statements
    assert "is_active BOOLEAN NOT NULL DEFAULT TRUE" in statements
    assert "superseded_at TIMESTAMPTZ" in statements
    assert "processing_task_lease_expires_at TIMESTAMPTZ" in statements
    assert "processing_task_updated_at TIMESTAMPTZ" in statements
    assert "processing_task_attempt INTEGER NOT NULL DEFAULT 0" in statements
    assert "normalized_name VARCHAR(512)" in statements
    assert "uq_knowledge_folders_scope_name" in statements
    assert "COALESCE(parent_id, '')" in statements
    assert "WHERE is_folder IS TRUE AND normalized_name IS NOT NULL" in statements
    assert "parse_metadata JSONB" in statements
    assert "original_markdown_file VARCHAR(1024)" in statements
    assert "cleaning_draft_file VARCHAR(1024)" in statements
    assert "cleaning_metadata JSONB" in statements
    assert "CREATE TABLE IF NOT EXISTS knowledge_assertions" in statements
    assert "CREATE TABLE IF NOT EXISTS entity_link_candidates" in statements
    assert "CREATE TABLE IF NOT EXISTS knowledge_conflicts" in statements
    assert "incoming_assertion_id VARCHAR(64) NOT NULL UNIQUE" in statements
    assert "publish_status VARCHAR(32) NOT NULL DEFAULT 'not_requested'" in statements
    assert "ix_knowledge_conflicts_kb_status" in statements
    assert "CREATE TABLE IF NOT EXISTS knowledge_conflict_publish_tasks" in statements
    assert "UNIQUE (conflict_id, expected_version)" in statements
    assert "neo4j_status VARCHAR(32) NOT NULL DEFAULT 'pending'" in statements
    assert "vector_status VARCHAR(32) NOT NULL DEFAULT 'pending'" in statements
    assert "lease_expires_at TIMESTAMPTZ" in statements
    assert "ix_knowledge_conflict_publish_tasks_status_retry" in statements
    assert "cleaning_version INTEGER NOT NULL DEFAULT 0" in statements
    assert "confirmed_at TIMESTAMPTZ" in statements
    assert "confirmed_by VARCHAR(64)" in statements
    assert "enrichment_data JSONB" in statements
    assert "enrichment_status VARCHAR(32)" in statements
    assert "enrichment_version INTEGER NOT NULL DEFAULT 0" in statements
    assert "enrichment_content_hash VARCHAR(64)" in statements
    assert "enrichment_generated_at TIMESTAMPTZ" in statements
    assert "enrichment_error TEXT" in statements
    assert "enrichment_possibly_outdated BOOLEAN NOT NULL DEFAULT FALSE" in statements
    assert "ix_knowledge_files_enrichment_status" in statements
    assert "source_metadata JSONB" in statements
    assert "CREATE TABLE IF NOT EXISTS document_qa_pairs" in statements
    assert "source_chunk_ids JSONB NOT NULL" in statements
    assert "evidence JSONB NOT NULL" in statements
    assert "sync_status VARCHAR(32) NOT NULL DEFAULT 'pending'" in statements
    assert "possibly_outdated BOOLEAN NOT NULL DEFAULT FALSE" in statements
    assert "uq_document_qa_pairs_file_content_question" in statements
    assert "ix_document_qa_pairs_file_id" in statements


def test_document_qa_model_has_unique_index_names():
    from yuxi.storage.postgres.models_knowledge import DocumentQAPair

    index_names = [index.name for index in DocumentQAPair.__table__.indexes]

    assert len(index_names) == len(set(index_names))


def test_knowledge_publish_task_model_has_unique_index_names():
    from yuxi.storage.postgres.models_knowledge import KnowledgeConflictPublishTask

    index_names = [index.name for index in KnowledgeConflictPublishTask.__table__.indexes]

    assert len(index_names) == len(set(index_names))
