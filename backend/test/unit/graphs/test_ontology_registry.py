from __future__ import annotations

import json
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
import yaml

from yuxi.knowledge.graphs.ontology.registry import (
    OntologyConflictError,
    OntologySpec,
    _build_ontology,
    compile_ontology_prompt,
    create_ontology_registry,
    get_ontology_registry_detail,
    import_ontology_bundle,
    list_ontology_registries,
    load_ontology,
    merge_ontology,
    normalize_ontology_aliases,
    overwrite_ontology_registry,
    parse_domain_extension,
    resolve_ontology_registry,
    validate_ontology_result,
)


def _product_ontology() -> OntologySpec:
    return _build_ontology(
        {
            "registry_id": "test",
            "version": "1.0.0",
            "name": "Product Ontology",
            "status": "active",
            "entities": {
                "Product": {"description": "可独立销售产品", "examples": ["F10"]},
                "Feature": {"description": "用户可感知能力", "examples": ["蓝牙"]},
                "Technology": {"description": "后台实现技术", "examples": ["WebRTC"]},
            },
            "relations": {
                "SUPPORTS": {"source": "Product", "target": "Feature"},
                "USES": {"source": "Product", "target": "Technology"},
            },
        },
        entity_aliases={
            "Product": {"MCSTARS": ["MCX系统", "MCSTARS平台"]},
            "Feature": {"组呼": ["群组呼叫", "群组对讲"]},
        },
        relation_aliases={
            "SUPPORTS": {"aliases": ["支持", "具备"]},
            "USES": {"aliases": ["采用", "基于"]},
        },
        properties={
            "Hardware": {
                "screen_size": {
                    "type": "float",
                    "unit": "inch",
                    "owners": ["Product"],
                    "description": "屏幕尺寸",
                },
            }
        },
        expected_registry_id="test",
    )


def test_load_generic_registry():
    entry = resolve_ontology_registry("tongyong", "1.0.0")
    ontology = load_ontology(entry.registry_id, entry.version, entry.digest)

    assert entry.name == "通用"
    assert ontology.registry_id == "tongyong"
    assert ontology.version == "1.0.0"
    assert ontology.status == "active"
    assert set(ontology.entities) == {"effect", "feature", "product", "technology"}
    assert set(ontology.relations) == {"has_effect", "has_feature", "has_tech"}


def test_load_shanli_builtin_preset_v42_is_frozen():
    entry = resolve_ontology_registry("shanli-preset", "4.2")
    ontology = load_ontology(entry.registry_id, entry.version, entry.digest)

    assert entry.name == "善理预设 V4.2（历史）"
    assert entry.digest == "df86d3187392d59cf4b9466b4c152575447f108c17f2283abd2332c522dcdb7b"
    assert entry.source == "builtin"
    assert entry.public_dict()["is_default"] is False
    assert len(ontology.entities) == 20
    assert len(ontology.relations) == 22


