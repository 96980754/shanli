from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.db import Base


class Role(Base):
    __tablename__ = "roles"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    level: Mapped[int] = mapped_column(Integer)

    users: Mapped[list["User"]] = relationship(back_populates="role")


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    role_id: Mapped[int] = mapped_column(ForeignKey("roles.id"))

    role: Mapped[Role] = relationship(back_populates="users")
    knowledge_bases: Mapped[list["KnowledgeBase"]] = relationship(back_populates="owner")


class KnowledgeBasePermission(Base):
    __tablename__ = "kb_permissions"
    __table_args__ = (UniqueConstraint("kb_id", "user_id", name="uq_kb_permissions_kb_id_user_id"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    kb_id: Mapped[int] = mapped_column(ForeignKey("knowledge_bases.id"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    can_view: Mapped[bool] = mapped_column(Boolean, default=False)
    can_upload: Mapped[bool] = mapped_column(Boolean, default=False)
    can_delete: Mapped[bool] = mapped_column(Boolean, default=False)
    can_grant: Mapped[bool] = mapped_column(Boolean, default=False)

    kb: Mapped["KnowledgeBase"] = relationship()
    user: Mapped[User] = relationship()


class KnowledgeViewRule(Base):
    __tablename__ = "kb_knowledge_view_rules"
    __table_args__ = (
        UniqueConstraint("kb_id", "user_id", name="uq_kb_knowledge_view_rules_kb_user"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    kb_id: Mapped[int] = mapped_column(ForeignKey("knowledge_bases.id"))
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    allowed_departments: Mapped[str] = mapped_column(Text, default="[]")
    allowed_product_lines: Mapped[str] = mapped_column(Text, default="[]")
    allowed_visibilities: Mapped[str] = mapped_column(Text, default="[]")
    max_security_level: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    kb: Mapped["KnowledgeBase"] = relationship()
    user: Mapped[User] = relationship()
