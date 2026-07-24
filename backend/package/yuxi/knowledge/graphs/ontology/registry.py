from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import struct
import tempfile
from dataclasses import dataclass
from functools import cache
from io import BytesIO
from pathlib import Path, PurePosixPath
from typing import Any, Literal
from zipfile import BadZipFile, ZIP_DEFLATED, ZIP_STORED, ZipFile, ZipInfo

import yaml

from yuxi import config

DEFAULT_ONTOLOGY_REGISTRY_ID = "V4.1"
_BUILTIN_REGISTRY_IDS = (DEFAULT_ONTOLOGY_REGISTRY_ID, "Shanli")
_ONTOLOGY_ROOT = Path(__file__).parent
_BUNDLE_FILENAMES = ("schema.json", "entity.yaml", "relation.yaml", "property.yaml")
_CURRENT_FILENAME = "current.json"
_RESERVED_SCHEMA_FIELDS = {"registry_id", "version", "name", "status", "entities", "relations"}
_ALLOWED_STATUSES = {"scaffold", "active"}
_ALLOWED_PROPERTY_TYPES = {"string", "int", "integer", "float", "number", "bool", "boolean"}
_ALLOWED_DOMAIN_FIELDS = {"entities", "relations", "entity_aliases", "relation_aliases", "properties"}
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
MAX_ONTOLOGY_ZIP_BYTES = 5 * 1024 * 1024
MAX_ONTOLOGY_FILE_BYTES = 4 * 1024 * 1024
MAX_ONTOLOGY_TOTAL_BYTES = 8 * 1024 * 1024
MAX_ONTOLOGY_COMPRESSION_RATIO = 100


class OntologyConflictError(ValueError):
    pass


@dataclass(frozen=True)
class EntityDefinition:
    name: str
    description: str
    examples: tuple[str, ...]


@dataclass(frozen=True)
class RelationDefinition:
    name: str
    description: str
    source: tuple[str, ...]
    target: tuple[str, ...]


@dataclass(frozen=True)
class PropertyDefinition:
    category: str
    name: str
    value_type: str
    unit: str | None


@dataclass(frozen=True)
class OntologySpec:
    registry_id: str
    version: str
    name: str
    status: str
    entities: dict[str, EntityDefinition]
    relations: dict[str, RelationDefinition]
    entity_aliases: dict[str, dict[str, tuple[str, ...]]]
    relation_aliases: dict[str, tuple[str, ...]]
    properties: dict[str, dict[str, PropertyDefinition]]
    rules: dict[str, Any]


@dataclass(frozen=True)
class OntologyRegistryEntry:
    registry_id: str
    version: str
    digest: str
    name: str
    status: str
    source: Literal["builtin", "uploaded"]
    root: Path

    def public_dict(self) -> dict[str, Any]:
        return {
            "registry_id": self.registry_id,
            "version": self.version,
            "digest": self.digest,
            "name": self.name,
            "status": self.status,
            "source": self.source,
        }


def get_uploaded_registry_root() -> Path:
    configured = str(os.getenv("ONTOLOGY_REGISTRY_DIR") or "").strip()
    return Path(configured) if configured else Path(config.save_dir) / "ontology_registry"


def list_ontology_registries() -> list[OntologyRegistryEntry]:
    entries = [_entry_from_root(_ONTOLOGY_ROOT / registry_id, "builtin") for registry_id in _BUILTIN_REGISTRY_IDS]
    bundles_root = get_uploaded_registry_root() / "bundles"
    if bundles_root.is_dir():
        for registry_dir in bundles_root.iterdir():
            if not registry_dir.is_dir() or registry_dir.name.startswith("."):
                continue
            for version_dir in registry_dir.iterdir():
                if not version_dir.is_dir() or version_dir.name.startswith("."):
                    continue
                digest_dir = _active_digest_root(version_dir)
                entry = _entry_from_root(digest_dir, "uploaded")
                if entry.registry_id != registry_dir.name or entry.version != version_dir.name:
                    raise ValueError(f"Ontology 目录身份与 schema.json 不一致: {digest_dir}")
                if entry.digest != digest_dir.name:
                    raise ValueError(f"Ontology digest 与目录名不一致: {digest_dir}")
                entries.append(entry)

    by_identity: dict[tuple[str, str], OntologyRegistryEntry] = {}
    for entry in entries:
        key = (entry.registry_id.casefold(), entry.version.casefold())
        existing = by_identity.get(key)
        if existing and existing.digest != entry.digest:
            raise OntologyConflictError(
                f"Ontology {entry.registry_id}@{entry.version} 存在不同内容: {existing.digest} / {entry.digest}"
            )
        by_identity[key] = existing or entry
    return sorted(
        by_identity.values(),
        key=lambda item: (item.registry_id.casefold(), item.version.casefold(), item.digest),
    )


