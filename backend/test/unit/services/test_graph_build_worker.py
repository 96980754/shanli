from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from yuxi.services.graph_build_worker import process_knowledge_graph_index


@pytest.mark.asyncio
async def test_graph_build_worker_marks_success(monkeypatch):
    task = SimpleNamespace(
        id="task1",
        status="pending",
        payload={"kb_id": "kb1", "batch_size": 10},
        started_at=None,
    )
    repository = SimpleNamespace(
        get_by_id=AsyncMock(side_effect=[task, SimpleNamespace(cancel_requested=0)]),
        upsert=AsyncMock(),
    )
    service = SimpleNamespace(
        build_pending_chunks=AsyncMock(
            return_value={"kb_id": "kb1", "success": 2, "failed": 0, "remaining": 0, "failed_chunk_ids": []}
        )
    )
    monkeypatch.setattr("yuxi.services.graph_build_worker.TaskRepository", lambda: repository)
    monkeypatch.setattr("yuxi.services.graph_build_worker.MilvusGraphService", lambda: service)

    await process_knowledge_graph_index({}, "task1")

    service.build_pending_chunks.assert_awaited_once()
    assert repository.upsert.await_args_list[-1].args[1]["status"] == "success"
    assert repository.upsert.await_args_list[-1].args[1]["result"]["success"] == 2


@pytest.mark.asyncio
async def test_graph_build_worker_preserves_partial_result_and_marks_failed(monkeypatch):
    task = SimpleNamespace(
        id="task1",
        status="pending",
        payload={"kb_id": "kb1", "batch_size": 10},
        started_at=None,
    )
    result = {"kb_id": "kb1", "success": 1, "failed": 1, "remaining": 1, "failed_chunk_ids": ["c2"]}
    repository = SimpleNamespace(
        get_by_id=AsyncMock(side_effect=[task, SimpleNamespace(cancel_requested=0)]),
        upsert=AsyncMock(),
    )
    service = SimpleNamespace(build_pending_chunks=AsyncMock(return_value=result))
    monkeypatch.setattr("yuxi.services.graph_build_worker.TaskRepository", lambda: repository)
    monkeypatch.setattr("yuxi.services.graph_build_worker.MilvusGraphService", lambda: service)

    await process_knowledge_graph_index({}, "task1")

    calls = [call.args[1] for call in repository.upsert.await_args_list]
    assert any(call.get("result") == result for call in calls)
    assert calls[-1]["status"] == "failed"
    assert "剩余 1 个" in calls[-1]["error"]
