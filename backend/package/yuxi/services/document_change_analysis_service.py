from __future__ import annotations

import json
import re
from collections import defaultdict
from typing import Any

from yuxi.knowledge.graphs.graph_utils import locate_evidence_quote, normalize_entity_name
from yuxi.knowledge.graphs.ontology.registry import OntologySpec

_KEY_VALUE_RE = re.compile(r"^\s*([^:：=]+?)\s*[:：=]\s*(.+?)\s*$")
_CHANGE_ORDER = {"conflict": 0, "removed": 1, "changed": 2, "new": 3}
_SEVERITY = {"new": "low", "changed": "medium", "removed": "high", "conflict": "critical"}


def analyze_document_changes(
    old_chunks: list[dict[str, Any]],
    new_chunks: list[dict[str, Any]],
    ontology: OntologySpec,
) -> dict[str, Any]:
    rules = ontology.rules.get("conflict_detection") or {}
    single_relations = {str(value).strip() for value in rules.get("single_valued_relations", [])}
    keyed_relations = {str(value).strip() for value in rules.get("keyed_value_relations", [])}

    diagnostics: list[str] = []
    old_occurrences = _collect_occurrences(old_chunks, single_relations, keyed_relations, diagnostics, "旧版")
    new_occurrences = _collect_occurrences(new_chunks, single_relations, keyed_relations, diagnostics, "新版")
    if not old_chunks or not new_chunks:
        diagnostics.append("新旧版本缺少可用于比较的文档分块")
    if not old_occurrences or not new_occurrences:
        diagnostics.append("新旧版本没有可用于知识变更分析的 assertion")

    old_positive = _group_positive(old_occurrences)
    new_positive = _group_positive(new_occurrences)
    new_negative = _group_negative(new_occurrences)
    items: list[dict[str, Any]] = []
    conflicted_facts: set[str] = set()
    conflicted_slots: set[str] = set()

    for fact_key in sorted(set(new_positive) & set(new_negative)):
        conflicted_facts.add(fact_key)
        items.append(
            _item(
                "conflict",
                fact_key,
                _snapshot(old_positive.get(fact_key, [])) or _snapshot(new_positive[fact_key]),
                _snapshot(new_negative[fact_key]),
                "候选版本对同一事实同时存在肯定和否定/撤回 assertion",
            )
        )

    old_slots = _group_slot_values(old_occurrences)
    new_slots = _group_slot_values(new_occurrences)
    constrained_slots = {
        occurrence["slot_key"] for occurrence in old_occurrences + new_occurrences if occurrence["slot_kind"] != "multi"
    }
    for slot_key in sorted(constrained_slots):
        old_values = old_slots.get(slot_key, {})
        new_values = new_slots.get(slot_key, {})
        if len(old_values) > 1 or len(new_values) > 1:
            conflicted_slots.add(slot_key)
            old_snapshot = _snapshot([item for values in old_values.values() for item in values])
            new_snapshot = _snapshot([item for values in new_values.values() for item in values])
            items.append(
                _item(
                    "conflict",
                    slot_key,
                    old_snapshot,
                    new_snapshot,
                    "单值或 keyed slot 同时存在多个不兼容的肯定值",
                )
            )

    for slot_key in sorted(constrained_slots - conflicted_slots):
        old_values = old_slots.get(slot_key, {})
        new_values = new_slots.get(slot_key, {})
        old_value = next(iter(old_values), None)
        new_value = next(iter(new_values), None)
        if old_value is not None and new_value is not None and old_value != new_value:
            items.append(
                _item(
                    "changed",
                    slot_key,
                    _snapshot(old_values[old_value]),
                    _snapshot(new_values[new_value]),
                    "单值或 keyed slot 的值发生变化",
                )
            )
        elif old_value is None and new_value is not None:
            occurrences = new_values[new_value]
            if occurrences[0]["fact_key"] not in conflicted_facts:
                items.append(_item("new", occurrences[0]["fact_key"], None, _snapshot(occurrences), "新增事实"))

    for fact_key in sorted(new_positive):
        occurrences = new_positive[fact_key]
        if occurrences[0]["slot_kind"] != "multi" or fact_key in conflicted_facts:
            continue
        if fact_key not in old_positive:
            items.append(_item("new", fact_key, None, _snapshot(occurrences), "新增事实"))

    for fact_key in sorted(new_negative):
        if fact_key in conflicted_facts:
            continue
        if fact_key in old_positive:
            items.append(
                _item(
                    "removed",
                    fact_key,
                    _snapshot(old_positive[fact_key]),
                    _snapshot(new_negative[fact_key]),
                    "候选版本以明确否定或撤回 assertion 删除旧事实",
                )
            )
        else:
            items.append(
                _item(
                    "conflict",
                    fact_key,
                    None,
                    _snapshot(new_negative[fact_key]),
                    "候选版本否定或撤回的事实在旧版中不存在",
                )
            )

    items.sort(key=lambda item: (item["fact_key"], _CHANGE_ORDER[item["change_type"]]))
    evidence_incomplete = any(not _item_evidence_valid(item) for item in items)
    if any(not occurrence["evidence_valid"] for occurrence in new_occurrences):
        evidence_incomplete = True
    if evidence_incomplete:
        diagnostics.append("存在缺失或无法定位到原文的 assertion evidence")

    diagnostics = list(dict.fromkeys(diagnostics))
    inconclusive = bool(diagnostics)
    review_required = inconclusive or any(item["change_type"] in {"removed", "conflict"} for item in items)
    for item in items:
        item["review_required"] = item["change_type"] in {"removed", "conflict"} or not _item_evidence_valid(item)
        item["decision"] = "pending" if item["review_required"] else "auto_accepted"

    counts = {change_type: 0 for change_type in _CHANGE_ORDER}
    for item in items:
        counts[item["change_type"]] += 1
    return {
        "status": "review_required" if review_required else "auto_accepted",
        "items": items,
        "summary": {
            "item_count": len(items),
            "new_count": counts["new"],
            "changed_count": counts["changed"],
            "removed_count": counts["removed"],
            "conflict_count": counts["conflict"],
            "inconclusive": inconclusive,
            "message": "；".join(diagnostics) or None,
        },
    }