def resolve_ontology_registry(
    registry_id: str,
    version: str | None = None,
    digest: str | None = None,
) -> OntologyRegistryEntry:
    normalized_id = str(registry_id or "").strip()
    if not normalized_id:
        raise ValueError("ontology_registry_id 不能为空")
    matches = [entry for entry in list_ontology_registries() if entry.registry_id == normalized_id]
    if version:
        matches = [entry for entry in matches if entry.version == version]
    if digest:
        matches = [entry for entry in matches if entry.digest == digest]
    if not matches:
        identity = "@".join(item for item in (normalized_id, version, digest) if item)
        raise ValueError(f"未找到 Ontology Registry: {identity}")
    if len(matches) > 1:
        raise ValueError(f"Ontology Registry {normalized_id} 存在多个版本，请指定 ontology_version")
    return matches[0]


def load_ontology(
    registry_id: str = DEFAULT_ONTOLOGY_REGISTRY_ID,
    version: str | None = None,
    digest: str | None = None,
) -> OntologySpec:
    entry = resolve_ontology_registry(registry_id, version, digest)
    return _load_ontology_by_identity(
        entry.registry_id,
        entry.version,
        entry.digest,
        str(entry.root.resolve()),
    )


@cache
def _load_ontology_by_identity(registry_id: str, version: str, digest: str, root: str) -> OntologySpec:
    entry = _entry_from_root(Path(root), "builtin" if Path(root).parent == _ONTOLOGY_ROOT else "uploaded")
    if (entry.registry_id, entry.version, entry.digest) != (registry_id, version, digest):
        raise ValueError(f"Ontology 文件内容已变化: {registry_id}@{version}")
    return _load_ontology_from_root(Path(root), expected_registry_id=registry_id)


def import_ontology_bundle(data: bytes) -> tuple[OntologyRegistryEntry, bool]:
    return _publish_ontology_files(_read_bundle_zip(data))


def create_ontology_registry(
    *,
    registry_id: str,
    version: str,
    name: str,
    entities: dict[str, Any],
    relations: dict[str, Any],
    entity_aliases: dict[str, Any],
    relation_aliases: dict[str, Any],
    properties: dict[str, Any],
    rules: dict[str, Any] | None = None,
) -> tuple[OntologyRegistryEntry, bool]:
    files = _serialize_ontology_files(
        registry_id=registry_id,
        version=version,
        name=name,
        status="active",
        entities=entities,
        relations=relations,
        entity_aliases=entity_aliases,
        relation_aliases=relation_aliases,
        properties=properties,
        rules=rules,
    )
    return _publish_ontology_files(files)


def get_ontology_registry_detail(
    registry_id: str,
    version: str,
    digest: str,
) -> dict[str, Any]:
    entry = resolve_ontology_registry(registry_id, version, digest)
    ontology = load_ontology(entry.registry_id, entry.version, entry.digest)
    entities = []
    for entity_name in sorted(ontology.entities, key=str.casefold):
        entity = ontology.entities[entity_name]
        aliases = ontology.entity_aliases.get(entity_name, {})
        entities.append(
            {
                "name": entity.name,
                "description": entity.description,
                "examples": list(entity.examples),
                "canonical_aliases": [
                    {"canonical": canonical, "aliases": list(aliases[canonical])}
                    for canonical in sorted(aliases, key=str.casefold)
                ],
            }
        )
    relations = [
        {
            "name": relation.name,
            "description": relation.description,
            "source": list(relation.source),
            "target": list(relation.target),
            "aliases": list(ontology.relation_aliases.get(relation.name, ())),
        }
        for relation in sorted(ontology.relations.values(), key=lambda item: item.name.casefold())
    ]
    properties = [
        {
            "category": category,
            "name": property_definition.name,
            "type": property_definition.value_type,
            "unit": property_definition.unit,
        }
        for category in sorted(ontology.properties, key=str.casefold)
        for property_definition in sorted(ontology.properties[category].values(), key=lambda item: item.name.casefold())
    ]
    return {
        "item": {**entry.public_dict(), "editable": entry.source == "uploaded"},
        "definition": {
            "name": ontology.name,
            "entities": entities,
            "relations": relations,
            "properties": properties,
            "rules": ontology.rules,
        },
    }


def overwrite_ontology_registry(
    *,
    registry_id: str,
    version: str,
    expected_digest: str,
    name: str,
    entities: dict[str, Any],
    relations: dict[str, Any],
    entity_aliases: dict[str, Any],
    relation_aliases: dict[str, Any],
    properties: dict[str, Any],
    rules: dict[str, Any],
) -> tuple[OntologyRegistryEntry, str, bool]:
    current = resolve_ontology_registry(registry_id, version, expected_digest)
    if current.source != "uploaded":
        raise OntologyConflictError("内置 Ontology 只读，不能在线编辑")
    files = _serialize_ontology_files(
        registry_id=registry_id,
        version=version,
        name=name,
        status=current.status,
        entities=entities,
        relations=relations,
        entity_aliases=entity_aliases,
        relation_aliases=relation_aliases,
        properties=properties,
        rules=rules,
    )
    new_digest = _compute_digest(files)
    if new_digest == current.digest:
        return current, current.digest, False

    version_root = current.root.parent
    active = _active_digest_root(version_root)
    if active.name != expected_digest:
        raise OntologyConflictError("Ontology 已被其他请求修改，请刷新后重试")
    target = version_root / new_digest
    if target.exists():
        candidate = _entry_from_root(target, "uploaded")
        if candidate.digest != new_digest:
            raise ValueError("Ontology 已有 digest 目录内容不一致")
    else:
        staging = Path(tempfile.mkdtemp(prefix=f".{new_digest}.", dir=version_root))
        try:
            for filename in _BUNDLE_FILENAMES:
                (staging / filename).write_bytes(files[filename])
            candidate = _entry_from_root(staging, "uploaded")
            if candidate.digest != new_digest:
                raise ValueError("Ontology staging digest 校验失败")
            staging.rename(target)
        finally:
            if staging.exists():
                shutil.rmtree(staging)
    _write_current_digest(version_root, new_digest)
    _load_ontology_by_identity.cache_clear()
    return _entry_from_root(target, "uploaded"), current.digest, True


