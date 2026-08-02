from __future__ import annotations

import asyncio
from typing import Any

from yuxi.knowledge.graphs.milvus_graph_service import MilvusGraphService
from yuxi.repositories.task_repository import TaskRepository
from yuxi.utils.datetime_utils import utc_now_naive
from yuxi.utils.logging_config import logger


class GraphBuildTaskContext:
    def __init__(self, task_id: str, repository: TaskRepository):
        self.task_id = task_id
        self._repository = repository

    async def set_progress(self, progress: float, message: str | None = None) -> None:
        data: dict[str, Any] = {
            "progress": max(0.0, min(progress, 100.0)),
            "updated_at": utc_now_naive(),
        }
        if message is not None:
            data["message"] = message
        await self._repository.upsert(self.task_id, data)

    async def set_message(self, message: str) -> None:
        await self._repository.upsert(self.task_id, {"message": message, "updated_at": utc_now_naive()})

    async def set_result(self, result: Any) -> None:
        await self._repository.upsert(self.task_id, {"result": result, "updated_at": utc_now_naive()})

    async def raise_if_cancelled(self) -> None:
        task = await self._repository.get_by_id(self.task_id)
        if task is None or task.cancel_requested:
            raise asyncio.CancelledError("任务被取消")


async def process_knowledge_graph_index(ctx: dict[str, Any], task_id: str) -> None:
    del ctx
    repository = TaskRepository()
    task = await repository.get_by_id(task_id)
    if task is None or task.status in {"success", "failed", "cancelled"}:
        return

    now = utc_now_naive()
    await repository.upsert(
        task_id,
        {
            "status": "running",
            "progress": 0.0,
            "message": "任务开始执行",
            "error": None,
            "started_at": task.started_at or now,
            "updated_at": now,
        },
    )
    context = GraphBuildTaskContext(task_id, repository)
    payload = task.payload or {}

    try:
        result = await MilvusGraphService().build_pending_chunks(
            str(payload["kb_id"]),
            batch_size=int(payload.get("batch_size") or 20),
            context=context,
        )
        await context.set_result(result)
        if result["failed"] > 0 or result["remaining"] > 0:
            raise RuntimeError(
                f"图谱构建未全部完成：成功 {result['success']} 个，"
                f"失败 {result['failed']} 个，剩余 {result['remaining']} 个"
            )
        completed_at = utc_now_naive()
        await repository.upsert(
            task_id,
            {
                "status": "success",
                "progress": 100.0,
                "message": f"图谱构建完成，成功 {result['success']} 个",
                "result": result,
                "completed_at": completed_at,
                "updated_at": completed_at,
            },
        )
    except asyncio.CancelledError:
        completed_at = utc_now_naive()
        await repository.upsert(
            task_id,
            {
                "status": "cancelled",
                "progress": 100.0,
                "message": "任务被取消",
                "completed_at": completed_at,
                "updated_at": completed_at,
            },
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Graph build task {} failed: {}", task_id, exc)
        completed_at = utc_now_naive()
        await repository.upsert(
            task_id,
            {
                "status": "failed",
                "progress": 100.0,
                "message": "任务执行失败",
                "error": str(exc),
                "completed_at": completed_at,
                "updated_at": completed_at,
            },
        )


__all__ = ["GraphBuildTaskContext", "process_knowledge_graph_index"]
