from __future__ import annotations
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any
class ConflictClassification(StrEnum):
    DUPLICATE = "DUPLICATE"
    COMPLETION = "COMPLETION"
    UPDATE = "UPDATE"
    CONFLICT = "CONFLICT"
    LINK_AMBIGUOUS = "LINK_AMBIGUOUS"
    INVALID = "INVALID"
@dataclass(frozen=True)
class BusinessRule:
    entity_type: str
    predicate: str
    value_type: str
    multiple: bool = False
    standard_unit: str | None = None
    allowed_values: tuple[str, ...] = ()
    version_sensitive: bool = True
    time_sensitive: bool = True
@dataclass(frozen=True)
class DetectionResult:
    classification: ConflictClassification
    conflict_type: str
    reasons: tuple[str, ...]
    existing_assertion_ids: tuple[str, ...]
    normalized_existing_value: Any
    normalized_incoming_value: Any
    severity: str
    requires_review: bool
PRODUCT_SPECIFICATION_RULES: dict[str, BusinessRule] = {
    "product_name": BusinessRule("Product", "product_name", "string"),
    "product_model": BusinessRule("Product", "product_model", "string"),
    "product_version": BusinessRule("Product", "product_version", "string"),
    "max_concurrent_users": BusinessRule(
        "Specification", "max_concurrent_users", "integer"
    ),
    "supported_os": BusinessRule(
        "Specification",
        "supported_os",
        "enum",
        multiple=True,
        allowed_values=("Windows", "Linux", "macOS", "Android", "iOS"),
    ),
    "product_capability": BusinessRule(
        "Specification", "product_capability", "string", multiple=True
    ),
    "launch_date": BusinessRule("Specification", "launch_date", "date"),
    "weight": BusinessRule("Specification", "weight", "decimal", standard_unit="kg"),
    "deployment_mode": BusinessRule(
        "Specification",
        "deployment_mode",
        "enum",
        multiple=True,
        allowed_values=("on-premises", "private-cloud", "public-cloud", "hybrid"),
    ),
    "compatible_with": BusinessRule(
        "Specification", "compatible_with", "string", multiple=True
    ),
}
_UNIT_FACTORS: dict[tuple[str, str], Decimal] = {
    ("g", "kg"): Decimal("0.001"),
    ("kg", "kg"): Decimal("1"),
    ("mg", "kg"): Decimal("0.000001"),
    ("lb", "kg"): Decimal("0.45359237"),
}
def normalize_entity_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value or "")
    return " ".join(normalized.casefold().split())
def normalize_value(raw_value: Any, rule: BusinessRule, unit: str | None = None) -> Any:
    values = raw_value if rule.multiple and isinstance(raw_value, list) else [raw_value]
    normalized = [_normalize_single_value(value, rule, unit) for value in values]
    if rule.multiple:
        return sorted(
            dict.fromkeys(normalized), key=lambda value: str(value).casefold()
        )
    return normalized[0]
def _normalize_single_value(
    raw_value: Any, rule: BusinessRule, unit: str | None
) -> Any:
    if raw_value is None or (isinstance(raw_value, str) and not raw_value.strip()):
        raise ValueError("value is empty")
    if rule.value_type == "integer":
        text = str(raw_value).replace(",", "").strip()
        if not re.fullmatch(r"[+-]?\d+", text):
            raise ValueError("value must be an integer")
        return int(text)
    if rule.value_type == "decimal":
        source_unit = (unit or rule.standard_unit or "").strip().casefold()
        target_unit = (rule.standard_unit or source_unit).casefold()
        try:
            value = Decimal(str(raw_value).replace(",", "").strip())
        except InvalidOperation as exc:
            raise ValueError("value must be numeric") from exc
        factor = _UNIT_FACTORS.get((source_unit, target_unit))
        if factor is None:
            raise ValueError(f"unsupported unit: {source_unit or '(missing)'}")
        return format((value * factor).normalize(), "f")
    if rule.value_type == "date":
        try:
            return datetime.fromisoformat(str(raw_value).strip()).date().isoformat()
        except ValueError as exc:
            raise ValueError("value must be an ISO date") from exc
    normalized = " ".join(unicodedata.normalize("NFKC", str(raw_value)).strip().split())
    if rule.value_type == "enum":
        allowed_by_key = {item.casefold(): item for item in rule.allowed_values}
        allowed = allowed_by_key.get(normalized.casefold())
        if allowed is None:
            raise ValueError(f"value is not in allowed values: {normalized}")
        return allowed
    return normalized
def _time_ranges_overlap(incoming: Any, existing: Any) -> bool:
    incoming_start = getattr(incoming, "valid_from", None)
    incoming_end = getattr(incoming, "valid_to", None)
    existing_start = getattr(existing, "valid_from", None)
    existing_end = getattr(existing, "valid_to", None)
    if incoming_end and existing_start and incoming_end < existing_start:
        return False
    if existing_end and incoming_start and existing_end < incoming_start:
        return False
    return True
