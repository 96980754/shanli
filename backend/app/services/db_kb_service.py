from __future__ import annotations

from sqlalchemy.orm import Session

from app.models import KnowledgeBase, KnowledgeBasePermission


class DbKnowledgeBaseService:
    """Database-backed knowledge base service."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        name: str,
        owner_id: int,
        description: str = "",
        visibility: str = "department",
    ) -> KnowledgeBase:
        kb = KnowledgeBase(
            name=name,
            description=description,
            visibility=visibility,
            doc_count=0,
            owner_id=owner_id,
        )
        permission = KnowledgeBasePermission(
            kb=kb,
            user_id=owner_id,
            can_view=True,
            can_upload=True,
            can_delete=True,
            can_grant=True,
        )
        try:
            self.session.add_all([kb, permission])
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
        self.session.refresh(kb)

        return kb

    def list(self) -> list[KnowledgeBase]:
        return self.session.query(KnowledgeBase).order_by(KnowledgeBase.id).all()

    def list_for_user(self, user_id: int) -> list[KnowledgeBase]:
        return (
            self.session.query(KnowledgeBase)
            .join(KnowledgeBasePermission, KnowledgeBasePermission.kb_id == KnowledgeBase.id)
            .filter(KnowledgeBasePermission.user_id == user_id, KnowledgeBasePermission.can_view.is_(True))
            .order_by(KnowledgeBase.id)
            .all()
        )

    def get(self, kb_id: int) -> KnowledgeBase | None:
        return self.session.get(KnowledgeBase, kb_id)

    def update(
        self,
        kb_id: int,
        name: str,
        description: str = "",
        visibility: str = "department",
    ) -> KnowledgeBase | None:
        kb = self.get(kb_id)
        if kb is None:
            return None
        kb.name = name
        kb.description = description
        kb.visibility = visibility
        self.session.commit()
        self.session.refresh(kb)
        return kb

    def delete(self, kb_id: int) -> KnowledgeBase | None:
        kb = self.get(kb_id)
        if kb is None:
            return None
        self.session.delete(kb)
        self.session.commit()
        return kb

    def increment_doc_count(self, kb_id: int) -> None:
        kb = self.get(kb_id)
        if kb is None:
            return
        kb.doc_count += 1
        self.session.commit()

    def list_permissions(self, kb_id: int) -> list[KnowledgeBasePermission]:
        return (
            self.session.query(KnowledgeBasePermission)
            .filter(KnowledgeBasePermission.kb_id == kb_id)
            .order_by(KnowledgeBasePermission.user_id)
            .all()
        )

    def set_permission(self, kb_id: int, user_id: int, payload: dict[str, bool]) -> KnowledgeBasePermission | None:
        kb = self.get(kb_id)
        if kb is None:
            return None
        permission = (
            self.session.query(KnowledgeBasePermission)
            .filter(KnowledgeBasePermission.kb_id == kb_id, KnowledgeBasePermission.user_id == user_id)
            .one_or_none()
        )
        if permission is None:
            permission = KnowledgeBasePermission(kb_id=kb_id, user_id=user_id)
            self.session.add(permission)
        permission.can_view = payload["can_view"]
        permission.can_upload = payload["can_upload"]
        permission.can_delete = payload["can_delete"]
        permission.can_grant = payload["can_grant"]
        self.session.commit()
        self.session.refresh(permission)
        return permission

    def delete_permission(self, kb_id: int, user_id: int) -> KnowledgeBasePermission | None:
        permission = (
            self.session.query(KnowledgeBasePermission)
            .filter(KnowledgeBasePermission.kb_id == kb_id, KnowledgeBasePermission.user_id == user_id)
            .one_or_none()
        )
        if permission is None:
            return None
        self.session.delete(permission)
        self.session.commit()
        return permission

    def has_permission(self, kb_id: int, user_id: int, permission: str) -> bool:
        record = (
            self.session.query(KnowledgeBasePermission)
            .filter(KnowledgeBasePermission.kb_id == kb_id, KnowledgeBasePermission.user_id == user_id)
            .one_or_none()
        )
        if record is None:
            return False
        return bool(getattr(record, permission, False))
