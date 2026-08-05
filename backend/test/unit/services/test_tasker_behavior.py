"""Tasker 行为单元测试：payload 暴露、进度节流、终态保留与重启恢复。

使用内存 fake repo，不依赖真实数据库与 Docker。
"""

import asyncio

from yuxi.services import task_service
from yuxi.services.task_service import Task, Tasker


class FakeRecord:
    def __init__(self, data: dict):
        self._data = data

    def to_dict(self) -> dict:
        return self._data


class FakeRepo:
    """记录 upsert/delete 调用的内存仓库替身。"""

    def __init__(self, preset: list[FakeRecord] | None = None):
        self.preset = preset or []
        self.records = {record.to_dict()["id"]: record for record in self.preset}
        self.upsert_calls = 0
        self.progress_writes: list[float] = []
        self.deleted: list[str] = []

    async def upsert(self, task_id: str, data: dict) -> None:
        self.upsert_calls += 1
        self.progress_writes.append(data.get("progress"))
        existing = self.records.get(task_id)
        record_data = existing.to_dict() if existing else {"id": task_id}
        record_data.update(data)
        self.records[task_id] = FakeRecord(record_data)

    async def get_by_id(self, task_id: str):
        return self.records.get(task_id)

    async def request_cancel(self, task_id: str) -> bool:
        record = self.records.get(task_id)
        if record is None:
            return False
        record.to_dict()["cancel_requested"] = True
        return True

    async def delete(self, task_id: str) -> bool:
        self.deleted.append(task_id)
        self.records.pop(task_id, None)
        return True

    async def list_all(self) -> list[FakeRecord]:
        return list(self.records.values())


async def _make_tasker(repo: FakeRepo, worker_count: int = 1) -> Tasker:
    tasker = Tasker(worker_count=worker_count)
    tasker._repo = repo
    await tasker.start()
    return tasker


async def _wait_status(tasker: Tasker, task_id: str, statuses: set[str], timeout: float = 2.0) -> dict:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        task = await tasker.get_task(task_id)
        if task and task["status"] in statuses:
            return task
        if loop.time() > deadline:
            raise AssertionError(f"任务 {task_id} 未在超时内进入 {statuses}")
        await asyncio.sleep(0.01)


async def test_task_context_exposes_payload():
    repo = FakeRepo()
    tasker = await _make_tasker(repo)
    seen: dict = {}

    async def coro(ctx):
        seen["payload"] = ctx.payload
        return "ok"

    task = await tasker.enqueue(name="x", task_type="demo", payload={"a": 1}, coroutine=coro)
    await _wait_status(tasker, task.id, {"success"})

    assert seen["payload"] == {"a": 1}
    await tasker.shutdown()


async def test_progress_updates_are_throttled():
    repo = FakeRepo()
    tasker = await _make_tasker(repo)

    async def coro(ctx):
        for percent in range(101):
            await ctx.set_progress(percent)
        return "done"

    task = await tasker.enqueue(name="x", task_type="demo", coroutine=coro)
    final = await _wait_status(tasker, task.id, {"success"})

    # 101 次进度推进经节流后落库次数应远小于 101（含 enqueue/running/success 也仅个位数额外写入）
    assert repo.upsert_calls < 60
    # 内存中进度仍为完整的 100
    assert final["progress"] == 100
    await tasker.shutdown()


async def test_explicit_none_result_is_persisted():
    repo = FakeRepo()
    tasker = await _make_tasker(repo)

    async def coro(ctx):
        await ctx.set_result("partial")
        return None

    task = await tasker.enqueue(name="x", task_type="demo", coroutine=coro)
    final = await _wait_status(tasker, task.id, {"success"})

    # 协程最终返回 None 应覆盖中途结果（sentinel 区分「未传」与「显式 None」）
    assert final["result"] is None
    await tasker.shutdown()


async def test_completed_tasks_are_pruned_to_limit(monkeypatch):
    monkeypatch.setattr(task_service, "MAX_TERMINAL_TASKS", 3)
    repo = FakeRepo()
    tasker = await _make_tasker(repo)

    async def coro(ctx):
        return "ok"

    for index in range(6):
        task = await tasker.enqueue(name=f"t{index}", task_type="demo", coroutine=coro)
        await _wait_status(tasker, task.id, {"success"})

    listing = await tasker.list_tasks(limit=100)
    assert listing["summary"]["total"] <= 3
    assert len(repo.deleted) >= 3
    await tasker.shutdown()