class ConflictDetector:
    def __init__(self, rules: dict[str, BusinessRule] | None = None):
        self.rules = rules or PRODUCT_SPECIFICATION_RULES
    def detect(
        self,
        incoming: Any,
        existing_assertions: list[Any],
        *,
        link_status: str = "linked",
    ) -> DetectionResult:
        rule = self.rules.get(incoming.predicate)
        if rule is None or rule.entity_type != incoming.entity_type:
            return self._invalid("属性不在 Product / Specification MVP 规则范围内")
        try:
            incoming_value = normalize_value(
                incoming.raw_value, rule, getattr(incoming, "unit", None)
            )
        except ValueError as exc:
            return self._invalid(str(exc))
        if link_status != "linked":
            return DetectionResult(
                classification=ConflictClassification.LINK_AMBIGUOUS,
                conflict_type="entity_link",
                reasons=("实体无法通过确定性规则唯一链接，需要人工确认",),
                existing_assertion_ids=(),
                normalized_existing_value=None,
                normalized_incoming_value=incoming_value,
                severity="medium",
                requires_review=True,
            )
        relevant = [
            item
            for item in existing_assertions
            if item.predicate == incoming.predicate
            and item.status in {"accepted", "published"}
        ]
        if not relevant:
            return DetectionResult(
                classification=ConflictClassification.COMPLETION,
                conflict_type="missing_value",
                reasons=("正式知识中尚无该属性值",),
                existing_assertion_ids=(),
                normalized_existing_value=None,
                normalized_incoming_value=incoming_value,
                severity="low",
                requires_review=True,
            )
        existing_values: list[Any] = []
        for item in relevant:
            try:
                existing_values.append(
                    item.normalized_value
                    if item.normalized_value is not None
                    else normalize_value(
                        item.raw_value, rule, getattr(item, "unit", None)
                    )
                )
            except ValueError:
                continue
        existing_ids = tuple(item.assertion_id for item in relevant)
        if rule.multiple:
            flattened = {
                value
                for item in existing_values
                for value in (item if isinstance(item, list) else [item])
            }
            incoming_items = set(incoming_value)
            if incoming_items.issubset(flattened):
                return self._result(
                    ConflictClassification.DUPLICATE,
                    "same_normalized_value",
                    "标准化后的值已存在",
                    existing_ids,
                    sorted(flattened, key=str),
                    incoming_value,
                    "info",
                    False,
                )
            return self._result(
                ConflictClassification.COMPLETION,
                "new_multi_value_member",
                "多值属性包含新的有效成员",
                existing_ids,
                sorted(flattened, key=str),
                incoming_value,
                "low",
                True,
            )
        if incoming_value in existing_values:
            return self._result(
                ConflictClassification.DUPLICATE,
                "same_normalized_value",
                "标准化后的值相同",
                existing_ids,
                existing_values[0],
                incoming_value,
                "info",
                False,
            )
        same_version_and_time = []
        for item in relevant:
            version_differs = bool(
                rule.version_sensitive
                and incoming.product_version
                and item.product_version
                and incoming.product_version != item.product_version
            )
            time_overlaps = not rule.time_sensitive or _time_ranges_overlap(
                incoming, item
            )
            if not version_differs and time_overlaps:
                same_version_and_time.append(item)
        if not same_version_and_time:
            return self._result(
                ConflictClassification.UPDATE,
                "version_or_time_update",
                "产品版本不同或有效时间范围不重叠",
                existing_ids,
                existing_values,
                incoming_value,
                "low",
                True,
            )
        return self._result(
            ConflictClassification.CONFLICT,
            "single_value_conflict",
            "同一实体、版本和有效时间内的单值属性不同",
            tuple(item.assertion_id for item in same_version_and_time),
            [
                item.normalized_value
                if item.normalized_value is not None
                else item.raw_value
                for item in same_version_and_time
            ],
            incoming_value,
            "high",
            True,
        )
    @staticmethod
    def _invalid(reason: str) -> DetectionResult:
        return DetectionResult(
            classification=ConflictClassification.INVALID,
            conflict_type="business_rule_validation",
            reasons=(reason,),
            existing_assertion_ids=(),
            normalized_existing_value=None,
            normalized_incoming_value=None,
            severity="high",
            requires_review=True,
        )
    @staticmethod
    def _result(
        classification: ConflictClassification,
        conflict_type: str,
        reason: str,
        existing_ids: tuple[str, ...],
        existing_value: Any,
        incoming_value: Any,
        severity: str,
        requires_review: bool,
    ) -> DetectionResult:
        return DetectionResult(
            classification=classification,
            conflict_type=conflict_type,
            reasons=(reason,),
            existing_assertion_ids=existing_ids,
            normalized_existing_value=existing_value,
            normalized_incoming_value=incoming_value,
            severity=severity,
            requires_review=requires_review,
        )