def test_load_shanli_builtin_preset_v43_has_exact_contract():
    expected_entities = {
        "Company", "Brand", "ProductCategory", "ProductFamily", "Product", "Solution",
        "Feature", "Technology", "Scenario", "Industry", "Specification", "Standard",
        "Certification", "DeploymentMode", "Document", "Evidence", "SellingPoint",
        "Positioning", "Roadmap", "Case", "CompatibleTarget", "Capability",
    }
    expected_relations = {
        "HAS_FAMILY", "HAS_PRODUCT", "SUPPORTS", "USES", "USED_IN", "APPLICABLE_TO",
        "COMPLIES_WITH", "HAS_CERTIFICATION", "HAS_DEPLOYMENT", "HAS_SPEC", "HAS_DOCUMENT",
        "HAS_SELLING_POINT", "HAS_POSITIONING", "HAS_CAPABILITY", "HAS_ROADMAP", "PLANS",
        "USES_PRODUCT", "COMPATIBLE_WITH", "SUPPORTED_BY", "EXTRACTED_FROM", "HAS_EVIDENCE",
        "BELONGS_TO",
    }
    entry = resolve_ontology_registry("shanli-preset", "4.3")
    ontology = load_ontology(entry.registry_id, entry.version, entry.digest)

    assert entry.name == "善理预设新版"
    assert entry.public_dict()["is_default"] is True
    assert set(ontology.entities) == expected_entities
    assert set(ontology.relations) == expected_relations
    assert all(relation.description for relation in ontology.relations.values())
    assert ontology.relation_aliases["HAS_SPEC"] == ("规格", "参数", "配置", "尺寸", "容量", "指标")
    assert ontology.relation_aliases["HAS_EVIDENCE"] == ()
    assert ontology.rules["forbidden_relations"] == ["HAS_EVIDENCE"]
    assert ontology.properties["Positioning"]["market_level"].owners == ("Positioning",)
    assert ontology.properties["Positioning"]["market_level"].enum == ("高端", "中端", "入门")
    assert "SLA" in ontology.properties["Document"]["document_type"].enum


def test_v43_rejects_new_has_evidence_relations():
    entry = resolve_ontology_registry("shanli-preset", "4.3")
    ontology = load_ontology(entry.registry_id, entry.version, entry.digest)

    with pytest.raises(ValueError, match="禁止新抽取关系类型"):
        validate_ontology_result(
            {
                "entities": [
                    {"text": "F10", "label": "Product", "attributes": []},
                    {"text": "原文证据", "label": "Evidence", "attributes": []},
                ],
                "relations": [
                    {
                        "source": {"text": "F10", "label": "Product", "attributes": []},
                        "target": {"text": "原文证据", "label": "Evidence", "attributes": []},
                        "text": "具有证据",
                        "label": "HAS_EVIDENCE",
                    }
                ],
            },
            ontology,
        )


def test_shanli_builtin_versions_require_explicit_version():
    entries = [entry for entry in list_ontology_registries() if entry.registry_id == "shanli-preset"]

    assert [entry.version for entry in entries] == ["4.2", "4.3"]
    with pytest.raises(ValueError, match="存在多个版本"):
        resolve_ontology_registry("shanli-preset")


def test_domain_extension_must_be_structured_yaml():
    with pytest.raises(ValueError, match="必须是 YAML 对象"):
        parse_domain_extension("- Product")

    with pytest.raises(ValueError, match="未知字段"):
        parse_domain_extension("custom: true")


def test_merge_domain_extension_rejects_core_override():
    ontology = _product_ontology()

    with pytest.raises(ValueError, match="不能覆盖 Core 实体"):
        merge_ontology(ontology, {"entities": {"product": {"description": "覆盖"}}})


def test_merge_domain_extension_adds_types_and_validates_references():
    ontology = merge_ontology(
        _product_ontology(),
        parse_domain_extension(
            yaml.safe_dump(
                {
                    "entities": {"Scenario": {"description": "使用场景", "examples": []}},
                    "relations": {
                        "USED_IN": {
                            "description": "产品用于场景",
                            "source": "Product",
                            "target": "Scenario",
                        }
                    },
                    "entity_aliases": {"Scenario": {"公安执法": ["执法场景"]}},
                    "relation_aliases": {"USED_IN": {"aliases": ["用于"]}},
                },
                allow_unicode=True,
            )
        ),
    )

    assert "Scenario" in ontology.entities
    assert ontology.relations["USED_IN"].source == ("Product",)
    assert ontology.entity_aliases["Scenario"]["公安执法"] == ("执法场景",)


def test_compile_prompt_is_compact_and_strict():
    prompt = compile_ontology_prompt(_product_ontology())

    assert "允许实体类型" in prompt
    assert "SUPPORTS: Product -> Feature" in prompt
    assert "Product.MCSTARS: MCX系统, MCSTARS平台" in prompt
    assert "screen_size: category=Hardware, type=float, unit=inch, owners=Product, description=屏幕尺寸" in prompt
    assert "允许属性：\n- screen_size: category=Hardware" in prompt
    assert "禁止输出 Hardware.screen_size" in prompt
    assert "禁止创建列表之外" in prompt
    assert json.dumps({"registry_id": "test"}) not in prompt


