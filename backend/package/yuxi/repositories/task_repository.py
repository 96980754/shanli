from __future__ import annotations

from typing import Any

from sqlalchemy import delete, select, text

from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import TaskRecord


class TaskRepository:
    async def get_by_id(self, task_id: str) -> TaskRecord | None:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(select(TaskRecord).where(TaskRecord.id == task_id))
            return result.scalar_one_or_none()

    async def list(self, status: str | None = None, limit: int = 100) -> list[TaskRecord]:
        async with pg_manager.get_async_session_context() as session:
            stmt = select(TaskRecord)
            if status:
                stmt = stmt.where(TaskRecord.status == status)
            stmt = stmt.order_by(TaskRecord.created_at.desc()).limit(max(limit, 0))
            result = await session.execute(stmt)
            return list(result.scalars().all())

    async def find_latest_by_payload(
        self,
        *,
        task_type: str,
        payload_match: dict[str, Any],
        statuses: set[str] | None = None,
    ) -> TaskRecord | None:
        async with pg_manager.get_async_session_context() as session:
            stmt = select(TaskRecord).where(TaskRecord.type == task_type)
            if statuses is not None:
                stmt = stmt.where(TaskRecord.status.in_(statuses))
            for key, value in payload_match.items():
                stmt = stmt.where(TaskRecord.payload[key].as_string() == str(value))
            result = await session.execute(
                stmt.order_by(TaskRecord.created_at.desc(), TaskRecord.updated_at.desc(), TaskRecord.id.desc()).limit(1)
            )
            return result.scalar_one_or_none()

    async def create_if_no_active(
        self,
        *,
        task_id: str,
        data: dict[str, Any],
        payload_key: str,
        payload_value: str,
        active_statuses: set[str],
    ) -> TaskRecord | None:
        async with pg_manager.get_async_session_context() as session:
            lock_key = f"{data['type']}:{payload_key}:{payload_value}"
            lock_stmt = (
                text("SELECT pg_advisory_xact_lock(hashtext(:lock_key))")
                if session.bind.dialect.name == "postgresql"
                else None
            )
            if lock_stmt is not None:
                await session.execute(lock_stmt, {"lock_key": lock_key})
            stmt = (
                select(TaskRecord.id)
                .where(
                    TaskRecord.type == data["type"],
                    TaskRecord.status.in_(active_statuses),
                    TaskRecord.payload[payload_key].as_string() == str(payload_value),
                )
                .limit(1)
            )
            if (await session.execute(stmt)).scalar_one_or_none() is not None:
                return None
            record = TaskRecord(id=task_id, **data)
            session.add(record)
            await session.flush()
            return record

    async def request_cancel(self, task_id: str) -> bool:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(select(TaskRecord).where(TaskRecord.id == task_id).with_for_update())
            record = result.scalar_one_or_none()
            if record is None or record.status in {"success", "failed", "cancelled"}:
                return False
            record.cancel_requested = 1
            return True

    async def list_all(self) -> list[TaskRecord]:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(select(TaskRecord).order_by(TaskRecord.created_at.desc()))
            return list(result.scalars().all())

    async def upsert(self, task_id: str, data: dict[str, Any]) -> TaskRecord:
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(select(TaskRecord).where(TaskRecord.id == task_id))
            record = result.scalar_one_or_none()
            if record is None:
                record = TaskRecord(id=task_id, **data)
                session.add(record)
                return record
            for key, value in data.items():
                setattr(record, key, value)
            return record

    async def delete(self, task_id: str) -> bool:
        """Delete a task by id. Returns True if deleted, False if not found."""
        async with pg_manager.get_async_session_context() as session:
            result = await session.execute(delete(TaskRecord).where(TaskRecord.id == task_id))
            return result.rowcount > 0

    async def delete_all(self) -> None:
        async with pg_manager.get_async_session_context() as session:
            await session.execute(delete(TaskRecord))
