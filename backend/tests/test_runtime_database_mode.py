import os
import tempfile

from sqlalchemy import create_engine, inspect, text

from app.core.db import Base, create_session_factory
from app.main import build_app_state_services, create_app_from_env
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


def test_create_app_from_env_sets_default_owner_id_from_env():
    db_fd, db_path = tempfile.mkstemp(suffix=".db")
    os.close(db_fd)
    try:
        app = create_app_from_env(
            {
                "DATABASE_URL": f"sqlite:///{db_path}",
                "DEFAULT_OWNER_ID": "7",
            }
        )

        assert app.state.default_owner_id == 7
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

        create_app_from_env({"DATABASE_URL": f"sqlite:///{db_path}"})

        columns = {column["name"] for column in inspect(engine).get_columns("documents")}
        assert {"scope", "document_type", "product", "priority", "acl_roles"}.issubset(columns)
    finally:
        os.remove(db_path)
