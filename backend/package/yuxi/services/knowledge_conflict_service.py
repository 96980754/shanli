from __future__ import annotations

from datetime import datetime
from difflib import SequenceMatcher
from typing import Any
from uuid import uuid4

from yuxi.knowledge.conflicts import ConflictDetector, normalize_entity_name
from yuxi.knowledge.graphs.graph_utils import compute_entity_id
from yuxi.repositories.knowledge_chunk_repository import KnowledgeChunkRepository
from yuxi.repositories.knowledge_conflict_repository import KnowledgeConflictRepository
from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository
from yuxi.services.knowledge_conflict_publish_service import (
    KnowledgeConflictPublishService,
    serialize_publish_task,
)
from yuxi.utils.logging_config import logger


class KnowledgeConflictError(ValueError):
    pass


class KnowledgeConflictNotFound(KnowledgeConflictError):
    pass


class KnowledgeConflictVersionError(KnowledgeConflictError):
    pass


class KnowledgeConflictService:
    def __init__(
        self,
        *,
        repository: KnowledgeConflictRepository | None = None,
        file_repository: KnowledgeFileRepository | None = None,
        chunk_repository: KnowledgeChunkRepository | None = None,
        detector: ConflictDetector | None = None,
        publish_service: KnowledgeConflictPublishService | None = None,
    ):
        self.repository = repository or KnowledgeConflictRepository()
        self.file_repository = file_repository or KnowledgeFileRepository()
        self.chunk_repository = chunk_repository or KnowledgeChunkRepository()
        self.detector = detector or ConflictDetector()
        self.publish_service = publish_service or KnowledgeConflictPublishService()

    async def evaluate(self, *, kb_id: str, payload: dict[str, Any], operator_id: str) -> dict[str, Any]:
        file_record, chunk = await self._validate_evidence(kb_id=kb_id, payload=payload)
        entity_type = str(payload["entity_type"]).strip()
        entity_name = str(payload["entity_name"]).strip()
        predicate = str(payload["predicate"]).strip()
        assertion_id = f"assert_{uuid4().hex}"
        assertion = await self.repository.create_assertion(
            {
                "assertion_id": assertion_id,
                "kb_id": kb_id,
                "entity_type": entity_type,
                "entity_name": entity_name,
                "linked_entity_id": payload.get("linked_entity_id"),
                "predicate": predicate,
                "raw_value": payload["raw_value"],
                "value_type": str(payload["value_type"]).strip(),
                "unit": payload.get("unit"),
                "valid_from": _parse_datetime(payload.get("valid_from")),
                "valid_to": _parse_datetime(payload.get("valid_to")),
                "product_version": payload.get("product_version"),
                "file_id": file_record.file_id,
                "chunk_id": chunk.chunk_id,
                "evidence": str(payload["evidence"]).strip(),
                "cleaning_version": int(file_record.cleaning_version or 0),
                "content_hash": str(file_record.content_hash or ""),
                "extraction_method": str(payload.get("extraction_method") or "manual"),
                "confidence": payload.get("confidence"),
                "status": "candidate",
                "source": "manual" if payload.get("extraction_method") == "manual" else "generated",
            }
        )

        link_status, linked_entity, link_rows = await self._link_entity(
            kb_id=kb_id,
            assertion_id=assertion_id,
            entity_type=entity_type,
            entity_name=entity_name,
            requested_entity_id=payload.get("linked_entity_id"),
            hints=payload.get("link_hints") or {},
        )
        await self.repository.create_link_candidates(link_rows)
        linked_entity_id = linked_entity.entity_id if linked_entity else None
        existing = (
            await self.repository.list_published_assertions(
                kb_id=kb_id,
                entity_id=linked_entity_id,
                predicate=predicate,
            )
            if linked_entity_id
            else []
        )
        result = self.detector.detect(assertion, existing, link_status=link_status)
        assertion = await self.repository.update_assertion_link(
            assertion_id=assertion_id,
            linked_entity_id=linked_entity_id,
            normalized_value=result.normalized_incoming_value,
        )
        conflict = await self.repository.create_conflict(
            {
                "conflict_id": f"conflict_{uuid4().hex}",
                "kb_id": kb_id,
                "entity_id": linked_entity_id,
                "predicate": predicate,
                "existing_assertion_ids": list(result.existing_assertion_ids),
                "incoming_assertion_id": assertion_id,
                "conflict_type": result.conflict_type,
                "classification": result.classification.value,
                "existing_value": [item.raw_value for item in existing] or None,
                "incoming_value": assertion.raw_value,
                "normalized_existing_value": result.normalized_existing_value,
                "normalized_incoming_value": result.normalized_incoming_value,
                "detection_rules": {
                    "reasons": list(result.reasons),
                    "entity_link_status": link_status,
                    "detector": "deterministic-product-specification-v1",
                },
                "severity": result.severity,
                "requires_review": result.requires_review,
                "status": "pending",
                "publish_status": "not_requested",
            }
        )
        return await self._serialize_conflict(conflict, assertion=assertion)

    async def list_conflicts(self, *, kb_id: str, status: str | None = None) -> dict[str, Any]:
        conflicts = await self.repository.list_conflicts(kb_id=kb_id, status=status)
        items = [await self._serialize_conflict(conflict) for conflict in conflicts]
        return {"items": items, "total": len(items)}

    async def get_conflict(self, *, kb_id: str, conflict_id: str) -> dict[str, Any]:
        conflict = await self.repository.get_conflict(kb_id=kb_id, conflict_id=conflict_id)
        if conflict is None:
            raise KnowledgeConflictNotFound("knowledge conflict not found")
        return await self._serialize_conflict(conflict)

    async def list_entity_link_candidates(self, *, kb_id: str) -> dict[str, Any]:
        records = await self.repository.list_link_candidates(kb_id=kb_id)
        return {
            "items": [
                {
                    "link_id": item.link_id,
                    "assertion_id": item.assertion_id,
                    "candidate_name": item.candidate_name,
                    "normalized_name": item.normalized_name,
                    "target_entity_id": item.target_entity_id,
                    "target_entity_name": item.target_entity_name,
                    "matching_rules": item.matching_rules or [],
                    "similarity": item.similarity,
                    "aliases": item.aliases or [],
                    "status": item.status,
                    "resolved_by": item.resolved_by,
                    "resolved_at": item.resolved_at,
                }
                for item in records
            ],
            "total": len(records),
        }

    async def resolve(
        self,
        *,
        kb_id: str,
        conflict_id: str,
        resolution: str,
        expected_version: int,
        reason: str | None,
        operator_id: str,
        target_entity_id: str | None = None,
    ) -> dict[str, Any]:
        conflict = await self.repository.get_conflict(kb_id=kb_id, conflict_id=conflict_id)
        if conflict is None:
            raise KnowledgeConflictNotFound("knowledge conflict not found")
        assertion = await self.repository.get_assertion(
            kb_id=kb_id,
            assertion_id=conflict.incoming_assertion_id,
        )
        if assertion is None:
            raise KnowledgeConflictNotFound("incoming assertion not found")

        if resolution == "link_existing_entity":
            if not target_entity_id:
                raise KnowledgeConflictError("target_entity_id is required when linking an existing entity")
            target = await self.repository.get_entity(kb_id=kb_id, entity_id=target_entity_id)
            if target is None:
                raise KnowledgeConflictNotFound("linked entity not found")
            existing = await self.repository.list_published_assertions(
                kb_id=kb_id,
                entity_id=target_entity_id,
                predicate=assertion.predicate,
            )
            detection = self.detector.detect(assertion, existing, link_status="linked")
            try:
                (
                    reclassified,
                    linked_assertion,
                ) = await self.repository.reclassify_linked_conflict(
                    kb_id=kb_id,
                    conflict_id=conflict_id,
                    expected_version=expected_version,
                    target_entity_id=target_entity_id,
                    normalized_value=detection.normalized_incoming_value,
                    detection={
                        "existing_assertion_ids": list(detection.existing_assertion_ids),
                        "conflict_type": detection.conflict_type,
                        "classification": detection.classification.value,
                        "existing_value": [item.raw_value for item in existing] or None,
                        "normalized_existing_value": detection.normalized_existing_value,
                        "normalized_incoming_value": detection.normalized_incoming_value,
                        "detection_rules": {
                            "reasons": list(detection.reasons),
                            "entity_link_status": "linked",
                            "detector": "deterministic-product-specification-v1",
                        },
                        "severity": detection.severity,
                        "requires_review": detection.requires_review,
                    },
                    operator_id=operator_id,
                )
            except LookupError as exc:
                raise KnowledgeConflictNotFound(str(exc)) from exc
            except ValueError as exc:
                raise KnowledgeConflictVersionError("knowledge conflict was updated by another reviewer") from exc
            return await self._serialize_conflict(reclassified, assertion=linked_assertion)

        create_entity = None
        if resolution == "create_new_entity":
            normalized_name = normalize_entity_name(assertion.entity_name)
            create_entity = {
                "entity_id": compute_entity_id(kb_id, normalized_name, assertion.entity_type),
                "kb_id": kb_id,
                "normalized_name": normalized_name,
                "label": assertion.entity_type,
                "name": assertion.entity_name,
                "attributes": _entity_attributes_from_assertion(assertion),
            }
        try:
            resolved, resolved_assertion, _entity = await self.repository.resolve(
                kb_id=kb_id,
                conflict_id=conflict_id,
                expected_version=expected_version,
                resolution=resolution,
                reason=reason,
                operator_id=operator_id,
                create_entity=create_entity,
            )
        except LookupError as exc:
            raise KnowledgeConflictNotFound(str(exc)) from exc
        except ValueError as exc:
            if str(exc) == "version conflict":
                raise KnowledgeConflictVersionError("knowledge conflict was updated by another reviewer") from exc
            raise KnowledgeConflictError(str(exc)) from exc
        payload = await self._serialize_conflict(resolved, assertion=resolved_assertion)
        await self._enqueue_publish_task(payload)
        return payload

    async def batch_resolve(
        self,
        *,
        kb_id: str,
        items: list[dict[str, Any]],
        operator_id: str,
    ) -> dict[str, Any]:
        results = []
        for item in items:
            results.append(
                await self.resolve(
                    kb_id=kb_id,
                    conflict_id=item["conflict_id"],
                    resolution=item["resolution"],
                    expected_version=item["version"],
                    reason=item.get("reason"),
                    operator_id=operator_id,
                    target_entity_id=item.get("target_entity_id"),
                )
            )
        return {"items": results, "total": len(results)}

    async def retry_publish(self, *, kb_id: str, conflict_id: str) -> dict[str, Any]:
        conflict = await self.repository.get_conflict(kb_id=kb_id, conflict_id=conflict_id)
        if conflict is None:
            raise KnowledgeConflictNotFound("knowledge conflict not found")
        task = await self.publish_service.retry(kb_id=kb_id, conflict_id=conflict_id)
        if task is None:
            raise KnowledgeConflictError("knowledge conflict has no publish task")
        return task

    async def _enqueue_publish_task(self, conflict_payload: dict[str, Any]) -> None:
        task = conflict_payload.get("publish_task")
        if not task or task.get("status") not in {"pending", "failed"}:
            return
        try:
            await self.publish_service.enqueue(task["task_id"])
        except Exception as exc:  # noqa: BLE001 - PostgreSQL outbox remains recoverable
            logger.warning(
                "Failed to enqueue durable knowledge publish task {}: {}", task["task_id"], type(exc).__name__
            )

    async def _validate_evidence(self, *, kb_id: str, payload: dict[str, Any]):
        file_record = await self.file_repository.get_by_file_id(str(payload["file_id"]))
        if file_record is None or file_record.kb_id != kb_id or not file_record.is_active or file_record.is_folder:
            raise KnowledgeConflictNotFound("source document not found")
        if file_record.status != "indexed":
            raise KnowledgeConflictError("source document must be active and indexed")

        chunk = await self.chunk_repository.get_by_chunk_id(str(payload["chunk_id"]))
        if chunk is None or chunk.kb_id != kb_id or chunk.file_id != file_record.file_id:
            raise KnowledgeConflictError("source chunk does not belong to the source document")
        evidence = str(payload["evidence"]).strip()
        if not evidence or evidence not in chunk.content:
            raise KnowledgeConflictError("evidence must be an exact excerpt from the source chunk")
        return file_record, chunk

    async def _link_entity(
        self,
        *,
        kb_id: str,
        assertion_id: str,
        entity_type: str,
        entity_name: str,
        requested_entity_id: str | None,
        hints: dict[str, Any],
    ):
        if requested_entity_id:
            entity = await self.repository.get_entity(kb_id=kb_id, entity_id=requested_entity_id)
            if entity is None:
                raise KnowledgeConflictNotFound("linked entity not found")
            return (
                "linked",
                entity,
                [
                    _link_row(
                        assertion_id,
                        kb_id,
                        entity_name,
                        entity,
                        ["exact_entity_id"],
                        1.0,
                        "linked",
                    )
                ],
            )

        normalized_name = normalize_entity_name(entity_name)
        entities = await self.repository.list_entities(kb_id=kb_id, entity_type=entity_type)
        deterministic: list[tuple[Any, list[str]]] = []
        fuzzy: list[tuple[Any, float]] = []
        business_id = normalize_entity_name(str(hints.get("business_id") or ""))
        product_model = normalize_entity_name(str(hints.get("product_model") or ""))
        brand = normalize_entity_name(str(hints.get("brand") or ""))

        for entity in entities:
            attributes = entity.attributes or {}
            rules: list[str] = []
            if business_id and normalize_entity_name(str(attributes.get("business_id") or "")) == business_id:
                rules.append("exact_business_id")
            entity_model = normalize_entity_name(str(attributes.get("product_model") or ""))
            if product_model and entity_model == product_model:
                rules.append("product_model")
                if brand and normalize_entity_name(str(attributes.get("brand") or "")) == brand:
                    rules.append("brand_and_model")
            aliases = [normalize_entity_name(str(value)) for value in attributes.get("aliases") or []]
            if normalized_name == entity.normalized_name:
                rules.append("normalized_name")
            if normalized_name in aliases:
                rules.append("confirmed_alias")
            if rules:
                deterministic.append((entity, rules))
                continue
            similarity = SequenceMatcher(None, normalized_name, entity.normalized_name).ratio()
            if similarity >= 0.72:
                fuzzy.append((entity, similarity))

        if len(deterministic) == 1:
            entity, rules = deterministic[0]
            return (
                "linked",
                entity,
                [_link_row(assertion_id, kb_id, entity_name, entity, rules, 1.0, "linked")],
            )
        if deterministic:
            rows = [
                _link_row(assertion_id, kb_id, entity_name, entity, rules, 1.0, "ambiguous")
                for entity, rules in deterministic
            ]
            return "ambiguous", None, rows
        if fuzzy:
            rows = [
                _link_row(
                    assertion_id,
                    kb_id,
                    entity_name,
                    entity,
                    ["string_similarity_recall_only"],
                    similarity,
                    "ambiguous",
                )
                for entity, similarity in fuzzy
            ]
            return "ambiguous", None, rows
        return (
            "new_entity",
            None,
            [
                {
                    "link_id": f"link_{uuid4().hex}",
                    "assertion_id": assertion_id,
                    "kb_id": kb_id,
                    "candidate_name": entity_name,
                    "normalized_name": normalized_name,
                    "target_entity_id": None,
                    "target_entity_name": None,
                    "matching_rules": ["no_deterministic_match"],
                    "similarity": None,
                    "aliases": [],
                    "status": "new_entity",
                }
            ],
        )

    async def _serialize_conflict(self, conflict: Any, *, assertion: Any | None = None) -> dict[str, Any]:
        assertion = assertion or await self.repository.get_assertion(
            kb_id=conflict.kb_id,
            assertion_id=conflict.incoming_assertion_id,
        )
        if assertion is None:
            raise KnowledgeConflictNotFound("incoming assertion not found")
        existing = []
        for assertion_id in conflict.existing_assertion_ids or []:
            record = await self.repository.get_assertion(kb_id=conflict.kb_id, assertion_id=assertion_id)
            if record:
                existing.append(_serialize_assertion(record))
        publish_task = await self.publish_service.repository.get_task_for_conflict(
            kb_id=conflict.kb_id,
            conflict_id=conflict.conflict_id,
        )
        return {
            "conflict_id": conflict.conflict_id,
            "kb_id": conflict.kb_id,
            "entity_id": conflict.entity_id,
            "entity_name": assertion.entity_name,
            "entity_type": assertion.entity_type,
            "predicate": conflict.predicate,
            "classification": conflict.classification,
            "conflict_type": conflict.conflict_type,
            "existing_assertions": existing,
            "incoming_assertion": _serialize_assertion(assertion),
            "existing_value": conflict.existing_value,
            "incoming_value": conflict.incoming_value,
            "normalized_existing_value": conflict.normalized_existing_value,
            "normalized_incoming_value": conflict.normalized_incoming_value,
            "detection_rules": conflict.detection_rules or {},
            "severity": conflict.severity,
            "requires_review": conflict.requires_review,
            "status": conflict.status,
            "resolution": conflict.resolution,
            "resolution_reason": conflict.resolution_reason,
            "resolved_by": conflict.resolved_by,
            "resolved_at": conflict.resolved_at,
            "publish_status": conflict.publish_status,
            "publish_error": conflict.publish_error,
            "publish_task": serialize_publish_task(publish_task) if publish_task else None,
            "created_at": conflict.created_at,
            "updated_at": conflict.updated_at,
            "version": conflict.version,
        }


