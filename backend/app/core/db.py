from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker


class Base(DeclarativeBase):
    pass


_DOCUMENT_COLUMNS = {
    "scope": "VARCHAR(1) NOT NULL DEFAULT 'I'",
    "document_type": "VARCHAR(16) NOT NULL DEFAULT 'OTH'",
    "product": "VARCHAR(16) NOT NULL DEFAULT 'GEN'",
    "priority": "VARCHAR(2) NOT NULL DEFAULT 'P2'",
    "acl_roles": "TEXT NOT NULL DEFAULT '[]'",
    "storage_key": "VARCHAR(1024) NOT NULL DEFAULT ''",
    "original_filename": "VARCHAR(512) NOT NULL DEFAULT ''",
    "content_type": "VARCHAR(255) NOT NULL DEFAULT ''",
    "file_size": "INTEGER NOT NULL DEFAULT 0",
}

_AUDIT_LOG_COLUMNS = {
    "target_id": "VARCHAR(100) NOT NULL DEFAULT ''",
    "kb_id": "INTEGER",
    "detail": "TEXT NOT NULL DEFAULT ''",
    "created_at": "DATETIME",
}


def ensure_runtime_schema(engine) -> None:
    inspector = inspect(engine)
    table_columns = {
        "documents": _DOCUMENT_COLUMNS,
        "audit_log": _AUDIT_LOG_COLUMNS,
    }
    migrations = [
        (table_name, name, ddl)
        for table_name, columns in table_columns.items()
        if table_name in inspector.get_table_names()
        for name, ddl in columns.items()
        if name not in {column["name"] for column in inspector.get_columns(table_name)}
    ]
    if not migrations:
        return
    if engine.dialect.name not in {"sqlite", "postgresql"}:
        raise RuntimeError(f"Unsupported runtime schema migration dialect: {engine.dialect.name}")
    with engine.begin() as connection:
        for table_name, name, ddl in migrations:
            connection.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {name} {ddl}"))


def create_session_factory(database_url: str):
    engine = create_engine(database_url)
    ensure_runtime_schema(engine)
    return sessionmaker(bind=engine)
