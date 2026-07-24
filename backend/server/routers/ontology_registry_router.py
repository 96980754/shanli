from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from server.utils.auth_middleware import get_admin_user, get_superadmin_user
from yuxi.knowledge.graphs.ontology import (
    MAX_ONTOLOGY_ZIP_BYTES,
    OntologyConflictError,
    create_ontology_registry,
    get_ontology_registry_detail,
    import_ontology_bundle,
    list_ontology_registries,
    overwrite_ontology_registry,
)
from yuxi.repositories.knowledge_base_repository import KnowledgeBaseRepository
from yuxi.repositories.knowledge_chunk_repository import KnowledgeChunkRepository
from yuxi.storage.postgres.models_business import User
from yuxi.utils import logger
from yuxi.utils.upload_utils import read_upload_with_limit

ontology_registries = APIRouter(prefix="/system/ontology-registries", tags=["ontology-registries"])

RequiredText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]
IdentityText = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$",
    ),
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CanonicalAliasInput(StrictModel):
    canonical: RequiredText
    aliases: list[RequiredText] = Field(default_factory=list, max_length=200)


class OntologyEntityInput(StrictModel):
    name: RequiredText
    description: str = Field(default="", max_length=1000)
    examples: list[RequiredText] = Field(default_factory=list, max_length=100)
    canonical_aliases: list[CanonicalAliasInput] = Field(default_factory=list, max_length=500)


class OntologyRelationInput(StrictModel):
    name: RequiredText
    description: str = Field(default="", max_length=1000)
    source: list[RequiredText] = Field(min_length=1, max_length=100)
    target: list[RequiredText] = Field(min_length=1, max_length=100)
    aliases: list[RequiredText] = Field(default_factory=list, max_length=200)


class OntologyPropertyInput(StrictModel):
    category: RequiredText
    name: RequiredText
    type: Literal["string", "int", "integer", "float", "number", "bool", "boolean"]
    unit: str | None = Field(default=None, max_length=64)


class CreateOntologyRegistryRequest(StrictModel):
    registry_id: IdentityText
    version: IdentityText
    name: RequiredText
    entities: list[OntologyEntityInput] = Field(min_length=1, max_length=500)
    relations: list[OntologyRelationInput] = Field(default_factory=list, max_length=1000)
    properties: list[OntologyPropertyInput] = Field(default_factory=list, max_length=1000)
    rules: dict = Field(default_factory=dict)

    def definitions(self):
        entities = {}
        entity_aliases = {}
        for item in self.entities:
            entities[item.name] = {
                "description": item.description.strip(),
                "examples": item.examples,
            }
            entity_aliases[item.name] = {alias.canonical: alias.aliases for alias in item.canonical_aliases}

        relations = {}
        relation_aliases = {}
        for item in self.relations:
            relations[item.name] = {
                "description": item.description.strip(),
                "source": item.source,
                "target": item.target,
            }
            relation_aliases[item.name] = item.aliases

        properties = {}
        for item in self.properties:
            definition = {"type": item.type}
            if item.unit and item.unit.strip():
                definition["unit"] = item.unit.strip()
            properties.setdefault(item.category, {})[item.name] = definition

        return {
            "name": self.name,
            "entities": entities,
            "relations": relations,
            "entity_aliases": entity_aliases,
            "relation_aliases": relation_aliases,
            "properties": properties,
            "rules": self.rules,
        }

    def create(self):
        return create_ontology_registry(
            registry_id=self.registry_id,
            version=self.version,
            **self.definitions(),
        )


class OverwriteOntologyRegistryRequest(CreateOntologyRegistryRequest):
    expected_digest: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


@ontology_registries.get("")
async def list_registries(_current_user: User = Depends(get_admin_user)):
    try:
        return {"items": [entry.public_dict() for entry in list_ontology_registries()]}
    except ValueError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@ontology_registries.get("/{registry_id}/versions/{version}")
async def get_registry_detail(
    registry_id: str,
    version: str,
    digest: str,
    _current_user: User = Depends(get_admin_user),
):
    try:
        return get_ontology_registry_detail(registry_id, version, digest)
    except ValueError as exc:
        if str(exc).startswith("未找到 Ontology Registry"):
            raise HTTPException(status_code=404, detail="未找到 Core Ontology") from exc
        logger.exception(f"读取 Ontology Registry 详情失败: {exc}")
        raise HTTPException(status_code=500, detail="读取 Core Ontology 详情失败") from exc


@ontology_registries.put("/{registry_id}/versions/{version}")
async def overwrite_registry(
    registry_id: str,
    version: str,
    request: OverwriteOntologyRegistryRequest,
    _current_user: User = Depends(get_superadmin_user),
):
    if request.registry_id != registry_id or request.version != version:
        raise HTTPException(status_code=400, detail="请求路径与 Ontology 身份不一致")
    try:
        config_refs = await KnowledgeBaseRepository().list_ontology_config_references(
            registry_id, version, request.expected_digest
        )
        extraction_refs = await KnowledgeChunkRepository().count_ontology_extraction_references(
            registry_id, version, request.expected_digest
        )
        references = {
            item["kb_id"]: {**item, "sources": ["config"], "extraction_result_count": 0} for item in config_refs
        }
        for kb_id, count in extraction_refs.items():
            item = references.setdefault(
                kb_id,
                {"kb_id": kb_id, "name": kb_id, "sources": [], "extraction_result_count": 0},
            )
            item["sources"].append("extraction_result")
            item["extraction_result_count"] = count
        if references:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "ontology_in_use",
                    "message": "该 Ontology 正被知识库配置或抽取结果引用，不能覆盖。",
                    "references": list(references.values()),
                },
            )
        entry, previous_digest, changed = overwrite_ontology_registry(
            registry_id=registry_id,
            version=version,
            expected_digest=request.expected_digest,
            **request.definitions(),
        )
        return {"item": entry.public_dict(), "previous_digest": previous_digest, "changed": changed}
    except HTTPException:
        raise
    except OntologyConflictError as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "ontology_conflict", "message": str(exc)},
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception(f"覆盖 Ontology Registry 失败: {exc}")
        raise HTTPException(status_code=500, detail="覆盖 Ontology Registry 失败") from exc


@ontology_registries.post("", status_code=status.HTTP_201_CREATED)
async def create_registry(
    request: CreateOntologyRegistryRequest,
    _current_user: User = Depends(get_superadmin_user),
):
    try:
        entry, already_exists = request.create()
        return {"item": entry.public_dict(), "already_exists": already_exists}
    except OntologyConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception(f"创建 Ontology Registry 失败: {exc}")
        raise HTTPException(status_code=500, detail="创建 Ontology Registry 失败") from exc


@ontology_registries.post("/upload", status_code=status.HTTP_201_CREATED)
async def upload_registry(
    file: UploadFile = File(...),
    _current_user: User = Depends(get_superadmin_user),
):
    filename = file.filename or ""
    if not filename.lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="仅支持上传 .zip Ontology Bundle")
    try:
        data = await read_upload_with_limit(
            file,
            max_size_bytes=MAX_ONTOLOGY_ZIP_BYTES,
            too_large_message="Ontology ZIP 不能超过 5 MiB",
        )
        entry, already_exists = import_ontology_bundle(data)
        return {"item": entry.public_dict(), "already_exists": already_exists}
    except OntologyConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception(f"上传 Ontology Bundle 失败: {exc}")
        raise HTTPException(status_code=500, detail="上传 Ontology Bundle 失败") from exc
