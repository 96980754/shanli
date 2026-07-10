from app.services.merge_engine import MergeEngine


def test_merge_scalar_field_deduplicates_same_numeric_value_and_merges_sources():
    records = [
        {"数值": 4800, "单位": "mAh", "来源图片": [4]},
        {"数值": 4800, "单位": "mAh", "来源图片": [8]},
    ]

    result = MergeEngine.merge_scalar_field(records)

    assert result["冲突"] is False
    assert result["值"] == 4800
    assert result["来源图片"] == [4, 8]


def test_merge_scalar_field_marks_conflict_when_values_differ():
    records = [
        {"数值": 4800, "单位": "mAh", "来源图片": [4]},
        {"数值": 5000, "单位": "mAh", "来源图片": [8]},
    ]

    result = MergeEngine.merge_scalar_field(records)

    assert result["冲突"] is True
    assert {item["值"] for item in result["各值"]} == {4800, 5000}


def test_merge_list_field_returns_union_and_merges_sources():
    items = [
        {"值": "GPS定位", "来源图片": [1]},
        {"值": "GPS定位", "来源图片": [8]},
        {"值": "手电筒", "来源图片": [3]},
    ]

    result = MergeEngine.merge_list_field(items)

    assert len(result) == 2
    merged = {item["值"]: item["来源图片"] for item in result}
    assert merged["GPS定位"] == [1, 8]
    assert merged["手电筒"] == [3]


def test_detect_identity_conflict_returns_conflict_when_identifiers_differ():
    records = [
        {"值": "P368", "来源图片": [1]},
        {"值": "P369", "来源图片": [2]},
    ]

    result = MergeEngine.detect_identity_conflict(records)

    assert result["冲突"] is True
    assert {item["值"] for item in result["各值"]} == {"P368", "P369"}
    assert set(result["涉及图片"]) == {1, 2}
