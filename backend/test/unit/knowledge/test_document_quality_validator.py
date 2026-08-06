from __future__ import annotations

from yuxi.knowledge.utils.document_quality_validator import validate_clean_result


def test_no_warnings_when_faithful():
    original = "MCSTARS 支持 4GB 内存和 WiFi。"
    cleaned = "MCSTARS 支持 4GB 内存和 WiFi。"
    result = validate_clean_result(original, cleaned)
    assert result["ok"] is True
    assert result["warnings"] == []
    assert result["should_fallback"] is False


def test_warns_on_missing_numbers():
    original = "支持 4GB 内存、32GB 存储和 12.5% 速率。"
    cleaned = "支持大容量内存和高存储。"
    result = validate_clean_result(original, cleaned)
    assert result["ok"] is False
    assert any("数字" in w for w in result["warnings"])


def test_warns_on_missing_entities():
    original = "MCSTARS 与 POCSTARS 提供融合定位。"
    cleaned = "产品提供融合定位。"
    result = validate_clean_result(original, cleaned)
    assert result["ok"] is False
    assert any("专有名词" in w for w in result["warnings"])


def test_should_fallback_on_severe_shrink():
    original = "这是一段非常长的原文内容。" * 100
    cleaned = "太短了"
    result = validate_clean_result(original, cleaned)
    assert result["should_fallback"] is True
    assert any("缩减" in w for w in result["warnings"])


def test_warns_on_length_inflation():
    original = "原文。"
    cleaned = "原文。" + ("额外内容。" * 30)
    result = validate_clean_result(original, cleaned)
    assert result["ok"] is False
    assert any("膨胀" in w for w in result["warnings"])
