import os
import tempfile

from sqlalchemy import create_engine, inspect, text

from app.core.db import Base, create_session_factory
from app.main import build_app_state_services, create_app_from_env, resolve_session_user_id
from app.models import Role, User


def test_create_session_factory_uses_database_url():
    db_fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(db_fd)
    try:
        database_url = f"sqlite:///{db_path}"
        session_factory = create_session_factory(database_url)
        session = session_factory()
        Base.metadata.create_all(session.bind)
        assert session.bind.url.database.endswith(".db")
    finally:
        os.remove(db_path)


def test_build_services_in_database_mode_without_explicit_session_uses_factory_session():
    db_fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(db_fd)
    try:
        database_url = f"sqlite:///{db_path}"
        session_factory = create_session_factory(database_url)
        session = session_factory()
        Base.metadata.create_all(session.bind)
        role = Role(name="管理员", level=3)
        user = User(username="admin", password_hash="hash", role=role)
        session.add_all([role, user])
        session.commit()

        services = build_app_state_services(mode="database", session_factory=session_factory)
        kb = services["kb_service"].create(name="运行时知识库", owner_id=user.id)

        assert services["mode"] == "database"
        assert kb.name == "运行时知识库"
    finally:
        os.remove(db_path)


def test_create_app_from_env_uses_memory_mode_without_database_url():
    app = create_app_from_env({})

    assert app.state.service_mode == "memory"


def test_create_app_from_env_uses_database_mode_when_database_url_exists():
    db_fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(db_fd)
    try:
        app = create_app_from_env({"DATABASE_URL": f"sqlite:///{db_path}"})

        assert app.state.service_mode == "database"
        assert app.state.db_session.bind.url.database == db_path
    finally:
        os.remove(db_path)


def test_create_app_from_env_sets_file_storage_root_from_env():
    db_fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(db_fd)
    try:
        app = create_app_from_env(
            {
                "DATABASE_URL": f"sqlite:///{db_path}",
                "FILE_STORAGE_ROOT": "/tmp/acceptance-files",
            }
        )

        assert str(app.state.file_storage_root) == "/tmp/acceptance-files"
    finally:
        os.remove(db_path)

def test_database_mode_does_not_fallback_to_default_owner_for_non_numeric_admin_session():
    app = create_app_from_env({})
    app.state.service_mode = "database"
    app.state.default_owner_id = 1

    assert resolve_session_user_id(app, {"user_id": "admin", "username": "admin"}) == "admin"


    db_fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(db_fd)
    try:
        engine = create_engine(f"sqlite:///{db_path}")
        with engine.begin() as connection:
            connection.execute(text("""
                CREATE TABLE documents (
                    id INTEGER PRIMARY KEY,
                    kb_id INTEGER,
                    title VARCHAR(512),
                    file_type VARCHAR(16),
                    status VARCHAR(16),
                    department VARCHAR(100),
                    product_line VARCHAR(100),
                    visibility VARCHAR(30),
                    security_level INTEGER,
                    tags TEXT
                )
            """))
            connection.execute(text("""
                INSERT INTO documents (id, kb_id, title, file_type, status, department, product_line, visibility, security_level, tags)
                VALUES
                    (1, 1, 'restricted.pdf', 'pdf', 'parsed', '', 'MCSTARS', 'restricted', 3, ''),
                    (2, 1, 'public.pdf', 'pdf', 'parsed', '', 'MINISERVER', 'public', 1, ''),
                    (3, 1, 'internal.pdf', 'pdf', 'parsed', '', 'POCSTARS-MNO', 'internal', 2, ''),
                    (4, 1, 'unknown.pdf', 'pdf', 'parsed', '', 'Unknown', '', 1, '')
            """))

        create_app_from_env({"DATABASE_URL": f"sqlite:///{db_path}"})
        create_app_from_env({"DATABASE_URL": f"sqlite:///{db_path}"})

        rows = engine.connect().execute(text("SELECT id, scope, product FROM documents ORDER BY id")).all()
        assert rows == [(1, "R", "MC"), (2, "C", "MS"), (3, "I", "MNO"), (4, "I", "GEN")]
    finally:
        os.remove(db_path)


def test_runtime_database_mode_adds_v2_document_columns_to_existing_schema():
    db_fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(db_fd)
    try:
        engine = create_engine(f"sqlite:///{db_path}")
        with engine.begin() as connection:
            connection.execute(text("""
                CREATE TABLE documents (
                    id INTEGER PRIMARY KEY,
                    kb_id INTEGER NOT NULL,
                    title VARCHAR(512) NOT NULL,
                    file_type VARCHAR(16) NOT NULL,
                    status VARCHAR(16),
                    department VARCHAR(100),
                    product_line VARCHAR(100),
                    visibility VARCHAR(30),
                    security_level INTEGER,
                    tags TEXT
                )
            """))
            connection.execute(text("""
                CREATE TABLE audit_log (
                    id INTEGER PRIMARY KEY,
                    user_id INTEGER NOT NULL,
                    action VARCHAR(100) NOT NULL,
                    target_type VARCHAR(50)
                )
            """))

        create_app_from_env({"DATABASE_URL": f"sqlite:///{db_path}"})
        create_app_from_env({"DATABASE_URL": f"sqlite:///{db_path}"})

        document_columns = {column["name"] for column in inspect(engine).get_columns("documents")}
        audit_columns = {column["name"] for column in inspect(engine).get_columns("audit_log")}
        assert {
            "scope", "document_type", "product", "priority", "acl_roles",
            "storage_key", "original_filename", "content_type", "file_size",
        }.issubset(document_columns)
        assert {"target_id", "kb_id", "detail", "created_at"}.issubset(audit_columns)
    finally:
        os.remove(db_path)