def _serialize_ontology_files(
    *,
    registry_id: str,
    version: str,
    name: str,
    status: str,
    entities: dict[str, Any],
    relations: dict[str, Any],
    entity_aliases: dict[str, Any],
    relation_aliases: dict[str, Any],
    properties: dict[str, Any],
    rules: dict[str, Any] | None,
) -> dict[str, bytes]:
    rules = dict(rules or {})
    reserved = sorted(set(rules) & _RESERVED_SCHEMA_FIELDS)
    if reserved:
        raise ValueError(f"Ontology rules 不能覆盖保留字段: {', '.join(reserved)}")
    schema = {
        "registry_id": registry_id,
        "version": version,
        "name": name,
        "status": status,
        "entities": _sorted_mapping(entities),
        "relations": _sorted_mapping(relations),
        **_sorted_mapping(rules),
    }
    entity_data = {"entities": _sorted_nested_mapping(entity_aliases)}
    relation_data = {
        "relations": {
            relation_name: {"aliases": aliases}
            for relation_name, aliases in sorted(relation_aliases.items(), key=lambda item: item[0].casefold())
        }
    }
    property_data = {"properties": _sorted_nested_mapping(properties)}
    files = {
        "schema.json": (json.dumps(schema, ensure_ascii=False, indent=2) + "\n").encode(),
        "entity.yaml": yaml.safe_dump(entity_data, allow_unicode=True, sort_keys=False).encode(),
        "relation.yaml": yaml.safe_dump(relation_data, allow_unicode=True, sort_keys=False).encode(),
        "property.yaml": yaml.safe_dump(property_data, allow_unicode=True, sort_keys=False).encode(),
    }
    _validate_generated_files(files)
    _build_ontology_from_files(files)
    return files


def _publish_ontology_files(files: dict[str, bytes]) -> tuple[OntologyRegistryEntry, bool]:
    _validate_generated_files(files)

    digest = _compute_digest(files)
    spec = _build_ontology_from_files(files)
    _validate_identity(spec.registry_id, "registry_id")
    _validate_identity(spec.version, "version")

    existing = [
        entry
        for entry in list_ontology_registries()
        if entry.registry_id.casefold() == spec.registry_id.casefold()
        and entry.version.casefold() == spec.version.casefold()
    ]
    if existing:
        entry = existing[0]
        if entry.digest == digest:
            return entry, True
        raise OntologyConflictError(f"Ontology {spec.registry_id}@{spec.version} 已存在，请提升 version 后重试")

    registry_root = get_uploaded_registry_root() / "bundles" / spec.registry_id
    target_version = registry_root / spec.version
    target = target_version / digest
    registry_root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{spec.version}.{digest}.", dir=registry_root))
    try:
        staged_bundle = staging / digest
        staged_bundle.mkdir()
        for filename in _BUNDLE_FILENAMES:
            (staged_bundle / filename).write_bytes(files[filename])
        staged_entry = _entry_from_root(staged_bundle, "uploaded")
        if staged_entry.digest != digest:
            raise ValueError("Ontology staging digest 校验失败")
        try:
            staging.rename(target_version)
        except FileExistsError:
            active = _active_digest_root(target_version)
            current = _entry_from_root(active, "uploaded")
            if current.digest != digest:
                raise OntologyConflictError(f"Ontology {spec.registry_id}@{spec.version} 已存在不同内容")
            return current, True
        _write_current_digest(target_version, digest)
        return _entry_from_root(target, "uploaded"), False
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def _validate_generated_files(files: dict[str, bytes]) -> None:
    if set(files) != set(_BUNDLE_FILENAMES):
        raise ValueError(f"Ontology 文件必须是: {', '.join(_BUNDLE_FILENAMES)}")
    if any(not isinstance(data, bytes) for data in files.values()):
        raise ValueError("Ontology 文件内容必须是 bytes")
    for filename, data in files.items():
        if len(data) > MAX_ONTOLOGY_FILE_BYTES:
            raise ValueError(f"Ontology 文件不能超过 4 MiB: {filename}")
    if sum(map(len, files.values())) > MAX_ONTOLOGY_TOTAL_BYTES:
        raise ValueError("Ontology 文件总大小不能超过 8 MiB")


