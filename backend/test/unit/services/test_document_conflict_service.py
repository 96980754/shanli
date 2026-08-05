from __future__ import annotations

from types import SimpleNamespace

from yuxi.services.document_conflict_service import analyze_document_conflicts, detect_document_conflicts


def _ontology(*, single=(), keyed=()):
    return SimpleNamespace(
        rules={
            "conflict_detection": {
                "single_valued_relations": list(single),
                "keyed_value_relations": list(keyed),
            }
        }
    )


def _chunk(file_id: str, chunk_id: str, relation: str, target: str):
    source = {"text": "产品A", "label": "Product", "attributes": []}
    target_entity = {"text": target, "label": "Specification", "attributes": []}
    return {
        "file_id": file_id,
        "chunk_id": chunk_id,
        "chunk_index": 0,
        "content": f"产品A {relation} {target}",
        "extraction_result": {
            "entities": [source, target_entity],
            "relations": [
                {
                    "source": source,
                    "target": target_entity,
                    "text": relation,
                    "label": relation,
                }
            ],
        },
    }


def test_detects_keyed_value_change_with_provenance():
    conflicts = detect_document_conflicts(
        [_chunk("old", "old-chunk", "HAS_SPEC", "battery_capacity:5000mAh")],
        [_chunk("new", "new-chunk", "HAS_SPEC", "battery_capacity:6000mAh")],
        _ontology(keyed=("HAS_SPEC",)),
    )

    assert len(conflicts) == 1
    assert conflicts[0]["conflict_type"] == "keyed_value_changed"
    assert conflicts[0]["old_fact"]["chunk_id"] == "old-chunk"
    assert conflicts[0]["new_fact"]["chunk_id"] == "new-chunk"


def test_different_spec_keys_do_not_conflict():
    conflicts = detect_document_conflicts(
        [_chunk("old", "old-chunk", "HAS_SPEC", "weight:100g")],
        [_chunk("new", "new-chunk", "HAS_SPEC", "battery_capacity:6000mAh")],
        _ontology(keyed=("HAS_SPEC",)),
    )

    assert conflicts == []


def test_multivalued_relation_is_not_treated_as_conflict():
    conflicts = detect_document_conflicts(
        [_chunk("old", "old-chunk", "SUPPORTS", "组呼")],
        [_chunk("new", "new-chunk", "SUPPORTS", "视频调度")],
        _ontology(),
    )

    assert conflicts == []


def test_single_value_relation_change_is_detected():
    conflicts = detect_document_conflicts(
        [_chunk("old", "old-chunk", "CURRENT_STATUS", "Planning")],
        [_chunk("new", "new-chunk", "CURRENT_STATUS", "Released")],
        _ontology(single=("CURRENT_STATUS",)),
    )

    assert len(conflicts) == 1
    assert conflicts[0]["conflict_type"] == "single_value_changed"


def test_same_fact_does_not_conflict():
    analysis = analyze_document_conflicts(
        [_chunk("old", "old-chunk", "HAS_SPEC", "weight:100g")],
        [_chunk("new", "new-chunk", "HAS_SPEC", "weight:100g")],
        _ontology(keyed=("HAS_SPEC",)),
    )

    assert analysis["status"] == "clear"
    assert analysis["conflicts"] == []
    assert analysis["comparable_fact_count"] == 1


def test_empty_chunks_are_inconclusive():
    analysis = analyze_document_conflicts([], [], _ontology(keyed=("HAS_SPEC",)))

    assert analysis["status"] == "inconclusive"
    assert "缺少" in analysis["message"]


def test_no_conflict_eligible_facts_is_inconclusive():
    analysis = analyze_document_conflicts(
        [_chunk("old", "old-chunk", "SUPPORTS", "组呼")],
        [_chunk("new", "new-chunk", "SUPPORTS", "视频调度")],
        _ontology(keyed=("HAS_SPEC",)),
    )

    assert analysis["status"] == "inconclusive"
    assert analysis["old_fact_count"] == 0
    assert analysis["new_fact_count"] == 0


def test_different_fact_keys_are_inconclusive():
    analysis = analyze_document_conflicts(
        [_chunk("old", "old-chunk", "HAS_SPEC", "weight:100g")],
        [_chunk("new", "new-chunk", "HAS_SPEC", "battery_capacity:6000mAh")],
        _ontology(keyed=("HAS_SPEC",)),
    )

    assert analysis["status"] == "inconclusive"
    assert analysis["comparable_fact_count"] == 0
