from __future__ import annotations

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    kb_id: Mapped[int] = mapped_column(ForeignKey("knowledge_bases.id"))
    title: Mapped[str] = mapped_column(String(512))
    file_type: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default="pending")
    department: Mapped[str] = mapped_column(String(100), default="")
    product_line: Mapped[str] = mapped_column(String(100), default="")
    visibility: Mapped[str] = mapped_column(String(30), default="internal")
    security_level: Mapped[int] = mapped_column(Integer, default=1)
    tags: Mapped[str] = mapped_column(Text, default="")
    scope: Mapped[str] = mapped_column(String(1), default="I")
    document_type: Mapped[str] = mapped_column(String(16), default="OTH")
    product: Mapped[str] = mapped_column(String(16), default="GEN")
    priority: Mapped[str] = mapped_column(String(2), default="P2")
    storage_key: Mapped[str] = mapped_column(String(1024), default="")
    original_filename: Mapped[str] = mapped_column(String(512), default="")
    content_type: Mapped[str] = mapped_column(String(255), default="")
    file_size: Mapped[int] = mapped_column(Integer, default=0)
    acl_roles: Mapped[str] = mapped_column(Text, default="[]")
    storage_key: Mapped[str] = mapped_column(String(1024), default="")
    original_filename: Mapped[str] = mapped_column(String(512), default="")
    content_type: Mapped[str] = mapped_column(String(255), default="")
    file_size: Mapped[int] = mapped_column(Integer, default=0)

    kb: Mapped["KnowledgeBase"] = relationship(back_populates="documents")
    chunks: Mapped[list["DocumentChunk"]] = relationship(back_populates="document")


class DocumentChunk(Base):
    __tablename__ = "document_chunks"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    document_id: Mapped[int] = mapped_column(ForeignKey("documents.id"))
    chunk_index: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)

    document: Mapped[Document] = relationship(back_populates="chunks")
