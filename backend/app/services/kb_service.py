from __future__ import annotations

from typing import Any
from uuid import uuid4


class InMemoryKnowledgeBaseService:
    """In-memory implementation used by the MVP skeleton and tests."""

    def __init__(self) -> None:
        self._items: dict[str, dict[str, Any]] = {}
        self._usernames: dict[str, str] = {"admin": "admin"}

    def create(
        self,
        name: str,
        description: str = "",
        visibility: str = "department",
        owner_id: str | int | None = None,
    ) -> dict[str, Any]:
        kb_id = str(uuid4())
        permissions: dict[str, dict[str, bool]] = {}
        if owner_id is not None:
            permissions[str(owner_id)] = {
                "can_view": True,
                "can_upload": True,
                "can_delete": True,
                "can_grant": True,
            }
        item = {
            "id": kb_id,
            "name": name,
            "description": description,
            "visibility": visibility,
            "doc_count": 0,
            "permissions": permissions,
        }
        self._items[kb_id] = item
        return item

    def list(self) -> list[dict[str, Any]]:
        return list(self._items.values())

    def list_for_user(self, user_id: str) -> list[dict[str, Any]]:
        normalized_user_id = str(user_id)
        return [
            item
            for item in self._items.values()
            if item.get("permissions", {}).get(normalized_user_id, {}).get("can_view") is True
        ]

    def get(self, kb_id: str) -> dict[str, Any] | None:
        return self._items.get(kb_id)

    def update(
        self,
        kb_id: str,
        name: str,
        description: str = "",
        visibility: str = "department",
    ) -> dict[str, Any] | None:
        item = self.get(kb_id)
        if item is None:
            return None
        item.update({"name": name, "description": description, "visibility": visibility})
        return item

    def delete(self, kb_id: str) -> dict[str, Any] | None:
        return self._items.pop(kb_id, None)

    def increment_doc_count(self, kb_id: str) -> None:
        item = self.get(kb_id)
        if item is not None:
            item["doc_count"] += 1

    def list_permissions(self, kb_id: str) -> list[dict[str, Any]] | None:
        item = self.get(kb_id)
        if item is None:
            return None
        permissions = item.get("permissions", {})
        return [
            {
                "user_id": user_id,
                "username": self._usernames.get(user_id, user_id),
                **flags,
            }
            for user_id, flags in permissions.items()
        ]

    def set_permission(self, kb_id: str, user_id: str, permission: dict[str, bool]) -> dict[str, Any] | None:
        item = self.get(kb_id)
        if item is None:
            return None
        normalized_user_id = str(user_id)
        item.setdefault("permissions", {})[normalized_user_id] = {
            "can_view": permission["can_view"],
            "can_upload": permission["can_upload"],
            "can_delete": permission["can_delete"],
            "can_grant": permission["can_grant"],
        }
        self._usernames.setdefault(normalized_user_id, normalized_user_id)
        return {
            "user_id": normalized_user_id,
            "username": self._usernames[normalized_user_id],
            **item["permissions"][normalized_user_id],
        }

    def delete_permission(self, kb_id: str, user_id: str) -> dict[str, Any] | None:
        item = self.get(kb_id)
        if item is None:
            return None
        permissions = item.setdefault("permissions", {})
        return permissions.pop(str(user_id), None)

    def has_permission(self, kb_id: str, user_id: str, permission: str) -> bool:
        item = self.get(kb_id)
        if item is None:
            return False
        return item.get("permissions", {}).get(str(user_id), {}).get(permission, False) is True