def _collect_occurrences(
    chunks: list[dict[str, Any]],
    single_relations: set[str],
    keyed_relations: set[str],
    diagnostics: list[str],
    version_name: str,
) -> list[dict[str, Any]]:
    occurrences = []
    for chunk in chunks:
        extraction_result = chunk.get("extraction_result") or {}
        for relation_index, relation in enumerate(extraction_result.get("relations") or []):
            source = relation.get("source") or {}
            target = relation.get("target") or {}
            relation_type = str(relation.get("label") or "").strip()
            subject_key = _entity_key(source)
            target_key = _entity_key(target)
            if not subject_key or not target_key or not relation_type:
                diagnostics.append(f"{version_name}存在无法归一化的 assertion")
                continue

            slot_kind = "multi"
            value = target_key
            slot_parts = [subject_key, relation_type]
            if relation_type in keyed_relations:
                parsed = _parse_keyed_value(str(target.get("text") or ""))
                if parsed is None:
                    diagnostics.append(f"{version_name}关系 {relation_type} 的 keyed value 格式无效")
                    continue
                key, value = parsed
                slot_kind = "keyed"
                slot_parts.append(key)
            elif relation_type in single_relations:
                slot_kind = "single"

            slot_key = _serialize_key(slot_parts)
            fact_key = _serialize_key([*slot_parts, value])
            evidence = _evidence_snapshot(chunk, relation.get("evidence") or {})
            occurrences.append(
                {
                    "subject": source,
                    "relation": relation_type,
                    "target": target,
                    "normalized_value": value,
                    "polarity": str(relation.get("polarity") or "positive").lower(),
                    "assertion_kind": str(relation.get("assertion_kind") or "fact").lower(),
                    "slot_kind": slot_kind,
                    "slot_key": slot_key,
                    "fact_key": fact_key,
                    "evidence": evidence,
                    "evidence_valid": evidence["valid"],
                    "relation_index": relation_index,
                }
            )
    return sorted(
        occurrences,
        key=lambda item: (
            item["fact_key"],
            str(item["evidence"].get("file_id") or ""),
            int(item["evidence"].get("chunk_index") or 0),
            str(item["evidence"].get("chunk_id") or ""),
            int(item["evidence"].get("start_char") or 0),
            item["relation_index"],
        ),
    )


