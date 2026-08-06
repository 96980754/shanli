from datetime import datetime
from types import SimpleNamespace
import pytest
from yuxi.knowledge.conflicts import ConflictClassification, ConflictDetector
def _assertion(**overrides):
    values = {
        "assertion_id": "assertion-new",
        "entity_type": "Specification",
        "predicate": "max_concurrent_users",
        "raw_value": 100,
        "normalized_value": None,
        "unit": None,
        "product_version": "V1",
        "valid_from": None,
        "valid_to": None,
        "status": "pending_review",
    }
    values.update(overrides)
    return SimpleNamespace(**values)
def _existing(**overrides):
    values = {
        "assertion_id": "assertion-old",
        "entity_type": "Specification",
        "predicate": "max_concurrent_users",
        "raw_value": 100,
        "normalized_value": 100,
        "unit": None,
        "product_version": "V1",
        "valid_from": None,
        "valid_to": None,
        "status": "published",
    }
    values.update(overrides)
    return SimpleNamespace(**values)
def test_single_value_duplicate() -> None:
    result = ConflictDetector().detect(_assertion(raw_value="100"), [_existing()])
    assert result.classification == ConflictClassification.DUPLICATE
    assert result.requires_review is False
def test_multi_value_completion() -> None:
    incoming = _assertion(predicate="supported_os", raw_value="Linux")
    existing = _existing(
        predicate="supported_os",
        raw_value=["Windows"],
        normalized_value=["Windows"],
    )
    result = ConflictDetector().detect(incoming, [existing])
    assert result.classification == ConflictClassification.COMPLETION
    assert result.normalized_incoming_value == ["Linux"]
def test_same_version_single_value_conflict() -> None:
    result = ConflictDetector().detect(
        _assertion(raw_value=200), [_existing(raw_value=100)]
    )
    assert result.classification == ConflictClassification.CONFLICT
    assert result.severity == "high"
def test_different_version_is_update() -> None:
    result = ConflictDetector().detect(
        _assertion(raw_value=200, product_version="V2"),
        [_existing(product_version="V1")],
    )
    assert result.classification == ConflictClassification.UPDATE
def test_non_overlapping_time_range_is_update() -> None:
    result = ConflictDetector().detect(
        _assertion(raw_value=200, valid_from=datetime(2026, 1, 1)),
        [_existing(valid_to=datetime(2025, 12, 31))],
    )
    assert result.classification == ConflictClassification.UPDATE
def test_unit_conversion_before_comparison() -> None:
    incoming = _assertion(predicate="weight", raw_value="1000", unit="g")
    existing = _existing(
        predicate="weight",
        raw_value="1",
        normalized_value="1",
        unit="kg",
    )
    result = ConflictDetector().detect(incoming, [existing])
    assert result.classification == ConflictClassification.DUPLICATE
    assert result.normalized_incoming_value == "1"
def test_unit_conversion_can_reveal_conflict() -> None:
    incoming = _assertion(predicate="weight", raw_value="1200", unit="g")
    existing = _existing(
        predicate="weight",
        raw_value="1",
        normalized_value="1",
        unit="kg",
    )
    result = ConflictDetector().detect(incoming, [existing])
    assert result.classification == ConflictClassification.CONFLICT
    assert result.normalized_incoming_value == "1.2"
def test_invalid_enum_is_rejected() -> None:
    result = ConflictDetector().detect(
        _assertion(predicate="supported_os", raw_value="TempleOS"),
        [],
    )
    assert result.classification == ConflictClassification.INVALID
    assert "allowed values" in result.reasons[0]
@pytest.mark.parametrize("link_status", ["ambiguous", "new_entity", "rejected"])
def test_uncertain_entity_link_requires_review(link_status: str) -> None:
    result = ConflictDetector().detect(_assertion(), [], link_status=link_status)
    assert result.classification == ConflictClassification.LINK_AMBIGUOUS
    assert result.requires_review is True
