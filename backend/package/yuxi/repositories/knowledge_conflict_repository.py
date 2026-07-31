from __future__ import annotations

from typing import Any

from sqlalchemy import select

from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_knowledge import (
    EntityLinkCandidate,
    KnowledgeAssertion,
    KnowledgeConflict,
    KnowledgeGraphEntity,
)
from yuxi.utils.datetime_utils import utc_now_naive


class KnowledgeConflictRepository:
    async def get_entity(
        self, *, kb_id: str, entity_id: str
    ) -> KnowledgeGraphEntity | None:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(KnowledgeGraphEntity).where(
                    KnowledgeGraphEntity.kb_id == kb_id,
                    KnowledgeGraphEntity.entity_id == entity_id,
                )
            )
            return result.scalar_one_or_none()

    async def list_entities(
        self, *, kb_id: str, entity_type: str
    ) -> list[KnowledgeGraphEntity]:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(KnowledgeGraphEntity)
                .where(
                    KnowledgeGraphEntity.kb_id == kb_id,
                    KnowledgeGraphEntity.label == entity_type,
                )
                .order_by(KnowledgeGraphEntity.id.asc())
            )
            return list(result.scalars().all())

    async def create_assertion(self, data: dict[str, Any]) -> KnowledgeAssertion:
        async with pg_manager.get_async_session_context() as session:
            assertion = KnowledgeAssertion(**data)
            session.add(assertion)
            await session.flush()
            return assertion

    async def update_assertion_link(
        self,
        *,
        assertion_id: str,
        linked_entity_id: str | None,
        normalized_value: Any,
        status: str = "pending_review",
    ) -> KnowledgeAssertion:
        async with pg_manager.get_async_session_context() as session:
            assertion = await session.scalar(
                select(KnowledgeAssertion)
                .where(KnowledgeAssertion.assertion_id == assertion_id)
                .with_for_update()
            )
            if assertion is None:
                raise LookupError("assertion not found")
            assertion.linked_entity_id = linked_entity_id
            assertion.normalized_value = normalized_value
            assertion.status = status
            assertion.updated_at = utc_now_naive()
            await session.flush()
            return assertion

    async def create_link_candidates(
        self, rows: list[dict[str, Any]]
    ) -> list[EntityLinkCandidate]:
        if not rows:
            return []
        async with pg_manager.get_async_session_context() as session:
            records = [EntityLinkCandidate(**row) for row in rows]
            session.add_all(records)
            await session.flush()
            return records

    async def create_conflict(self, data: dict[str, Any]) -> KnowledgeConflict:
        async with pg_manager.get_async_session_context() as session:
            conflict = KnowledgeConflict(**data)
            session.add(conflict)
            await session.flush()
            return conflict

    async def get_assertion(
        self, *, kb_id: str, assertion_id: str
    ) -> KnowledgeAssertion | None:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(KnowledgeAssertion).where(
                    KnowledgeAssertion.kb_id == kb_id,
                    KnowledgeAssertion.assertion_id == assertion_id,
                )
            )
            return result.scalar_one_or_none()

    async def list_published_assertions(
        self,
        *,
        kb_id: str,
        entity_id: str,
        predicate: str | None = None,
    ) -> list[KnowledgeAssertion]:
        conditions = [
            KnowledgeAssertion.kb_id == kb_id,
            KnowledgeAssertion.linked_entity_id == entity_id,
            KnowledgeAssertion.status.in_(["accepted", "published"]),
        ]
        if predicate:
            conditions.append(KnowledgeAssertion.predicate == predicate)
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(KnowledgeAssertion)
                .where(*conditions)
                .order_by(KnowledgeAssertion.created_at.asc())
            )
            return list(result.scalars().all())

    async def list_conflicts(
        self,
        *,
        kb_id: str,
        status: str | None = None,
    ) -> list[KnowledgeConflict]:
        conditions = [KnowledgeConflict.kb_id == kb_id]
        if status:
            conditions.append(KnowledgeConflict.status == status)
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(KnowledgeConflict)
                .where(*conditions)
                .order_by(
                    KnowledgeConflict.created_at.desc(), KnowledgeConflict.id.desc()
                )
            )
            return list(result.scalars().all())

    async def get_conflict(
        self, *, kb_id: str, conflict_id: str
    ) -> KnowledgeConflict | None:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(KnowledgeConflict).where(
                    KnowledgeConflict.kb_id == kb_id,
                    KnowledgeConflict.conflict_id == conflict_id,
                )
            )
            return result.scalar_one_or_none()

    async def list_link_candidates(self, *, kb_id: str) -> list[EntityLinkCandidate]:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(EntityLinkCandidate)
                .where(EntityLinkCandidate.kb_id == kb_id)
                .order_by(
                    EntityLinkCandidate.created_at.desc(), EntityLinkCandidate.id.desc()
                )
            )
            return list(result.scalars().all())

    async def reclassify_linked_conflict(
        self,
        *,
        kb_id: str,
        conflict_id: str,
        expected_version: int,
        target_entity_id: str,
        normalized_value: Any,
        detection: dict[str, Any],
        operator_id: str,
    ) -> tuple[KnowledgeConflict, KnowledgeAssertion]:
        async with pg_manager.get_async_session_context() as session:
            conflict = await session.scalar(
                select(KnowledgeConflict)
                .where(
                    KnowledgeConflict.kb_id == kb_id,
                    KnowledgeConflict.conflict_id == conflict_id,
                )
                .with_for_update()
            )
            if conflict is None:
                raise LookupError("conflict not found")
            if conflict.version != expected_version or conflict.status != "pending":
                raise ValueError("version conflict")
            entity = await session.scalar(
                select(KnowledgeGraphEntity).where(
                    KnowledgeGraphEntity.kb_id == kb_id,
                    KnowledgeGraphEntity.entity_id == target_entity_id,
                )
            )
            if entity is None:
                raise LookupError("entity not found")
            assertion = await session.scalar(
                select(KnowledgeAssertion)
                .where(
                    KnowledgeAssertion.assertion_id == conflict.incoming_assertion_id
                )
                .with_for_update()
            )
            if assertion is None:
                raise LookupError("assertion not found")

            assertion.linked_entity_id = target_entity_id
            assertion.normalized_value = normalized_value
            assertion.status = "pending_review"
            conflict.entity_id = target_entity_id
            for key, value in detection.items():
                setattr(conflict, key, value)
            conflict.version += 1
            now = utc_now_naive()
            conflict.updated_at = now
            assertion.updated_at = now

            link_result = await session.execute(
                select(EntityLinkCandidate).where(
                    EntityLinkCandidate.assertion_id == assertion.assertion_id
                )
            )
            for candidate in link_result.scalars().all():
                candidate.status = (
                    "linked"
                    if candidate.target_entity_id == target_entity_id
                    else "rejected"
                )
                candidate.resolved_by = operator_id
                candidate.resolved_at = now
            await session.flush()
            return conflict, assertion

    async def resolve(
        self,
        *,
        kb_id: str,
        conflict_id: str,
        expected_version: int,
        resolution: str,
        reason: str | None,
        operator_id: str,
        create_entity: dict[str, Any] | None = None,
    ) -> tuple[KnowledgeConflict, KnowledgeAssertion, KnowledgeGraphEntity | None]:
        async with pg_manager.get_async_session_context() as session:
            conflict = await session.scalar(
                select(KnowledgeConflict)
                .where(
                    KnowledgeConflict.kb_id == kb_id,
                    KnowledgeConflict.conflict_id == conflict_id,
                )
                .with_for_update()
            )
            if conflict is None:
                raise LookupError("conflict not found")
            assertion = await session.scalar(
                select(KnowledgeAssertion)
                .where(
                    KnowledgeAssertion.assertion_id == conflict.incoming_assertion_id
                )
                .with_for_update()
            )
            if assertion is None:
                raise LookupError("assertion not found")

            if conflict.status == "resolved" and conflict.resolution == resolution:
                entity = (
                    await session.scalar(
                        select(KnowledgeGraphEntity).where(
                            KnowledgeGraphEntity.entity_id == assertion.linked_entity_id
                        )
                    )
                    if assertion.linked_entity_id
                    else None
                )
                return conflict, assertion, entity
            if conflict.version != expected_version:
                raise ValueError("version conflict")

            entity = None
            if create_entity:
                entity = KnowledgeGraphEntity(**create_entity)
                session.add(entity)
                await session.flush()
                assertion.linked_entity_id = entity.entity_id
                conflict.entity_id = entity.entity_id
            elif assertion.linked_entity_id:
                entity = await session.scalar(
                    select(KnowledgeGraphEntity).where(
                        KnowledgeGraphEntity.kb_id == kb_id,
                        KnowledgeGraphEntity.entity_id == assertion.linked_entity_id,
                    )
                )

            now = utc_now_naive()
            publish_resolutions = {
                "use_new",
                "merge",
                "keep_both_by_version",
                "mark_as_completion",
                "create_new_entity",
            }
            reject_resolutions = {"keep_old", "reject_incoming"}
            if resolution in publish_resolutions:
                assertion.status = "published"
                assertion.published_at = now
                conflict.publish_status = "pending"
            elif resolution in reject_resolutions:
                assertion.status = "rejected"
                conflict.publish_status = "not_requested"
            elif resolution == "defer":
                assertion.status = "deferred"
                conflict.status = "deferred"
                conflict.publish_status = "not_requested"
            else:
                raise ValueError("unsupported resolution")

            if resolution == "use_new":
                existing_ids = list(conflict.existing_assertion_ids or [])
                if existing_ids:
                    existing_result = await session.execute(
                        select(KnowledgeAssertion).where(
                            KnowledgeAssertion.kb_id == kb_id,
                            KnowledgeAssertion.assertion_id.in_(existing_ids),
                        )
                    )
                    for existing in existing_result.scalars().all():
                        existing.status = "superseded"
                        existing.updated_at = now

            if resolution != "defer":
                conflict.status = "resolved"
            conflict.resolution = resolution
            conflict.resolution_reason = reason
            conflict.resolved_by = operator_id
            conflict.resolved_at = now
            conflict.version += 1
            conflict.updated_at = now
            assertion.updated_at = now

            link_result = await session.execute(
                select(EntityLinkCandidate).where(
                    EntityLinkCandidate.assertion_id == assertion.assertion_id
                )
            )
            for candidate in link_result.scalars().all():
                candidate.resolved_by = operator_id
                candidate.resolved_at = now
                if (
                    assertion.linked_entity_id
                    and candidate.target_entity_id == assertion.linked_entity_id
                ):
                    candidate.status = "linked"
            await session.flush()
            return conflict, assertion, entity
