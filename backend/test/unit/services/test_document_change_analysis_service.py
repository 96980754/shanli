from __future__ import annotations

from types import SimpleNamespace

from yuxi.services.document_change_analysis_service import analyze_document_changes


def _chunk(file_id: str, text: str, relations: list[dict], chunk_index: int = 0) -> dict:
    prepared = []
    for relation in relations:
        quote = relation.pop("quote")
        start = text.index(quote)
        prepared.append(
            {
                "source": {"text": relation.pop("source"), "label": relation.pop("source_label", "Product")},
                "target": {"text": relation.pop("target"), "label": relation.pop("target_label", "Capability")},
                "text": relation.pop("text", quote),
                "label": relation.pop("label"),
                "polarity": relation.pop("polarity", "positive"),
                "assertion_kind": relation.pop("assertion_kind", "fact"),
                "evidence": {"quote": quote, "start_char": start, "end_char": start + len(quote)},
            }
        )
    return {
        "file_id": file_id,
        "chunk_id": f"{file_id}-{chunk_index}",
        "chunk_index": chunk_index,
        "content": text,
        "extraction_result": {"metadata": {"schema_version": 2}, "relations": prepared},
    }


def _ontology() -> SimpleNamespace:
    return SimpleNamespace(
        rules={
            "conflict_detection": {
                "single_valued_relations": [],
                "keyed_value_relations": ["HAS_SPEC"],
            }
        }
    )


def test_keyed_value_replacement_is_changed_and_auto_accepted():
    old = _chunk(
        "old",
        "产品重量为 100g。",
        [{"source": "产品", "target": "重量: 100g", "label": "HAS_SPEC", "quote": "产品重量为 100g。"}],
    )
    new = _chunk(
        "new",
        "产品重量为 120g。",
        [{"source": "产品", "target": "重量: 120g", "label": "HAS_SPEC", "quote": "产品重量为 120g。"}],
    )

    result = analyze_document_changes([old], [new], _ontology())

    assert result["status"] == "auto_accepted"
    assert result["summary"]["changed_count"] == 1
    assert result["items"][0]["change_type"] == "changed"
    assert result["items"][0]["old_fact"]["normalized_value"] == "100g"
    assert result["items"][0]["new_fact"]["normalized_value"] == "120g"


def test_explicit_retraction_is_removed_and_requires_review():
    old = _chunk(
        "old",
        "产品支持离线模式。",
        [{"source": "产品", "target": "离线模式", "label": "SUPPORTS", "quote": "产品支持离线模式。"}],
    )
    new = _chunk(
        "new",
        "产品不再支持离线模式。",
        [
            {
                "source": "产品",
                "target": "离线模式",
                "label": "SUPPORTS",
                "quote": "产品不再支持离线模式。",
                "polarity": "negative",
                "assertion_kind": "retraction",
            }
        ],
    )

    result = analyze_document_changes([old], [new], _ontology())

    assert result["status"] == "review_required"
    assert result["summary"]["removed_count"] == 1
    assert result["items"][0]["change_type"] == "removed"
    assert result["items"][0]["review_required"] is True


def test_absent_old_fact_is_not_removed():
    old = _chunk(
        "old",
        "产品支持离线模式。",
        [{"source": "产品", "target": "离线模式", "label": "SUPPORTS", "quote": "产品支持离线模式。"}],
    )
    new = _chunk(
        "new",
        "产品支持在线模式。",
        [{"source": "产品", "target": "在线模式", "label": "SUPPORTS", "quote": "产品支持在线模式。"}],
    )

    result = analyze_document_changes([old], [new], _ontology())

    assert result["status"] == "auto_accepted"
    assert result["summary"]["new_count"] == 1
    assert result["summary"]["removed_count"] == 0


def test_candidate_positive_and_negative_same_fact_is_conflict():
    old = _chunk(
        "old",
        "产品支持离线模式。",
        [{"source": "产品", "target": "离线模式", "label": "SUPPORTS", "quote": "产品支持离线模式。"}],
    )
    new = _chunk(
        "new",
        "产品支持离线模式。产品不再支持离线模式。",
        [
            {"source": "产品", "target": "离线模式", "label": "SUPPORTS", "quote": "产品支持离线模式。"},
            {
                "source": "产品",
                "target": "离线模式",
                "label": "SUPPORTS",
                "quote": "产品不再支持离线模式。",
                "polarity": "negative",
                "assertion_kind": "retraction",
            },
        ],
    )

    result = analyze_document_changes([old], [new], _ontology())

    assert result["status"] == "review_required"
    assert result["summary"]["conflict_count"] == 1
    assert result["items"][0]["change_type"] == "conflict"


def test_multiple_keyed_values_in_candidate_are_one_conflict():
    old = _chunk(
        "old",
        "产品重量为 100g。",
        [{"source": "产品", "target": "重量: 100g", "label": "HAS_SPEC", "quote": "产品重量为 100g。"}],
    )
    new = _chunk(
        "new",
        "产品重量为 120g。产品重量为 130g。",
        [
            {"source": "产品", "target": "重量: 120g", "label": "HAS_SPEC", "quote": "产品重量为 120g。"},
            {"source": "产品", "target": "重量: 130g", "label": "HAS_SPEC", "quote": "产品重量为 130g。"},
        ],
    )

    result = analyze_document_changes([old], [new], _ontology())

    assert result["status"] == "review_required"
    assert result["summary"]["conflict_count"] == 1
    assert len(result["items"]) == 1


def test_missing_v1_evidence_is_inconclusive():
    old = _chunk(
        "old",
        "产品支持离线模式。",
        [{"source": "产品", "target": "离线模式", "label": "SUPPORTS", "quote": "产品支持离线模式。"}],
    )
    new = _chunk(
        "new",
        "产品支持在线模式。",
        [{"source": "产品", "target": "在线模式", "label": "SUPPORTS", "quote": "产品支持在线模式。"}],
    )
    new["extraction_result"]["metadata"]["schema_version"] = 1
    new["extraction_result"]["relations"][0]["evidence"] = {"quote": ""}

    result = analyze_document_changes([old], [new], _ontology())

    assert result["status"] == "review_required"
    assert result["summary"]["inconclusive"] is True
    assert "evidence" in result["summary"]["message"]


def test_duplicate_assertions_become_one_item_with_all_evidence():
    old = _chunk(
        "old",
        "产品支持基础模式。",
        [{"source": "产品", "target": "基础模式", "label": "SUPPORTS", "quote": "产品支持基础模式。"}],
    )
    new_chunks = [
        _chunk(
            "new",
            "产品支持在线模式。",
            [{"source": "产品", "target": "在线模式", "label": "SUPPORTS", "quote": "产品支持在线模式。"}],
            chunk_index=index,
        )
        for index in (1, 0)
    ]

    result = analyze_document_changes([old], new_chunks, _ontology())

    assert result["summary"]["new_count"] == 1
    assert len(result["items"]) == 1
    assert [value["chunk_index"] for value in result["items"][0]["new_evidence"]] == [0, 1]