async def test_find_task_by_payload_returns_latest_match():
    tasker = Tasker()
    tasker._tasks = {
        "new": Task(
            id="new",
            name="new",
            type="graph",
            payload={"kb_id": "kb1"},
            created_at="2026-01-02T00:00:00",
        ),
        "old": Task(
            id="old",
            name="old",
            type="graph",
            payload={"kb_id": "kb1"},
            created_at="2026-01-01T00:00:00",
        ),
    }

    task = await tasker.find_task_by_payload(task_type="graph", payload_match={"kb_id": "kb1"})

    assert task is not None
    assert task.id == "new"


async def test_shutdown_does_not_hold_lock_while_worker_finishes():
    repo = FakeRepo()
    tasker = await _make_tasker(repo)
    started = asyncio.Event()

    async def coro(ctx):
        started.set()
        await asyncio.Event().wait()

    task = await tasker.enqueue(name="x", task_type="demo", coroutine=coro)
    await started.wait()

    await asyncio.wait_for(tasker.shutdown(), timeout=1.0)

    final = await tasker.get_task(task.id)
    assert final["status"] == "cancelled"


async def test_load_state_does_not_fail_external_graph_task():
    repo = FakeRepo(
        preset=[
            FakeRecord(
                {
                    "id": "graph",
                    "name": "graph",
                    "type": "knowledge_graph_index",
                    "status": "running",
                    "created_at": "2026-01-01T00:00:00",
                }
            )
        ]
    )
    tasker = await _make_tasker(repo)

    assert (await repo.get_by_id("graph")).to_dict()["status"] == "running"
    assert await tasker.get_task("graph") == repo.records["graph"].to_dict()
    await tasker.shutdown()


async def test_load_state_marks_interrupted_and_prunes(monkeypatch):
    monkeypatch.setattr(task_service, "MAX_TERMINAL_TASKS", 2)
    repo = FakeRepo(
        preset=[
            FakeRecord(
                {"id": "a", "name": "a", "type": "demo", "status": "running", "created_at": "2026-01-01T00:00:05"}
            ),
            FakeRecord(
                {"id": "b", "name": "b", "type": "demo", "status": "success", "created_at": "2026-01-01T00:00:04"}
            ),
            FakeRecord(
                {"id": "c", "name": "c", "type": "demo", "status": "success", "created_at": "2026-01-01T00:00:03"}
            ),
            FakeRecord(
                {"id": "d", "name": "d", "type": "demo", "status": "success", "created_at": "2026-01-01T00:00:02"}
            ),
        ]
    )
    tasker = await _make_tasker(repo)

    # 中断的 running 任务被标记为 failed
    interrupted = await tasker.get_task("a")
    assert interrupted["status"] == "failed"
    assert interrupted["error"] == "服务重启时任务中断"
    assert interrupted["completed_at"] is not None
    # 仅保留最近 MAX_TERMINAL_TASKS 条终态任务，最旧的被清理
    listing = await tasker.list_tasks(limit=100)
    assert listing["summary"]["total"] == 2
    assert "c" in repo.deleted and "d" in repo.deleted
    await tasker.shutdown()


def test_tasker_recreates_runtime_queue_when_restarted_on_another_event_loop():
    repo = FakeRepo()
    tasker = Tasker(worker_count=1)
    tasker._repo = repo

    async def run_once() -> asyncio.AbstractEventLoop:
        await tasker.start()
        await asyncio.sleep(0)
        loop = asyncio.get_running_loop()
        await tasker.shutdown()
        return loop

    first_loop = asyncio.run(run_once())

    async def restart() -> None:
        await tasker.start()
        try:
            await asyncio.sleep(0)
            assert tasker._queue._loop is asyncio.get_running_loop()
            assert tasker._queue._loop is not first_loop
        finally:
            await tasker.shutdown()

    asyncio.run(restart())