def test_alias_normalization_and_result_validation():
    ontology = _product_ontology()
    result = {
        "entities": [
            {"text": "MCX系统", "label": "Product", "attributes": [{"text": "2.4", "label": "screen_size"}]},
            {"text": "群组呼叫", "label": "Feature", "attributes": []},
        ],
        "relations": [
            {
                "source": {"text": "MCX系统", "label": "Product", "attributes": []},
                "target": {"text": "群组呼叫", "label": "Feature", "attributes": []},
                "text": "支持群组呼叫",
                "label": "支持",
            }
        ],
        "metadata": {},
    }

    normalize_ontology_aliases(result, ontology)
    validate_ontology_result(result, ontology)

    result["entities"][0]["attributes"][0]["text"] = "2.4inch"
    validate_ontology_result(result, ontology)

    assert result["entities"][0]["text"] == "MCSTARS"
    assert result["entities"][1]["text"] == "组呼"
    assert result["relations"][0]["label"] == "SUPPORTS"


def test_alias_conflicts_and_non_string_keys_are_rejected():
    with pytest.raises(ValueError, match="同时映射"):
        _build_ontology(
            {
                "registry_id": "test",
                "version": "1.0.0",
                "name": "Test",
                "status": "active",
                "entities": {"Product": {}, "Feature": {}},
                "relations": {},
            },
            entity_aliases={"Product": {"A": ["共享"], "B": ["共享"]}},
            relation_aliases={},
            properties={},
            expected_registry_id="test",
        )

    with pytest.raises(ValueError, match="名称必须是非空字符串"):
        _build_ontology(
            {
                "registry_id": "test",
                "version": "1.0.0",
                "name": "Test",
                "status": "active",
                "entities": {"Product": {}},
                "relations": {},
            },
            entity_aliases={"Product": {True: ["yes"]}},
            relation_aliases={},
            properties={},
            expected_registry_id="test",
        )


def test_property_type_and_value_are_validated():
    ontology = _product_ontology()

    with pytest.raises(ValueError, match="不符合 float"):
        validate_ontology_result(
            {
                "entities": [
                    {
                        "text": "F10",
                        "label": "Product",
                        "attributes": [{"text": "not-a-number", "label": "screen_size"}],
                    }
                ],
                "relations": [],
            },
            ontology,
        )

    with pytest.raises(ValueError, match="type 不支持"):
        _build_ontology(
            {
                "registry_id": "test",
                "version": "1.0.0",
                "name": "Test",
                "status": "active",
                "entities": {},
                "relations": {},
            },
            entity_aliases={},
            relation_aliases={},
            properties={"Hardware": {"screen_size": {"type": "interger"}}},
            expected_registry_id="test",
        )


def test_property_owner_and_enum_are_validated():
    ontology = _build_ontology(
        {
            "registry_id": "test",
            "version": "1.0.0",
            "name": "Test",
            "status": "active",
            "entities": {"Product": {}, "Positioning": {}},
            "relations": {},
        },
        entity_aliases={},
        relation_aliases={},
        properties={
            "Positioning": {
                "market_level": {
                    "type": "string",
                    "owners": ["Positioning"],
                    "enum": ["高端", "中端", "入门"],
                    "description": "市场层级",
                }
            }
        },
        expected_registry_id="test",
    )

    validate_ontology_result(
        {
            "entities": [
                {
                    "text": "旗舰定位",
                    "label": "Positioning",
                    "attributes": [{"text": "高端", "label": "market_level"}],
                }
            ],
            "relations": [],
        },
        ontology,
    )
    with pytest.raises(ValueError, match="不允许用于实体类型"):
        validate_ontology_result(
            {
                "entities": [
                    {
                        "text": "F10",
                        "label": "Product",
                        "attributes": [{"text": "高端", "label": "market_level"}],
                    }
                ],
                "relations": [],
            },
            ontology,
        )
    with pytest.raises(ValueError, match="值必须是"):
        validate_ontology_result(
            {
                "entities": [
                    {
                        "text": "旗舰定位",
                        "label": "Positioning",
                        "attributes": [{"text": "旗舰", "label": "market_level"}],
                    }
                ],
                "relations": [],
            },
            ontology,
        )