def _parse_datetime(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError as exc:
        raise KnowledgeConflictError("valid_from and valid_to must use ISO date/time format") from exc


def _link_row(
    assertion_id: str,
    kb_id: str,
    candidate_name: str,
    entity: Any,
    rules: list[str],
    similarity: float,
    status: str,
) -> dict[str, Any]:
    attributes = entity.attributes or {}
    return {
        "link_id": f"link_{uuid4().hex}",
        "assertion_id": assertion_id,
        "kb_id": kb_id,
        "candidate_name": candidate_name,
        "normalized_name": normalize_entity_name(candidate_name),
        "target_entity_id": entity.entity_id,
        "target_entity_name": entity.name,
        "matching_rules": rules,
        "similarity": similarity,
        "aliases": attributes.get("aliases") or [],
        "status": status,
    }


def _entity_attributes_from_assertion(assertion: Any) -> dict[str, Any]:
    attributes: dict[str, Any] = {"review_source": "knowledge_conflict_mvp"}
    if assertion.predicate == "product_model":
        attributes["product_model"] = assertion.normalized_value or assertion.raw_value
    return attributes


def _serialize_assertion(assertion: Any) -> dict[str, Any]:
    return {
        "assertion_id": assertion.assertion_id,
        "kb_id": assertion.kb_id,
        "entity_type": assertion.entity_type,
        "entity_name": assertion.entity_name,
        "linked_entity_id": assertion.linked_entity_id,
        "predicate": assertion.predicate,
        "raw_value": assertion.raw_value,
        "normalized_value": assertion.normalized_value,
        "value_type": assertion.value_type,
        "unit": assertion.unit,
        "valid_from": assertion.valid_from,
        "valid_to": assertion.valid_to,
        "product_version": assertion.product_version,
        "file_id": assertion.file_id,
        "chunk_id": assertion.chunk_id,
        "evidence": assertion.evidence,
        "cleaning_version": assertion.cleaning_version,
        "content_hash": assertion.content_hash,
        "extraction_method": assertion.extraction_method,
        "confidence": assertion.confidence,
        "status": assertion.status,
        "source": assertion.source,
        "created_at": assertion.created_at,
        "updated_at": assertion.updated_at,
    }
