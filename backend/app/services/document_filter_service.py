from __future__ import annotations

import json
from dataclasses import dataclass

from app.models import Document, User
from app.services.db_kb_service import DbKnowledgeBaseService
from app.services.view_rule_service import KnowledgeViewRuleService


_SCOPE_BY_VISIBILITY = {
    "public": "C",
    "internal": "I",
    "restricted": "R",
}
_PRODUCT_BY_LEGACY_NAME = {
    "MCSTARS": "MC",
    "MINISERVER": "MS",
    "MDM": "MD",
    "POCSTARS-MNO": "MNO",
    "POCSTARS-PRO": "PRO",
    "POCSTARS-UC": "UC",
    "定位": "LOC",
}


@dataclass(frozen=True)
class EffectiveDocumentFilter:
    allow_all: bool
    allowed_scopes: set[str] | None
    allowed_departments: set[str] | None
    allowed_products: set[str] | None
    allowed_roles: set[str] | None
    max_security_level: int | None

    def matches(self, document: Document) -> bool:
        if not self.allow_all:
            return False
        if self.allowed_scopes is not None and document.scope not in self.allowed_scopes:
            return False
        if self.allowed_departments is not None and document.department not in self.allowed_departments:
            return False
        if self.allowed_products is not None:
            normalized_allowed_products = {
                _PRODUCT_BY_LEGACY_NAME.get(value.upper(), value)
                for value in self.allowed_products
            }
            product_matches = document.product in normalized_allowed_products
            legacy_matches = document.product_line in self.allowed_products
            if not product_matches and not legacy_matches:
                return False
        if self.max_security_level is not None and document.security_level > self.max_security_level:
            return False
        document_roles = set(json.loads(document.acl_roles or "[]"))
        if document_roles and self.allowed_roles is not None and not self.allowed_roles.intersection(document_roles):
            return False
        return True


class DocumentFilterService:
    def __init__(
        self,
        kb_service: DbKnowledgeBaseService,
        view_rule_service: KnowledgeViewRuleService,
    ) -> None:
        self.kb_service = kb_service
        self.view_rule_service = view_rule_service

    def roles_for_user(self, user_id: int) -> set[str]:
        user = self.kb_service.session.get(User, user_id)
        if user is None or user.role is None:
            return set()
        return {user.role.name}

    def build_filter(
        self,
        kb_id: int,
        user_id: int,
        user_roles: set[str],
        system_max_security_level: int | None = None,
    ) -> EffectiveDocumentFilter:
        if not self.kb_service.has_permission(kb_id, user_id, "can_view"):
            return EffectiveDocumentFilter(
                allow_all=False,
                allowed_scopes=set(),
                allowed_departments=set(),
                allowed_products=set(),
                allowed_roles=set(),
                max_security_level=system_max_security_level,
            )

        if self.kb_service.has_permission(kb_id, user_id, "can_grant"):
            return EffectiveDocumentFilter(
                allow_all=True,
                allowed_scopes=None,
                allowed_departments=None,
                allowed_products=None,
                allowed_roles=None,
                max_security_level=system_max_security_level,
            )

        rule = self.view_rule_service.get_rule(kb_id, user_id)
        if rule is None:
            return EffectiveDocumentFilter(
                allow_all=True,
                allowed_scopes=None,
                allowed_departments=None,
                allowed_products=None,
                allowed_roles=set(user_roles),
                max_security_level=system_max_security_level,
            )

        payload = self.view_rule_service.serialize_rule(rule)
        allowed_scopes = {
            _SCOPE_BY_VISIBILITY.get(value, value)
            for value in payload["allowed_visibilities"]
        } or None
        max_security_level = payload["max_security_level"]
        if system_max_security_level is not None:
            max_security_level = min(
                level
                for level in (max_security_level, system_max_security_level)
                if level is not None
            )

        return EffectiveDocumentFilter(
            allow_all=True,
            allowed_scopes=allowed_scopes,
            allowed_departments=set(payload["allowed_departments"]) or None,
            allowed_products=set(payload["allowed_product_lines"]) or None,
            allowed_roles=set(user_roles),
            max_security_level=max_security_level,
        )