def test_property_owner_must_reference_declared_entity():
    with pytest.raises(ValueError, match="owners 引用未声明实体"):
        _build_ontology(
            {
                "registry_id": "test",
                "version": "1.0.0",
                "name": "Test",
                "status": "active",
                "entities": {"Product": {}},
                "relations": {},
            },
            entity_aliases={},
            relation_aliases={},
            properties={"General": {"name": {"type": "string", "owners": ["Unknown"]}}},
            expected_registry_id="test",
        )


def test_validation_rejects_unknown_type_property_and_wrong_direction():
    ontology = _product_ontology()

    with pytest.raises(ValueError, match="实体类型"):
        validate_ontology_result(
            {"entities": [{"text": "x", "label": "Unknown", "attributes": []}], "relations": []}, ontology
        )

    with pytest.raises(ValueError, match="不允许属性"):
        validate_ontology_result(
            {
                "entities": [{"text": "F10", "label": "Product", "attributes": [{"text": "x", "label": "bad"}]}],
                "relations": [],
            },
            ontology,
        )

    with pytest.raises(ValueError, match="不允许 source 类型"):
        validate_ontology_result(
            {
                "entities": [
                    {"text": "蓝牙", "label": "Feature", "attributes": []},
                    {"text": "F10", "label": "Product", "attributes": []},
                ],
                "relations": [
                    {
                        "source": {"text": "蓝牙", "label": "Feature", "attributes": []},
                        "target": {"text": "F10", "label": "Product", "attributes": []},
                        "text": "错误方向",
                        "label": "SUPPORTS",
                    }
                ],
            },
            ontology,
        )


def _bundle_zip(
    *,
    registry_id: str = "uploaded-test",
    version: str = "1.0.0",
    entity_description: str = "产品",
) -> bytes:
    files = {
        "schema.json": json.dumps(
            {
                "registry_id": registry_id,
                "version": version,
                "name": "Uploaded Test",
                "status": "active",
                "entities": {"Product": {"description": entity_description, "examples": []}},
                "relations": {},
            },
            ensure_ascii=False,
        ),
        "entity.yaml": "entities:\n  Product: {}\n",
        "relation.yaml": "relations: {}\n",
        "property.yaml": "properties: {}\n",
    }
    output = BytesIO()
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        for filename, content in files.items():
            archive.writestr(filename, content)
    return output.getvalue()


def test_import_bundle_is_immediately_resolvable_and_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("ONTOLOGY_REGISTRY_DIR", str(tmp_path))

    entry, already_exists = import_ontology_bundle(_bundle_zip())
    repeated_entry, repeated = import_ontology_bundle(_bundle_zip())
    resolved = resolve_ontology_registry(entry.registry_id, entry.version, entry.digest)

    assert already_exists is False
    assert repeated is True
    assert repeated_entry == entry
    assert resolved == entry
    assert entry in list_ontology_registries()
    assert load_ontology(entry.registry_id, entry.version, entry.digest).entities["Product"].description == "产品"


def test_import_bundle_rejects_same_version_with_different_content(tmp_path, monkeypatch):
    monkeypatch.setenv("ONTOLOGY_REGISTRY_DIR", str(tmp_path))
    import_ontology_bundle(_bundle_zip())

    with pytest.raises(OntologyConflictError, match="已存在"):
        import_ontology_bundle(_bundle_zip(entity_description="另一种产品"))


