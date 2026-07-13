from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker


class Base(DeclarativeBase):
    pass


_DOCUMENT_V2_COLUMNS = {
    "scope": "VARCHAR(1) NOT NULL DEFAULT 'I'",
    "document_type": "VARCHAR(16) NOT NULL DEFAULT 'OTH'",
    "product": "VARCHAR(16) NOT NULL DEFAULT 'GEN'",
    "priority": "VARCHAR(2) NOT NULL DEFAULT 'P2'",
    "acl_roles": "TEXT NOT NULL DEFAULT '[]'",
}


def ensure_runtime_schema(engine) -> None:
    inspector = inspect(engine)
    if "documents" not in inspector.get_table_names():
        return
    existing = {column["name"] for column in inspector.get_columns("documents")}
    missing = [(name, ddl) for name, ddl in _DOCUMENT_V2_COLUMNS.items() if name not in existing]
    if not missing:
        return
    if engine.dialect.name not in {"sqlite", "postgresql"}:
        raise RuntimeError(f"Unsupported runtime schema migration dialect: {engine.dialect.name}")
    with engine.begin() as connection:
        for name, ddl in missing:
            connection.execute(text(f"ALTER TABLE documents ADD COLUMN {name} {ddl}"))


def create_session_factory(database_url: str):
    engine = create_engine(database_url)
    ensure_runtime_schema(engine)
    return sessionmaker(bind=engine)
