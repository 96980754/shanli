"""Enterprise knowledge-base permission helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from yuxi.knowledge.manager import KnowledgeBaseManager

KNOWLEDGE_PERMISSION_ACTIONS = {
    "can_view",
    "can_search",
    "can_upload",
    "can_download",
    "can_delete",
    "can_manage",
    "can_grant",
    "can_export",
}


class KnowledgeBaseRepositoryProtocol(Protocol):
    async def get_by_kb_id(self, kb_id: str) -> Any | None: ...


class KnowledgePermissionRepositoryProtocol(Protocol):
    async def list_by_kb_id(self, kb_id: str) -> list[Any]: ...


@dataclass(frozen=True)
class EffectiveKnowledgePermissions:
    can_view: bool = False
    can_search: bool = False
    can_upload: bool = False
    can_download: bool = False
    can_delete: bool = False
    can_manage: bool = False
    can_grant: bool = False
    can_export: bool = False

    def allows(self, action: str) -> bool:
        if action not in KNOWLEDGE_PERMISSION_ACTIONS:
            raise ValueError(f"Unsupported knowledge permission action: {action}")
        return bool(getattr(self, action))


class KnowledgePermissionService:
    """Resolve single-enterprise knowledge-base permissions.

    This service intentionally models private-deployment authorization, not SaaS
    tenant isolation. Permissions are additive: user, department, and role grants
    are merged; there is no deny rule in the first production-oriented slice.
    """

    def __init__(
        self,
        *,
        kb_repository: KnowledgeBaseRepositoryProtocol | None = None,
        permission_repository: KnowledgePermissionRepositoryProtocol | None = None,
    ) -> None:
        if kb_repository is None:
            from yuxi.repositories.knowledge_base_repository import KnowledgeBaseRepository

            kb_repository = KnowledgeBaseRepository()
        if permission_repository is None:
            from yuxi.repositories.knowledge_permission_repository import KnowledgePermissionRepository

            permission_repository = KnowledgePermissionRepository()
        self.kb_repository = kb_repository
        self.permission_repository = permission_repository

    async def has_permission(self, user: dict[str, Any], kb_id: str, action: str) -> bool:
        return (await self.effective_permissions(user, kb_id)).allows(action)

    async def effective_permissions(self, user: dict[str, Any], kb_id: str) -> EffectiveKnowledgePermissions:
        if user.get("role") == "superadmin":
            return self._all_permissions()

        kb = await self.kb_repository.get_by_kb_id(kb_id)
        if kb is None:
            return EffectiveKnowledgePermissions()

        user_uid = str(user.get("uid") or "")
        if user_uid and getattr(kb, "created_by", None) == user_uid:
            return self._all_permissions()

        grants = await self.permission_repository.list_by_kb_id(kb_id)
        merged = self._merge_matching_grants(user, grants)
        if any(getattr(merged, action) for action in KNOWLEDGE_PERMISSION_ACTIONS):
            return merged

        return self._share_config_fallback(user, getattr(kb, "share_config", None))

    @staticmethod
    def _all_permissions() -> EffectiveKnowledgePermissions:
        return EffectiveKnowledgePermissions(**{action: True for action in KNOWLEDGE_PERMISSION_ACTIONS})

    @staticmethod
    def _merge_matching_grants(user: dict[str, Any], grants: list[Any]) -> EffectiveKnowledgePermissions:
        values = {action: False for action in KNOWLEDGE_PERMISSION_ACTIONS}
        for grant in grants:
            if not KnowledgePermissionService._grant_matches_user(user, grant):
                continue
            for action in KNOWLEDGE_PERMISSION_ACTIONS:
                values[action] = values[action] or bool(getattr(grant, action, False))
        return EffectiveKnowledgePermissions(**values)

    @staticmethod
    def _grant_matches_user(user: dict[str, Any], grant: Any) -> bool:
        subject_type = getattr(grant, "subject_type", None)
        subject_id = str(getattr(grant, "subject_id", ""))
        if subject_type == "user":
            return bool(user.get("uid") and str(user["uid"]) == subject_id)
        if subject_type == "department":
            return bool(user.get("department_id") is not None and str(user["department_id"]) == subject_id)
        if subject_type == "role":
            role_names = {str(user.get("role") or "")}
            role_names.update(str(role) for role in user.get("roles") or [])
            return subject_id in role_names
        return False

    @staticmethod
    def _share_config_fallback(user: dict[str, Any], share_config: dict | None) -> EffectiveKnowledgePermissions:
        if not share_config:
            return EffectiveKnowledgePermissions()
        db_info = {"created_by": None, "share_config": share_config}
        if KnowledgeBaseManager._database_info_accessible(user, db_info):
            return EffectiveKnowledgePermissions(can_view=True, can_search=True)
        return EffectiveKnowledgePermissions()