def test_create_registry_from_structured_content(tmp_path, monkeypatch):
    monkeypatch.setenv("ONTOLOGY_REGISTRY_DIR", str(tmp_path))

    entry, already_exists = create_ontology_registry(
        registry_id="form-test",
        version="1.0.0",
        name="Form Test",
        entities={
            "Product": {"description": "产品", "examples": ["F10"]},
            "Feature": {"description": "功能", "examples": ["组呼"]},
        },
        relations={
            "SUPPORTS": {
                "description": "产品支持功能",
                "source": ["Product"],
                "target": ["Feature"],
            }
        },
        entity_aliases={
            "Product": {"F10": ["F10终端"]},
            "Feature": {"组呼": ["群组呼叫"]},
        },
        relation_aliases={"SUPPORTS": ["支持", "具备"]},
        properties={"Hardware": {"screen_size": {"type": "float", "unit": "inch"}}},
    )
    ontology = load_ontology(entry.registry_id, entry.version, entry.digest)

    assert already_exists is False
    assert {path.name for path in entry.root.iterdir()} == {
        "schema.json",
        "entity.yaml",
        "relation.yaml",
        "property.yaml",
    }
    assert ontology.entities["Product"].description == "产品"
    assert ontology.relations["SUPPORTS"].source == ("Product",)
    assert ontology.entity_aliases["Feature"]["组呼"] == ("群组呼叫",)
    assert ontology.relation_aliases["SUPPORTS"] == ("支持", "具备")
    assert ontology.properties["Hardware"]["screen_size"].unit == "inch"


def test_structured_creation_is_order_independent_and_conflict_safe(tmp_path, monkeypatch):
    monkeypatch.setenv("ONTOLOGY_REGISTRY_DIR", str(tmp_path))
    kwargs = {
        "registry_id": "ordered-test",
        "version": "1.0.0",
        "name": "Ordered Test",
        "entities": {
            "Product": {"description": "产品", "examples": []},
            "Feature": {"description": "功能", "examples": []},
        },
        "relations": {},
        "entity_aliases": {"Product": {}, "Feature": {}},
        "relation_aliases": {},
        "properties": {},
    }
    entry, _ = create_ontology_registry(**kwargs)
    reordered = {
        **kwargs,
        "entities": dict(reversed(list(kwargs["entities"].items()))),
        "entity_aliases": dict(reversed(list(kwargs["entity_aliases"].items()))),
    }
    repeated, already_exists = create_ontology_registry(**reordered)

    assert already_exists is True
    assert repeated.digest == entry.digest

    with pytest.raises(OntologyConflictError, match="已存在"):
        create_ontology_registry(
            **{
                **kwargs,
                "entities": {
                    **kwargs["entities"],
                    "Product": {"description": "变更后的产品", "examples": []},
                },
            }
        )


def test_structured_creation_reuses_semantic_validation(tmp_path, monkeypatch):
    monkeypatch.setenv("ONTOLOGY_REGISTRY_DIR", str(tmp_path))

    with pytest.raises(ValueError, match="引用未声明实体"):
        create_ontology_registry(
            registry_id="invalid-test",
            version="1.0.0",
            name="Invalid Test",
            entities={"Product": {"description": "", "examples": []}},
            relations={"SUPPORTS": {"source": ["Product"], "target": ["Feature"]}},
            entity_aliases={"Product": {}},
            relation_aliases={"SUPPORTS": []},
            properties={},
        )