def _active_digest_root(version_root: Path) -> Path:
    pointer = version_root / _CURRENT_FILENAME
    if pointer.is_file():
        try:
            value = json.loads(pointer.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Ontology current 指针无效: {version_root}") from exc
        digest = value.get("digest") if isinstance(value, dict) else None
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError(f"Ontology current digest 无效: {version_root}")
        root = version_root / digest
        if not root.is_dir():
            raise ValueError(f"Ontology current digest 目录不存在: {version_root}")
        return root

    roots = [path for path in version_root.iterdir() if path.is_dir() and not path.name.startswith(".")]
    if len(roots) != 1:
        raise ValueError(f"Ontology 版本目录必须有唯一生效 digest: {version_root}")
    return roots[0]


def _write_current_digest(version_root: Path, digest: str) -> None:
    fd, temp_path = tempfile.mkstemp(prefix=".current.", suffix=".json", dir=version_root)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump({"digest": digest}, file)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp_path, version_root / _CURRENT_FILENAME)
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


def _sorted_mapping(values: dict[str, Any]) -> dict[str, Any]:
    return {key: values[key] for key in sorted(values, key=str.casefold)}


def _sorted_nested_mapping(values: dict[str, Any]) -> dict[str, Any]:
    result = {}
    for key in sorted(values, key=str.casefold):
        nested = values[key]
        result[key] = _sorted_mapping(nested) if isinstance(nested, dict) else nested
    return result


