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


def test_normalized_evidence_is_valid_instead_of_inconclusive():
    from yuxi.knowledge.graphs.graph_utils import locate_evidence_quote

    # docx 表格 markdown：带 ** 加粗与 NBSP；quote 是 LLM 润色后的文本
    def chunk(file_id: str, target: str, cell: str) -> dict:
        content = f"| **{cell}** | 有效传输距离 800米\xa0（空旷） |"
        quote = f"{cell} 有效传输距离 800米（空旷）"
        start, end = locate_evidence_quote(content, quote)
        assert start is not None and end > start
        return {
            "file_id": file_id,
            "chunk_id": f"{file_id}-0",
            "chunk_index": 0,
            "content": content,
            "extraction_result": {
                "metadata": {"schema_version": 2},
                "relations": [
                    {
                        "source": {"text": "产品", "label": "Product"},
                        "target": {"text": target, "label": "Feature"},
                        "text": quote,
                        "label": "SUPPORTS",
                        "polarity": "positive",
                        "assertion_kind": "fact",
                        "evidence": {"quote": quote, "start_char": start, "end_char": end},
                    }
                ],
            },
        }

    old = chunk("old", "AI双麦降噪", "AI双麦降噪")
    new = chunk("new", "AI双麦降噪2", "AI双麦降噪2")

    result = analyze_document_changes([old], [new], _ontology())

    # 证据通过归一化校验，不应再触发 inconclusive
    assert result["status"] == "auto_accepted"
    assert result["summary"]["inconclusive"] is False
    assert all(occurrence["valid"] for occurrence in result["items"][0]["new_evidence"])


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


def test_same_subject_keyed_slot_whitespace_drift_is_changed_not_new():
    # 复现回归：旧版实体名"极峰·信使 （SM-990X）"与新版"极峰·信使（SM-990X）"
    # 仅括号前多一个空格。若不归一化空白，_entity_key 判为不同实体，
    # 同一 keyed 槽位的取值变化会误判为"新增"（旧值被隐藏、无法对照）。
    old = _chunk(
        "old",
        "极峰·信使 （SM-990X）电池容量 3800mAh。",
        [
            {
                "source": "极峰·信使 （SM-990X）",
                "target": "电池容量: 3800mAh",
                "label": "HAS_SPEC",
                "quote": "极峰·信使 （SM-990X）电池容量 3800mAh。",
            }
        ],
    )
    new = _chunk(
        "new",
        "极峰·信使（SM-990X）电池容量 1200mAh。",
        [
            {
                "source": "极峰·信使（SM-990X）",
                "target": "电池容量: 1200mAh",
                "label": "HAS_SPEC",
                "quote": "极峰·信使（SM-990X）电池容量 1200mAh。",
            }
        ],
    )

    result = analyze_document_changes([old], [new], _ontology())

    assert result["status"] == "auto_accepted"
    assert result["summary"]["changed_count"] == 1
    assert result["summary"]["new_count"] == 0
    item = result["items"][0]
    assert item["change_type"] == "changed"
    # normalized_value 经 normalize_entity_name 统一小写
    assert item["old_fact"]["normalized_value"] == "3800mah"
    assert item["new_fact"]["normalized_value"] == "1200mah"
    # 展示用 target.text 保留原始大小写
    assert item["old_fact"]["target"]["text"] == "电池容量: 3800mAh"
