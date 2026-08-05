from __future__ import annotations

import hashlib
from datetime import timedelta
from typing import Any

from sqlalchemy import or_, select

from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_knowledge import (
    KnowledgeAssertion,
    KnowledgeConflict,
    KnowledgeConflictPublishTask,
    KnowledgeGraphEntity,
)
from yuxi.utils.datetime_utils import utc_now

PUBLISH_TASK_STATUS_PENDING = "pending"
PUBLISH_TASK_STATUS_PROCESSING = "processing"
PUBLISH_TASK_STATUS_SUCCEEDED = "succeeded"
PUBLISH_TASK_STATUS_FAILED = "failed"
PUBLISH_TASK_STATUS_DEAD_LETTER = "dead_letter"


def build_publish_identity(conflict_id: str, expected_version: int) -> tuple[str, str]:
    digest = hashlib.sha256(f"{conflict_id}:{expected_version}".encode()).hexdigest()
    return f"publish_{digest[:32]}", f"resolution_{digest[:28]}"


class KnowledgePublishRepository:
    async def get_task(self, *, kb_id: str, task_id: str) -> KnowledgeConflictPublishTask | None:
        async with pg_manager.get_async_session_context() as session:
            return await session.scalar(
                select(KnowledgeConflictPublishTask).where(
                    KnowledgeConflictPublishTask.kb_id == kb_id,
                    KnowledgeConflictPublishTask.task_id == task_id,
                )
            )

    async def get_task_for_conflict(self, *, kb_id: str, conflict_id: str) -> KnowledgeConflictPublishTask | None:
        async with pg_manager.get_async_session_context() as session:
            return await session.scalar(
                select(KnowledgeConflictPublishTask)
                .where(
                    KnowledgeConflictPublishTask.kb_id == kb_id,
                    KnowledgeConflictPublishTask.conflict_id == conflict_id,
                )
                .order_by(KnowledgeConflictPublishTask.expected_version.desc())
                .limit(1)
            )

    async def list_recoverable_task_ids(self, *, limit: int = 100) -> list[str]:
        now = utc_now()
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(KnowledgeConflictPublishTask.task_id)
                .where(
                    or_(
                        KnowledgeConflictPublishTask.status == PUBLISH_TASK_STATUS_PENDING,
                        (
                            (KnowledgeConflictPublishTask.status == PUBLISH_TASK_STATUS_FAILED)
                            & (
                                (KnowledgeConflictPublishTask.next_attempt_at.is_(None))
                                | (KnowledgeConflictPublishTask.next_attempt_at <= now)
                            )
                        ),
                        (
                            (KnowledgeConflictPublishTask.status == PUBLISH_TASK_STATUS_PROCESSING)
                            & (KnowledgeConflictPublishTask.lease_expires_at <= now)
                        ),
                    )
                )
                .order_by(KnowledgeConflictPublishTask.created_at.asc())
                .limit(max(limit, 0))
            )
            return list(result.scalars().all())

    async def claim(self, task_id: str, *, lease_seconds: int) -> KnowledgeConflictPublishTask | None:
        now = utc_now()
        async with pg_manager.get_async_session_context() as session:
            task = await session.scalar(
                select(KnowledgeConflictPublishTask)
                .where(KnowledgeConflictPublishTask.task_id == task_id)
                .with_for_update(skip_locked=True)
            )
            if task is None or task.status in {
                PUBLISH_TASK_STATUS_SUCCEEDED,
                PUBLISH_TASK_STATUS_DEAD_LETTER,
            }:
                return None
            if task.status == PUBLISH_TASK_STATUS_PROCESSING and (
                task.lease_expires_at is None or task.lease_expires_at > now
            ):
                return None
            if task.status == PUBLISH_TASK_STATUS_FAILED and task.next_attempt_at and task.next_attempt_at > now:
                return None
            if task.attempt_count >= task.max_attempts:
                task.status = PUBLISH_TASK_STATUS_DEAD_LETTER
                task.lease_expires_at = None
                task.updated_at = now
                conflict = await session.scalar(
                    select(KnowledgeConflict).where(KnowledgeConflict.conflict_id == task.conflict_id)
                )
                if conflict is not None:
                    conflict.publish_status = PUBLISH_TASK_STATUS_DEAD_LETTER
                    conflict.publish_error = task.last_error
                return None

            task.status = PUBLISH_TASK_STATUS_PROCESSING
            task.attempt_count += 1
            task.next_attempt_at = None
            task.lease_expires_at = now + timedelta(seconds=lease_seconds)
            task.updated_at = now
            task.last_error = None
            task.error_code = None
            conflict = await session.scalar(
                select(KnowledgeConflict).where(KnowledgeConflict.conflict_id == task.conflict_id)
            )
            if conflict is not None:
                conflict.publish_status = PUBLISH_TASK_STATUS_PROCESSING
                conflict.publish_error = None
            await session.flush()
            return task

    async def load_authoritative_payload(self, task_id: str) -> dict[str, Any] | None:
        async with pg_manager.get_async_session_context() as session:
            task = await session.scalar(
                select(KnowledgeConflictPublishTask).where(KnowledgeConflictPublishTask.task_id == task_id)
            )
            if task is None:
                return None
            conflict = await session.scalar(
                select(KnowledgeConflict).where(KnowledgeConflict.conflict_id == task.conflict_id)
            )
            assertion = await session.scalar(
                select(KnowledgeAssertion).where(KnowledgeAssertion.assertion_id == task.assertion_id)
            )
            entity = (
                await session.scalar(
                    select(KnowledgeGraphEntity).where(
                        KnowledgeGraphEntity.kb_id == task.kb_id,
                        KnowledgeGraphEntity.entity_id == task.entity_id,
                    )
                )
                if task.entity_id
                else None
            )
            if conflict is None or assertion is None:
                return None
            return {
                "task": task,
                "conflict": conflict,
                "assertion": assertion,
                "entity": entity,
            }

    async def mark_target_succeeded(self, task_id: str, target: str) -> None:
        if target not in {"neo4j", "vector"}:
            raise ValueError("unsupported publish target")
        async with pg_manager.get_async_session_context() as session:
            task = await session.scalar(
                select(KnowledgeConflictPublishTask)
                .where(KnowledgeConflictPublishTask.task_id == task_id)
                .with_for_update()
            )
            if task is None:
                raise LookupError("publish task not found")
            setattr(task, f"{target}_status", PUBLISH_TASK_STATUS_SUCCEEDED)
            task.updated_at = utc_now()

    async def mark_stale(self, task_id: str) -> None:
        now = utc_now()
        async with pg_manager.get_async_session_context() as session:
            task = await session.scalar(
                select(KnowledgeConflictPublishTask)
                .where(KnowledgeConflictPublishTask.task_id == task_id)
                .with_for_update()
            )
            if task is None:
                return
            task.status = PUBLISH_TASK_STATUS_SUCCEEDED
            task.error_code = "stale_version"
            task.last_error = None
            task.lease_expires_at = None
            task.completed_at = now
            task.updated_at = now

    async def mark_succeeded(self, task_id: str) -> bool:
        now = utc_now()
        async with pg_manager.get_async_session_context() as session:
            task = await session.scalar(
                select(KnowledgeConflictPublishTask)
                .where(KnowledgeConflictPublishTask.task_id == task_id)
                .with_for_update()
            )
            if task is None:
                raise LookupError("publish task not found")
            if (
                task.neo4j_status != PUBLISH_TASK_STATUS_SUCCEEDED
                or task.vector_status != PUBLISH_TASK_STATUS_SUCCEEDED
            ):
                raise ValueError("publish targets are incomplete")
            conflict = await session.scalar(
                select(KnowledgeConflict).where(KnowledgeConflict.conflict_id == task.conflict_id).with_for_update()
            )
            assertion = await session.scalar(
                select(KnowledgeAssertion).where(KnowledgeAssertion.assertion_id == task.assertion_id).with_for_update()
            )
            if conflict is None or assertion is None:
                raise LookupError("publish source not found")
            if conflict.version != task.expected_version or assertion.status == "superseded":
                task.status = PUBLISH_TASK_STATUS_SUCCEEDED
                task.error_code = "stale_version"
                task.last_error = None
                task.lease_expires_at = None
                task.completed_at = now
                task.updated_at = now
                return False

            if conflict.resolution == "use_new" and conflict.existing_assertion_ids:
                old_result = await session.execute(
                    select(KnowledgeAssertion).where(
                        KnowledgeAssertion.kb_id == task.kb_id,
                        KnowledgeAssertion.assertion_id.in_(list(conflict.existing_assertion_ids)),
                    )
                )
                for old_assertion in old_result.scalars().all():
                    old_assertion.status = "superseded"
                    old_assertion.updated_at = now

            assertion.status = "published"
            assertion.published_at = now
            assertion.updated_at = now
            conflict.publish_status = PUBLISH_TASK_STATUS_SUCCEEDED
            conflict.publish_error = None
            conflict.updated_at = now
            task.status = PUBLISH_TASK_STATUS_SUCCEEDED
            task.error_code = None
            task.last_error = None
            task.lease_expires_at = None
            task.completed_at = now
            task.updated_at = now
            return True

    async def mark_failed(self, task_id: str, *, error_code: str, message: str) -> str:
        now = utc_now()
        async with pg_manager.get_async_session_context() as session:
            task = await session.scalar(
                select(KnowledgeConflictPublishTask)
                .where(KnowledgeConflictPublishTask.task_id == task_id)
                .with_for_update()
            )
            if task is None:
                raise LookupError("publish task not found")
            if task.status == PUBLISH_TASK_STATUS_SUCCEEDED:
                return task.status
            terminal = task.attempt_count >= task.max_attempts
            task.status = PUBLISH_TASK_STATUS_DEAD_LETTER if terminal else PUBLISH_TASK_STATUS_FAILED
            task.error_code = error_code
            task.last_error = message
            task.next_attempt_at = None if terminal else now + timedelta(seconds=min(2**task.attempt_count, 60))
            task.lease_expires_at = None
            task.updated_at = now
            task.completed_at = now if terminal else None
            conflict = await session.scalar(
                select(KnowledgeConflict).where(KnowledgeConflict.conflict_id == task.conflict_id)
            )
            if conflict is not None:
                conflict.publish_status = task.status
                conflict.publish_error = message
                conflict.updated_at = now
            return task.status

    async def retry(self, *, kb_id: str, conflict_id: str) -> KnowledgeConflictPublishTask | None:
        now = utc_now()
        async with pg_manager.get_async_session_context() as session:
            conflict = await session.scalar(
                select(KnowledgeConflict).where(
                    KnowledgeConflict.kb_id == kb_id,
                    KnowledgeConflict.conflict_id == conflict_id,
                )
            )
            if conflict is None:
                return None
            task = await session.scalar(
                select(KnowledgeConflictPublishTask)
                .where(
                    KnowledgeConflictPublishTask.kb_id == kb_id,
                    KnowledgeConflictPublishTask.conflict_id == conflict_id,
                )
                .order_by(KnowledgeConflictPublishTask.expected_version.desc())
                .with_for_update()
            )
            if task is None:
                return None
            if task.status in {
                PUBLISH_TASK_STATUS_PENDING,
                PUBLISH_TASK_STATUS_PROCESSING,
                PUBLISH_TASK_STATUS_SUCCEEDED,
            }:
                return task
            task.status = PUBLISH_TASK_STATUS_PENDING
            task.attempt_count = 0
            task.error_code = None
            task.last_error = None
            task.next_attempt_at = None
            task.lease_expires_at = None
            task.completed_at = None
            task.updated_at = now
            conflict.publish_status = PUBLISH_TASK_STATUS_PENDING
            conflict.publish_error = None
            conflict.updated_at = now
            return task

    async def list_published_assertions(self, *, kb_id: str, assertion_ids: list[str]) -> list[KnowledgeAssertion]:
        if not assertion_ids:
            return []
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(
                select(KnowledgeAssertion)
                .join(
                    KnowledgeConflict,
                    KnowledgeConflict.incoming_assertion_id == KnowledgeAssertion.assertion_id,
                )
                .where(
                    KnowledgeAssertion.kb_id == kb_id,
                    KnowledgeAssertion.assertion_id.in_(assertion_ids),
                    KnowledgeAssertion.status == "published",
                    KnowledgeConflict.status == "resolved",
                    KnowledgeConflict.publish_status == PUBLISH_TASK_STATUS_SUCCEEDED,
                )
            )
            return list(result.scalars().all())
