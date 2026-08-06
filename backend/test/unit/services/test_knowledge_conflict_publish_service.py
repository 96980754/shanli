from __future__ import annotations
import asyncio
from types import SimpleNamespace
import pytest
from yuxi.services.knowledge_conflict_publish_service import KnowledgeConflictPublishService
class _Repository:
    def __init__(
        self,
        *,
        vector_status: str = "pending",
        max_attempts: int = 5,
        conflict_version: int | None = None,
    ):
        self.task = SimpleNamespace(
            id=7,
            task_id="publish-1",
            conflict_id="conflict-1",
            assertion_id="assertion-1",
            kb_id="kb-1",
            resolution_id="resolution-1",
            entity_id="entity-1",
            expected_version=2,
            status="pending",
            neo4j_status="pending",
            vector_status=vector_status,
            attempt_count=0,
            max_attempts=max_attempts,
            error_code=None,
            last_error=None,
            updated_at=None,
            completed_at=None,
        )
        self.conflict = SimpleNamespace(
            conflict_id="conflict-1",
            version=conflict_version if conflict_version is not None else 2,
            status="resolved",
            resolution="use_new",
            existing_assertion_ids=["assertion-old"],
        )
        self.assertion = SimpleNamespace(
            assertion_id="assertion-1",
            status="accepted",
            linked_entity_id="entity-1",
            entity_name="MiniServer M200",
            predicate="max_concurrent_users",
            raw_value=200,
            normalized_value=200,
            product_version="V2",
            file_id="file-1",
            chunk_id="chunk-1",
        )
        self.entity = SimpleNamespace(entity_id="entity-1", name="MiniServer M200", label="Product")
        self._claim_lock = asyncio.Lock()
        self.target_updates: list[str] = []
        self.failures: list[str] = []
        self.stale_removed = False
        self.stale_marks: list[str] = []
    async def claim(self, task_id, *, lease_seconds):
        assert task_id == self.task.task_id
        assert lease_seconds > 0
        async with self._claim_lock:
            if self.task.status in {"processing", "succeeded", "dead_letter"}:
                return None
            self.task.status = "processing"
            self.task.attempt_count += 1
            return self.task
    async def load_authoritative_payload(self, task_id):
        assert task_id == self.task.task_id
        return {
            "task": self.task,
            "conflict": self.conflict,
            "assertion": self.assertion,
            "entity": self.entity,
        }
    async def mark_target_succeeded(self, _task_id, target):
        setattr(self.task, f"{target}_status", "succeeded")
        self.target_updates.append(target)
    async def mark_succeeded(self, _task_id):
        if self.assertion.status == "superseded":
            self.task.status = "succeeded"
            return False
        self.task.status = "succeeded"
        self.assertion.status = "published"
        return True
    async def mark_stale(self, task_id):
        self.stale_marks.append(task_id)
        self.task.status = "succeeded"
        self.task.error_code = "stale_version"
        self.task.last_error = None
    async def mark_failed(self, _task_id, *, error_code, message):
        self.failures.append(error_code)
        self.task.status = "dead_letter" if self.task.attempt_count >= self.task.max_attempts else "failed"
        self.task.last_error = message
        return self.task.status
    async def list_recoverable_task_ids(self, *, limit):
        assert limit == 10
        return [self.task.task_id]
class _PublishService(KnowledgeConflictPublishService):
    def __init__(self, repository, *, fail_vector_once=False):
        super().__init__(repository=repository, kb_repository=SimpleNamespace())
        self.neo4j_calls = 0
        self.vector_calls = 0
        self.fail_vector_once = fail_vector_once
    async def _publish_neo4j(self, *_args):
        self.neo4j_calls += 1
    async def _publish_vector(self, *_args):
        self.vector_calls += 1
        if self.fail_vector_once:
            self.fail_vector_once = False
            raise RuntimeError("milvus://private-host unavailable")
    async def _remove_stale_projection(self, *_args):
        self.repository.stale_removed = True
@pytest.mark.asyncio
async def test_publish_is_claimed_once_and_repeated_delivery_is_idempotent():
    repository = _Repository()
    service = _PublishService(repository)
    results = await asyncio.gather(service.process("publish-1"), service.process("publish-1"))
    assert sorted(results) == ["not_claimed", "succeeded"]
    assert service.neo4j_calls == 1
    assert service.vector_calls == 1
    assert repository.assertion.status == "published"
@pytest.mark.asyncio
async def test_partial_success_retries_only_missing_target_and_sanitizes_error():
    repository = _Repository()
    service = _PublishService(repository, fail_vector_once=True)
    assert await service.process("publish-1") == "failed"
    assert repository.task.neo4j_status == "succeeded"
    assert "private-host" not in repository.task.last_error
    repository.task.status = "failed"
    assert await service.process("publish-1") == "succeeded"
    assert service.neo4j_calls == 1
    assert service.vector_calls == 2
@pytest.mark.asyncio
async def test_stale_assertion_projection_is_removed_instead_of_reactivated():
    repository = _Repository(vector_status="pending")
    repository.assertion.status = "superseded"
    service = _PublishService(repository)
    assert await service.process("publish-1") == "failed"
    assert service.neo4j_calls == 0
    assert service.vector_calls == 0
@pytest.mark.asyncio
async def test_recovery_enqueues_durable_task_without_fixed_transport_identity():
    repository = _Repository()
    service = _PublishService(repository)
    calls = []
    class Queue:
        async def enqueue_job(self, *args, **kwargs):
            calls.append((args, kwargs))
            return SimpleNamespace()
    assert await service.recover(queue=Queue(), limit=10) == 1
    assert calls == [(("process_knowledge_conflict_publish", "publish-1"), {})]
@pytest.mark.asyncio
async def test_max_attempt_failure_enters_dead_letter():
    repository = _Repository(max_attempts=1)
    service = _PublishService(repository, fail_vector_once=True)
    assert await service.process("publish-1") == "dead_letter"
    assert repository.task.status == "dead_letter"
@pytest.mark.asyncio
async def test_version_mismatch_marks_stale_before_any_publish():
    repository = _Repository(conflict_version=3)
    service = _PublishService(repository)
    assert await service.process("publish-1") == "stale"
    assert service.neo4j_calls == 0
    assert service.vector_calls == 0
    assert repository.stale_marks == ["publish-1"]
    assert repository.task.status == "succeeded"
    assert repository.task.error_code == "stale_version"
    assert repository.stale_removed is True