def _group_positive(occurrences: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for occurrence in occurrences:
        if occurrence["polarity"] == "positive" and occurrence["assertion_kind"] == "fact":
            grouped[occurrence["fact_key"]].append(occurrence)
    return dict(grouped)


def _group_negative(occurrences: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for occurrence in occurrences:
        if occurrence["polarity"] == "negative" or occurrence["assertion_kind"] == "retraction":
            grouped[occurrence["fact_key"]].append(occurrence)
    return dict(grouped)


def _group_slot_values(occurrences: list[dict[str, Any]]) -> dict[str, dict[str, list[dict[str, Any]]]]:
    grouped: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    for occurrence in occurrences:
        if (
            occurrence["slot_kind"] != "multi"
            and occurrence["polarity"] == "positive"
            and occurrence["assertion_kind"] == "fact"
        ):
            grouped[occurrence["slot_key"]][occurrence["normalized_value"]].append(occurrence)
    return {slot: dict(values) for slot, values in grouped.items()}


def _item(
    change_type: str,
    fact_key: str,
    old_fact: dict[str, Any] | None,
    new_fact: dict[str, Any] | None,
    reason: str,
) -> dict[str, Any]:
    source = new_fact or old_fact or {}
    return {
        "change_type": change_type,
        "severity": _SEVERITY[change_type],
        "decision": "pending",
        "fact_key": fact_key,
        "relation": source.get("relation"),
        "old_fact": old_fact,
        "new_fact": new_fact,
        "old_evidence": (old_fact or {}).get("occurrences", []),
        "new_evidence": (new_fact or {}).get("occurrences", []),
        "review_required": False,
        "reason": reason,
    }


def _snapshot(occurrences: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not occurrences:
        return None
    first = occurrences[0]
    return {
        "subject": first["subject"],
        "relation": first["relation"],
        "target": first["target"],
        "normalized_value": first["normalized_value"],
        "polarity": first["polarity"],
        "assertion_kind": first["assertion_kind"],
        "occurrences": [occurrence["evidence"] for occurrence in occurrences],
    }


def _item_evidence_valid(item: dict[str, Any]) -> bool:
    evidence = [*(item.get("old_evidence") or []), *(item.get("new_evidence") or [])]
    return bool(evidence) and all(value.get("valid") for value in evidence)


def _evidence_snapshot(chunk: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    content = str(chunk.get("content") or "")
    quote = str(evidence.get("quote") or "")
    start = evidence.get("start_char")
    end = evidence.get("end_char")
    located = locate_evidence_quote(content, quote) if quote else None
    valid = (
        isinstance(start, int)
        and not isinstance(start, bool)
        and isinstance(end, int)
        and not isinstance(end, bool)
        and located is not None
        and start == located[0]
        and end == located[1]
    )
    return {
        "file_id": chunk.get("file_id"),
        "chunk_id": chunk.get("chunk_id"),
        "chunk_index": chunk.get("chunk_index"),
        "quote": quote,
        "start_char": start,
        "end_char": end,
        "valid": valid,
    }


def _entity_key(entity: dict[str, Any]) -> str:
    # 实体名归一化去掉全部空白：同一产品在不同版本抽取时可能因括号前空格、
    # 全角空格等细微差异被判为不同实体，导致新旧版所有 keyed 槽位无法配对，
    # 本应判"变更"的规格全部误判为"新增"（旧值被隐藏）。
    text = re.sub(r"\s+", "", normalize_entity_name(str(entity.get("text") or "")))
    label = str(entity.get("label") or "Entity").strip().casefold()
    return f"{label}:{text}" if text else ""


def _parse_keyed_value(value: str) -> tuple[str, str] | None:
    match = _KEY_VALUE_RE.fullmatch(value)
    if match is None:
        return None
    key = normalize_entity_name(match.group(1))
    normalized_value = normalize_entity_name(match.group(2))
    if not key or not normalized_value:
        return None
    return key, normalized_value


def _serialize_key(parts: list[str]) -> str:
    return json.dumps(parts, ensure_ascii=False, separators=(",", ":"))