def test_detail_and_overwrite_preserve_rules(tmp_path, monkeypatch):
    monkeypatch.setenv("ONTOLOGY_REGISTRY_DIR", str(tmp_path))
    entry, _ = create_ontology_registry(
        registry_id="editable-test",
        version="1.0.0",
        name="Editable",
        entities={"Product": {"description": "旧说明", "examples": []}},
        relations={},
        entity_aliases={"Product": {}},
        relation_aliases={},
        properties={},
        rules={"feature_rule": {"rule": "保留规则", "examples": ["示例"]}},
    )
    detail = get_ontology_registry_detail(entry.registry_id, entry.version, entry.digest)

    updated, previous_digest, changed = overwrite_ontology_registry(
        registry_id=entry.registry_id,
        version=entry.version,
        expected_digest=entry.digest,
        name="Editable Updated",
        entities={"Product": {"description": "新说明", "examples": []}},
        relations={},
        entity_aliases={"Product": {}},
        relation_aliases={},
        properties={},
        rules=detail["definition"]["rules"],
    )
    loaded = load_ontology(updated.registry_id, updated.version, updated.digest)

    assert detail["item"]["editable"] is True
    assert detail["definition"]["rules"]["feature_rule"]["rule"] == "保留规则"
    assert changed is True
    assert previous_digest == entry.digest
    assert updated.digest != entry.digest
    assert loaded.name == "Editable Updated"
    assert loaded.rules == detail["definition"]["rules"]
    assert resolve_ontology_registry(updated.registry_id, updated.version).digest == updated.digest
    with pytest.raises(ValueError, match="未找到"):
        resolve_ontology_registry(entry.registry_id, entry.version, entry.digest)


def test_detail_preserves_property_constraints(tmp_path, monkeypatch):
    monkeypatch.setenv("ONTOLOGY_REGISTRY_DIR", str(tmp_path))
    entry, _ = create_ontology_registry(
        registry_id="property-roundtrip",
        version="1.0.0",
        name="Property Roundtrip",
        entities={"Positioning": {"description": "定位", "examples": []}},
        relations={},
        entity_aliases={},
        relation_aliases={},
        properties={
            "Positioning": {
                "market_level": {
                    "type": "string",
                    "owners": ["Positioning"],
                    "enum": ["高端", "中端", "入门"],
                    "description": "市场层级",
                }
            }
        },
    )

    detail = get_ontology_registry_detail(entry.registry_id, entry.version, entry.digest)

    assert detail["definition"]["properties"] == [
        {
            "category": "Positioning",
            "name": "market_level",
            "type": "string",
            "unit": None,
            "owners": ["Positioning"],
            "enum": ["高端", "中端", "入门"],
            "description": "市场层级",
        }
    ]


def test_builtin_ontology_cannot_be_overwritten():
    entry = resolve_ontology_registry("shanli-preset", "4.2")
    detail = get_ontology_registry_detail(entry.registry_id, entry.version, entry.digest)

    with pytest.raises(OntologyConflictError, match="内置 Ontology 只读"):
        overwrite_ontology_registry(
            registry_id=entry.registry_id,
            version=entry.version,
            expected_digest=entry.digest,
            name=detail["definition"]["name"],
            entities={
                item["name"]: {
                    "description": item["description"],
                    "examples": item["examples"],
                }
                for item in detail["definition"]["entities"]
            },
            relations={
                item["name"]: {
                    "description": item["description"],
                    "source": item["source"],
                    "target": item["target"],
                }
                for item in detail["definition"]["relations"]
            },
            entity_aliases={
                item["name"]: {alias["canonical"]: alias["aliases"] for alias in item["canonical_aliases"]}
                for item in detail["definition"]["entities"]
            },
            relation_aliases={item["name"]: item["aliases"] for item in detail["definition"]["relations"]},
            properties={},
            rules=detail["definition"]["rules"],
        )


def test_uploaded_bundle_detects_extra_file_tampering(tmp_path, monkeypatch):
    monkeypatch.setenv("ONTOLOGY_REGISTRY_DIR", str(tmp_path))
    entry, _ = import_ontology_bundle(_bundle_zip())
    (entry.root / "unexpected.txt").write_text("tampered", encoding="utf-8")

    with pytest.raises(ValueError, match="必须且只能包含"):
        resolve_ontology_registry(entry.registry_id, entry.version, entry.digest)
