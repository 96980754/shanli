from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy.orm import Session

from app.models import Document, KnowledgeViewRule


class KnowledgeViewRuleService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_rule(self, kb_id: int, user_id: int) -> KnowledgeViewRule | None:
        return (
            self.session.query(KnowledgeViewRule)
            .filter(KnowledgeViewRule.kb_id == kb_id, KnowledgeViewRule.user_id == user_id)
            .one_or_none()
        )

    def set_rule(
        self,
        kb_id: int,
        user_id: int,
        allowed_departments: list[str],
        allowed_product_lines: list[str],
        allowed_visibilities: list[str],
        max_security_level: int | None,
    ) -> KnowledgeViewRule:
        rule = self.get_rule(kb_id, user_id)
        if rule is None:
            rule = KnowledgeViewRule(kb_id=kb_id, user_id=user_id)
            self.session.add(rule)
        rule.allowed_departments = json.dumps(allowed_departments, ensure_ascii=False)
        rule.allowed_product_lines = json.dumps(allowed_product_lines, ensure_ascii=False)
        rule.allowed_visibilities = json.dumps(allowed_visibilities, ensure_ascii=False)
        rule.max_security_level = max_security_level
        rule.updated_at = datetime.utcnow()
        self.session.commit()
        self.session.refresh(rule)
        return rule

    def delete_rule(self, kb_id: int, user_id: int) -> KnowledgeViewRule | None:
        rule = self.get_rule(kb_id, user_id)
        if rule is None:
            return None
        self.session.delete(rule)
        self.session.commit()
        return rule

    def serialize_rule(self, rule: KnowledgeViewRule) -> dict:
        return {
            "kb_id": rule.kb_id,
            "user_id": rule.user_id,
            "allowed_departments": json.loads(rule.allowed_departments or "[]"),
            "allowed_product_lines": json.loads(rule.allowed_product_lines or "[]"),
            "allowed_visibilities": json.loads(rule.allowed_visibilities or "[]"),
            "max_security_level": rule.max_security_level,
        }

    def can_view_document(self, document: Document, rule: KnowledgeViewRule | None) -> bool:
        if rule is None:
            return True
        payload = self.serialize_rule(rule)
        if payload["allowed_departments"] and document.department not in payload["allowed_departments"]:
            return False
        if payload["allowed_product_lines"] and document.product_line not in payload["allowed_product_lines"]:
            return False
        if payload["allowed_visibilities"] and document.visibility not in payload["allowed_visibilities"]:
            return False
        max_level = payload["max_security_level"]
        return max_level is None or document.security_level <= max_level