def parse_domain_extension(raw: str | None) -> dict[str, Any]:
    if not raw or not raw.strip():
        return {}
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise ValueError(f"领域 Ontology 扩展 YAML 无效: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("领域 Ontology 扩展必须是 YAML 对象")
    unknown_fields = sorted(set(data) - _ALLOWED_DOMAIN_FIELDS)
    if unknown_fields:
        raise ValueError(f"领域 Ontology 扩展包含未知字段: {', '.join(unknown_fields)}")
    for field in _ALLOWED_DOMAIN_FIELDS:
        value = data.get(field)
        if value is not None and not isinstance(value, dict):
            raise ValueError(f"领域 Ontology 扩展的 {field} 必须是对象")
    return data


def merge_ontology(core: OntologySpec, extension: dict[str, Any]) -> OntologySpec:
    if not extension:
        return core

    entity_schema = extension.get("entities", {})
    relation_schema = extension.get("relations", {})
    entity_aliases = extension.get("entity_aliases", {})
    relation_aliases = extension.get("relation_aliases", {})
    properties = extension.get("properties", {})

    _reject_core_overrides(core.entities, entity_schema, "实体")
    _reject_core_overrides(core.relations, relation_schema, "关系")
    _reject_core_overrides(core.entity_aliases, entity_aliases, "实体词典类型")
    _reject_core_overrides(core.relation_aliases, relation_aliases, "关系词典")
    _reject_core_overrides(core.properties, properties, "属性分类")

    merged_schema = {
        "registry_id": core.registry_id,
        "version": core.version,
        "name": core.name,
        "status": core.status,
        "entities": {
            **{
                name: {"description": item.description, "examples": list(item.examples)}
                for name, item in core.entities.items()
            },
            **entity_schema,
        },
        "relations": {
            **{
                name: {
                    "description": item.description,
                    "source": list(item.source),
                    "target": list(item.target),
                }
                for name, item in core.relations.items()
            },
            **relation_schema,
        },
        **core.rules,
    }
    merged_entity_aliases = {
        **{
            kind: {name: list(aliases) for name, aliases in values.items()}
            for kind, values in core.entity_aliases.items()
        },
        **entity_aliases,
    }
    merged_relation_aliases = {
        **{name: {"aliases": list(aliases)} for name, aliases in core.relation_aliases.items()},
        **relation_aliases,
    }
    merged_properties = {
        **{
            category: {
                name: {"type": item.value_type, **({"unit": item.unit} if item.unit else {})}
                for name, item in values.items()
            }
            for category, values in core.properties.items()
        },
        **properties,
    }
    return _build_ontology(
        merged_schema,
        entity_aliases=merged_entity_aliases,
        relation_aliases=merged_relation_aliases,
        properties=merged_properties,
        expected_registry_id=core.registry_id,
    )


def compile_ontology_prompt(ontology: OntologySpec) -> str:
    entity_lines = [
        f"- {item.name}: {item.description}" if item.description else f"- {item.name}"
        for item in ontology.entities.values()
    ]
    relation_lines = []
    for item in ontology.relations.values():
        source = " | ".join(item.source)
        target = " | ".join(item.target)
        description = f"，{item.description}" if item.description else ""
        relation_lines.append(f"- {item.name}: {source} -> {target}{description}")

    entity_alias_lines = []
    for entity_type, values in ontology.entity_aliases.items():
        for canonical, aliases in values.items():
            entity_alias_lines.append(f"- {entity_type}.{canonical}: {', '.join(aliases)}")

    relation_alias_lines = [
        f"- {name}: {', '.join(aliases)}" for name, aliases in ontology.relation_aliases.items() if aliases
    ]
    property_lines = []
    for category, values in ontology.properties.items():
        for item in values.values():
            suffix = f", unit={item.unit}" if item.unit else ""
            property_lines.append(f"- {category}.{item.name}: type={item.value_type}{suffix}")

    sections = [
        "你必须严格按照指定 Ontology 抽取实体、关系和属性。",
        "允许实体类型：\n" + ("\n".join(entity_lines) if entity_lines else "- 无"),
        "允许关系及方向：\n" + ("\n".join(relation_lines) if relation_lines else "- 无"),
    ]
    if entity_alias_lines:
        sections.append("实体标准名与别名：\n" + "\n".join(entity_alias_lines))
    if relation_alias_lines:
        sections.append("关系别名映射：\n" + "\n".join(relation_alias_lines))
    if property_lines:
        sections.append("允许属性：\n" + "\n".join(property_lines))
    for rule_name, rule in ontology.rules.items():
        if isinstance(rule, dict) and rule.get("rule"):
            sections.append(f"规则 {rule_name}: {rule['rule']}")

    sections.append(
        "强制规则：\n"
        "1. 实体 label 必须使用允许的实体类型。\n"
        "2. 关系 label 和 source/target 类型必须符合允许关系及方向。\n"
        "3. 属性 label 必须使用允许属性的 key。\n"
        "4. 禁止创建列表之外的实体类型、关系类型或属性。\n"
        "5. 文本中没有符合 Ontology 的事实时，返回空 entities 和 relations。"
    )
    return "\n\n".join(sections)


def normalize_ontology_aliases(result: dict[str, Any], ontology: OntologySpec) -> dict[str, Any]:
    entity_lookup: dict[tuple[str, str], str] = {}
    for entity_type, values in ontology.entity_aliases.items():
        for canonical, aliases in values.items():
            for alias in {canonical, *aliases}:
                entity_lookup[(entity_type, _normalize_alias(alias))] = canonical

    relation_lookup: dict[str, str] = {}
    for canonical, aliases in ontology.relation_aliases.items():
        for alias in {canonical, *aliases}:
            relation_lookup[_normalize_alias(alias)] = canonical

    for entity in result.get("entities", []):
        canonical = entity_lookup.get((entity.get("label", ""), _normalize_alias(entity.get("text", ""))))
        if canonical:
            entity["text"] = canonical
    for relation in result.get("relations", []):
        canonical = relation_lookup.get(_normalize_alias(relation.get("label", "")))
        if canonical:
            relation["label"] = canonical
        for endpoint_name in ("source", "target"):
            endpoint = relation.get(endpoint_name) or {}
            canonical_entity = entity_lookup.get(
                (endpoint.get("label", ""), _normalize_alias(endpoint.get("text", "")))
            )
            if canonical_entity:
                endpoint["text"] = canonical_entity
    return result


def validate_ontology_result(result: dict[str, Any], ontology: OntologySpec) -> None:
    allowed_entities = set(ontology.entities)
    properties_by_name = {item.name: item for values in ontology.properties.values() for item in values.values()}

    for entity in result.get("entities", []):
        label = entity.get("label")
        if label not in allowed_entities:
            raise ValueError(f"Ontology 不允许实体类型: {label}")
        for attribute in entity.get("attributes", []):
            attribute_label = attribute.get("label")
            definition = properties_by_name.get(attribute_label)
            if definition is None:
                raise ValueError(f"Ontology 不允许属性: {attribute_label}")
            _validate_property_value(attribute.get("text"), definition)

    for relation in result.get("relations", []):
        label = relation.get("label")
        definition = ontology.relations.get(label)
        if definition is None:
            raise ValueError(f"Ontology 不允许关系类型: {label}")
        source_label = (relation.get("source") or {}).get("label")
        target_label = (relation.get("target") or {}).get("label")
        if not _matches_endpoint(source_label, definition.source):
            raise ValueError(f"关系 {label} 不允许 source 类型: {source_label}")
        if not _matches_endpoint(target_label, definition.target):
            raise ValueError(f"关系 {label} 不允许 target 类型: {target_label}")


def _entry_from_root(root: Path, source: Literal["builtin", "uploaded"]) -> OntologyRegistryEntry:
    files = _read_bundle_files(root)
    spec = _build_ontology_from_files(files)
    return OntologyRegistryEntry(
        registry_id=spec.registry_id,
        version=spec.version,
        digest=_compute_digest(files),
        name=spec.name,
        status=spec.status,
        source=source,
        root=root,
    )


def _read_bundle_files(root: Path) -> dict[str, bytes]:
    try:
        paths = list(root.iterdir())
    except FileNotFoundError as exc:
        raise ValueError(f"Ontology 目录不存在: {root}") from exc
    if {path.name for path in paths} != set(_BUNDLE_FILENAMES):
        raise ValueError(f"Ontology 目录必须且只能包含: {', '.join(_BUNDLE_FILENAMES)}")
    if any(path.is_symlink() or not path.is_file() for path in paths):
        raise ValueError(f"Ontology 目录只允许普通文件: {root}")
    return {filename: (root / filename).read_bytes() for filename in _BUNDLE_FILENAMES}


def _build_ontology_from_files(files: dict[str, bytes]) -> OntologySpec:
    schema = _decode_json(files["schema.json"], "schema.json")
    entity_data = _decode_yaml(files["entity.yaml"], "entity.yaml")
    relation_data = _decode_yaml(files["relation.yaml"], "relation.yaml")
    property_data = _decode_yaml(files["property.yaml"], "property.yaml")
    return _build_ontology(
        schema,
        entity_aliases=entity_data.get("entities", {}),
        relation_aliases=relation_data.get("relations", {}),
        properties=property_data.get("properties", {}),
        expected_registry_id=_required_text(schema, "registry_id"),
    )


def _load_ontology_from_root(root: Path, *, expected_registry_id: str) -> OntologySpec:
    files = _read_bundle_files(root)
    spec = _build_ontology_from_files(files)
    if spec.registry_id != expected_registry_id:
        raise ValueError(f"Ontology registry_id 不匹配: 期望 {expected_registry_id}，实际 {spec.registry_id}")
    return spec


def _compute_digest(files: dict[str, bytes]) -> str:
    hasher = hashlib.sha256()
    for filename in _BUNDLE_FILENAMES:
        data = files[filename]
        encoded_name = filename.encode("utf-8")
        hasher.update(struct.pack(">I", len(encoded_name)))
        hasher.update(encoded_name)
        hasher.update(struct.pack(">Q", len(data)))
        hasher.update(data)
    return hasher.hexdigest()


def _read_bundle_zip(data: bytes) -> dict[str, bytes]:
    if not data:
        raise ValueError("Ontology ZIP 不能为空")
    if len(data) > MAX_ONTOLOGY_ZIP_BYTES:
        raise ValueError("Ontology ZIP 不能超过 5 MiB")
    try:
        with ZipFile(BytesIO(data)) as archive:
            infos = archive.infolist()
            if len(infos) != len(_BUNDLE_FILENAMES):
                raise ValueError("Ontology ZIP 必须且只能包含四个根目录文件")
            normalized: dict[str, ZipInfo] = {}
            total_size = 0
            for info in infos:
                filename = _validate_zip_info(info)
                if filename in normalized:
                    raise ValueError(f"Ontology ZIP 文件重复: {filename}")
                normalized[filename] = info
                total_size += info.file_size
            if set(normalized) != set(_BUNDLE_FILENAMES):
                raise ValueError(f"Ontology ZIP 文件必须是: {', '.join(_BUNDLE_FILENAMES)}")
            if total_size > MAX_ONTOLOGY_TOTAL_BYTES:
                raise ValueError("Ontology ZIP 解压后不能超过 8 MiB")
            files = {filename: archive.read(normalized[filename]) for filename in _BUNDLE_FILENAMES}
    except BadZipFile as exc:
        raise ValueError(f"Ontology ZIP 无效或已损坏: {exc}") from exc
    return files


def _validate_zip_info(info: ZipInfo) -> str:
    filename = info.filename
    if not filename or "\x00" in filename or "\\" in filename:
        raise ValueError("Ontology ZIP 包含非法文件名")
    path = PurePosixPath(filename)
    if info.is_dir() or path.is_absolute() or len(path.parts) != 1 or ".." in path.parts or ":" in filename:
        raise ValueError(f"Ontology ZIP 只允许根目录普通文件: {filename}")
    if info.flag_bits & 0x1:
        raise ValueError(f"Ontology ZIP 不允许加密文件: {filename}")
    if info.compress_type not in {ZIP_STORED, ZIP_DEFLATED}:
        raise ValueError(f"Ontology ZIP 不支持该压缩算法: {filename}")
    mode = info.external_attr >> 16
    file_type = stat.S_IFMT(mode)
    if file_type and file_type != stat.S_IFREG:
        raise ValueError(f"Ontology ZIP 不允许非普通文件: {filename}")
    if info.file_size > MAX_ONTOLOGY_FILE_BYTES:
        raise ValueError(f"Ontology 文件不能超过 4 MiB: {filename}")
    if info.file_size and info.compress_size == 0:
        raise ValueError(f"Ontology ZIP 压缩信息无效: {filename}")
    if info.compress_size and info.file_size / info.compress_size > MAX_ONTOLOGY_COMPRESSION_RATIO:
        raise ValueError(f"Ontology ZIP 压缩比过高: {filename}")
    return filename


def _decode_json(data: bytes, filename: str) -> dict[str, Any]:
    try:
        value = json.loads(data.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise ValueError(f"Ontology 文件必须是 UTF-8: {filename}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Ontology JSON 无效: {filename}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"Ontology 文件必须是对象: {filename}")
    return value


def _decode_yaml(data: bytes, filename: str) -> dict[str, Any]:
    try:
        value = yaml.safe_load(data.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise ValueError(f"Ontology 文件必须是 UTF-8: {filename}") from exc
    except yaml.YAMLError as exc:
        raise ValueError(f"Ontology YAML 无效: {filename}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"Ontology 文件必须是对象: {filename}")
    return value


def _build_ontology(
    schema: dict[str, Any],
    *,
    entity_aliases: Any,
    relation_aliases: Any,
    properties: Any,
    expected_registry_id: str,
) -> OntologySpec:
    registry_id = _required_text(schema, "registry_id")
    if registry_id != expected_registry_id:
        raise ValueError(f"Ontology registry_id 不匹配: 期望 {expected_registry_id}，实际 {registry_id}")
    version = _required_text(schema, "version")
    name = _required_text(schema, "name")
    status = _required_text(schema, "status")
    if status not in _ALLOWED_STATUSES:
        raise ValueError(f"Ontology status 必须是: {', '.join(sorted(_ALLOWED_STATUSES))}")

    raw_entities = _required_mapping(schema, "entities")
    raw_relations = _required_mapping(schema, "relations")
    _ensure_case_unique(raw_entities, "实体")
    _ensure_case_unique(raw_relations, "关系")

    entities = {
        entity_name: EntityDefinition(
            name=entity_name,
            description=_optional_text(definition, "description", f"实体 {entity_name}"),
            examples=_text_tuple(definition.get("examples", []), f"实体 {entity_name}.examples"),
        )
        for entity_name, definition in raw_entities.items()
        if isinstance(definition, dict)
    }
    if len(entities) != len(raw_entities):
        raise ValueError("Ontology entities 的每个定义都必须是对象")

    relations: dict[str, RelationDefinition] = {}
    for relation_name, definition in raw_relations.items():
        if not isinstance(definition, dict):
            raise ValueError(f"关系 {relation_name} 定义必须是对象")
        source = _endpoint_types(definition.get("source"), relation_name, "source")
        target = _endpoint_types(definition.get("target"), relation_name, "target")
        _validate_endpoint_references(source, entities, relation_name, "source")
        _validate_endpoint_references(target, entities, relation_name, "target")
        relations[relation_name] = RelationDefinition(
            name=relation_name,
            description=_optional_text(definition, "description", f"关系 {relation_name}"),
            source=source,
            target=target,
        )

    normalized_entity_aliases = _normalize_entity_aliases(entity_aliases, entities)
    normalized_relation_aliases = _normalize_relation_aliases(relation_aliases, relations)
    normalized_properties = _normalize_properties(properties)
    rules = {
        key: value
        for key, value in schema.items()
        if key not in {"registry_id", "version", "name", "status", "entities", "relations"}
    }
    return OntologySpec(
        registry_id=registry_id,
        version=version,
        name=name,
        status=status,
        entities=entities,
        relations=relations,
        entity_aliases=normalized_entity_aliases,
        relation_aliases=normalized_relation_aliases,
        properties=normalized_properties,
        rules=rules,
    )


def _normalize_entity_aliases(raw: Any, entities: dict[str, EntityDefinition]) -> dict[str, dict[str, tuple[str, ...]]]:
    if not isinstance(raw, dict):
        raise ValueError("entity.yaml 的 entities 必须是对象")
    unknown_types = sorted(set(raw) - set(entities))
    if unknown_types:
        raise ValueError(f"实体词典引用未声明类型: {', '.join(unknown_types)}")
    result: dict[str, dict[str, tuple[str, ...]]] = {}
    for entity_type, values in raw.items():
        if not isinstance(values, dict):
            raise ValueError(f"实体词典 {entity_type} 必须是对象")
        _ensure_case_unique(values, f"实体词典 {entity_type} 标准名")
        result[entity_type] = {
            canonical: _text_tuple(aliases, f"实体词典 {entity_type}.{canonical}")
            for canonical, aliases in values.items()
        }
    _validate_entity_alias_conflicts(result)
    return result


def _normalize_relation_aliases(raw: Any, relations: dict[str, RelationDefinition]) -> dict[str, tuple[str, ...]]:
    if not isinstance(raw, dict):
        raise ValueError("relation.yaml 的 relations 必须是对象")
    unknown_relations = sorted(set(raw) - set(relations))
    if unknown_relations:
        raise ValueError(f"关系词典引用未声明关系: {', '.join(unknown_relations)}")
    result = {}
    for relation_name, definition in raw.items():
        if not isinstance(definition, dict):
            raise ValueError(f"关系词典 {relation_name} 必须是对象")
        result[relation_name] = _text_tuple(definition.get("aliases", []), f"关系词典 {relation_name}.aliases")
    _validate_relation_alias_conflicts(result)
    return result


def _normalize_properties(raw: Any) -> dict[str, dict[str, PropertyDefinition]]:
    if not isinstance(raw, dict):
        raise ValueError("property.yaml 的 properties 必须是对象")
    seen_names: dict[str, str] = {}
    result: dict[str, dict[str, PropertyDefinition]] = {}
    for category, values in raw.items():
        if not isinstance(category, str) or not category.strip():
            raise ValueError("属性分类名称必须是非空字符串")
        if not isinstance(values, dict):
            raise ValueError(f"属性分类 {category} 必须是对象")
        category_items = {}
        _ensure_case_unique(values, f"属性分类 {category}")
        for property_name, definition in values.items():
            if not isinstance(definition, dict):
                raise ValueError(f"属性 {category}.{property_name} 必须是对象")
            folded = property_name.casefold()
            if folded in seen_names:
                raise ValueError(f"属性 key 重复: {seen_names[folded]} 与 {property_name}")
            seen_names[folded] = property_name
            value_type = _required_text(definition, "type", context=f"属性 {category}.{property_name}").lower()
            if value_type not in _ALLOWED_PROPERTY_TYPES:
                raise ValueError(
                    f"属性 {category}.{property_name}.type 不支持: {value_type}，"
                    f"可用类型: {', '.join(sorted(_ALLOWED_PROPERTY_TYPES))}"
                )
            unit = definition.get("unit")
            if unit is not None and not isinstance(unit, str):
                raise ValueError(f"属性 {category}.{property_name}.unit 必须是字符串")
            category_items[property_name] = PropertyDefinition(
                category=category,
                name=property_name,
                value_type=value_type,
                unit=unit,
            )
        result[category] = category_items
    return result


def _validate_entity_alias_conflicts(values: dict[str, dict[str, tuple[str, ...]]]) -> None:
    for entity_type, aliases_by_name in values.items():
        seen: dict[str, str] = {}
        for canonical, aliases in aliases_by_name.items():
            for alias in (canonical, *aliases):
                normalized = _normalize_alias(alias)
                existing = seen.get(normalized)
                if existing and existing != canonical:
                    raise ValueError(f"实体词典 {entity_type} 的别名 {alias} 同时映射到 {existing} 和 {canonical}")
                seen[normalized] = canonical


def _validate_relation_alias_conflicts(values: dict[str, tuple[str, ...]]) -> None:
    seen: dict[str, str] = {}
    for canonical, aliases in values.items():
        for alias in (canonical, *aliases):
            normalized = _normalize_alias(alias)
            existing = seen.get(normalized)
            if existing and existing != canonical:
                raise ValueError(f"关系别名 {alias} 同时映射到 {existing} 和 {canonical}")
            seen[normalized] = canonical


def _validate_property_value(value: Any, definition: PropertyDefinition) -> None:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"属性 {definition.name} 的值不能为空")
    value_type = definition.value_type
    comparable = text
    if definition.unit and comparable.casefold().endswith(definition.unit.casefold()):
        comparable = comparable[: -len(definition.unit)].strip()
    try:
        if value_type in {"int", "integer"}:
            int(comparable)
        elif value_type in {"float", "number"}:
            float(comparable)
        elif value_type in {"bool", "boolean"} and comparable.casefold() not in {"true", "false", "是", "否"}:
            raise ValueError
    except ValueError as exc:
        raise ValueError(f"属性 {definition.name} 的值不符合 {value_type}: {text}") from exc


def _reject_core_overrides(core: dict[str, Any], extension: dict[str, Any], label: str) -> None:
    if not isinstance(extension, dict):
        raise ValueError(f"领域扩展 {label} 必须是对象")
    core_names = {name.casefold(): name for name in core}
    for name in extension:
        existing = core_names.get(name.casefold())
        if existing:
            raise ValueError(f"领域扩展不能覆盖 Core {label}: {existing}")
    _ensure_case_unique(extension, f"领域扩展{label}")


def _ensure_case_unique(values: dict[str, Any], label: str) -> None:
    seen: dict[str, str] = {}
    for name in values:
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"{label}名称必须是非空字符串")
        folded = name.casefold()
        if folded in seen:
            raise ValueError(f"{label}名称大小写冲突: {seen[folded]} 与 {name}")
        seen[folded] = name


def _required_mapping(data: dict[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"Ontology {key} 必须是对象")
    return value


def _required_text(data: dict[str, Any], key: str, *, context: str = "Ontology") -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{context}.{key} 必须是非空字符串")
    return value.strip()


def _optional_text(data: dict[str, Any], key: str, context: str) -> str:
    value = data.get(key, "")
    if not isinstance(value, str):
        raise ValueError(f"{context}.{key} 必须是字符串")
    return value.strip()


def _text_tuple(value: Any, context: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item.strip() for item in value):
        raise ValueError(f"{context} 必须是非空字符串数组")
    return tuple(item.strip() for item in value)


def _endpoint_types(value: Any, relation_name: str, endpoint: str) -> tuple[str, ...]:
    if isinstance(value, str) and value.strip():
        return (value.strip(),)
    return _text_tuple(value, f"关系 {relation_name}.{endpoint}")


def _validate_endpoint_references(
    values: tuple[str, ...], entities: dict[str, EntityDefinition], relation_name: str, endpoint: str
) -> None:
    unknown = [value for value in values if value != "Any" and value not in entities]
    if unknown:
        raise ValueError(f"关系 {relation_name}.{endpoint} 引用未声明实体: {', '.join(unknown)}")


def _validate_identity(value: str, label: str) -> None:
    if not _ID_RE.fullmatch(value):
        raise ValueError(f"Ontology {label} 只能包含字母、数字、点、下划线和中划线，长度 1-64")


def _normalize_alias(value: Any) -> str:
    return " ".join(str(value or "").strip().casefold().split())


def _matches_endpoint(label: str | None, allowed: tuple[str, ...]) -> bool:
    return "Any" in allowed or label in allowed
