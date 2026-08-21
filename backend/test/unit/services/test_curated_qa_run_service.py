from types import SimpleNamespace

import pytest

from yuxi.services import curated_qa_run_service as svc
from yuxi.services.input_message_service import build_chat_input_message


class _FakeDb:
    def __init__(self):
        self.commits = 0

    async def commit(self):
        self.commits += 1


class _FakeQaRepo:
    qa_pair = SimpleNamespace(id=7, answer="人工确认答案", hit_count=0)

    def __init__(self, _db):
        pass

    async def get_exact(self, **_kwargs):
        return self.qa_pair

    async def mark_hit(self, item):
        item.hit_count += 1


class _FakeRunRepo:
    def __init__(self, _db):
        pass

    async def mark_running(self, run_id):
        assert run_id == "run-1"

    async def set_output_message(self, run_id, message_id):
        assert (run_id, message_id) == ("run-1", 22)

    async def set_terminal_status(self, run_id, *, status, **_kwargs):
        assert run_id == "run-1"
        assert status == "completed"
        _RUN.status = status


class _FakeConversationRepo:
    def __init__(self, _db):
        pass

    async def add_message_by_thread_id(self, **kwargs):
        assert kwargs["content"] == "人工确认答案"
        assert kwargs["extra_metadata"]["answer_source"] == "curated_qa"
        return SimpleNamespace(id=22)


_RUN = SimpleNamespace(
    id="run-1",
    conversation_thread_id="thread-1",
    status="pending",
    request_id="req-1",
)


@pytest.mark.asyncio
async def test_curated_qa_hit_creates_completed_run_without_worker(monkeypatch):
    db = _FakeDb()
    events = []

    async def fake_prepare(**_kwargs):
        return SimpleNamespace(
            conversation=SimpleNamespace(id=10),
            agent_item=SimpleNamespace(),
            agent_backend=SimpleNamespace(),
            existing_run=None,
        )

    async def fake_create_input_message(**_kwargs):
        return SimpleNamespace(id=11)

    async def fake_persist(**_kwargs):
        _RUN.status = "pending"
        return _RUN, True

    async def fake_append(run_id, event_type, payload, *, thread_id=None):
        events.append((run_id, event_type, payload, thread_id))

    monkeypatch.setattr(svc, "CuratedQARepository", _FakeQaRepo)
    monkeypatch.setattr(svc, "AgentRunRepository", _FakeRunRepo)
    monkeypatch.setattr(svc, "ConversationRepository", _FakeConversationRepo)
    monkeypatch.setattr(svc, "prepare_agent_run_creation_scope", fake_prepare)
    monkeypatch.setattr(svc, "resolve_agent_run_model_spec", lambda *_args, **_kwargs: "provider:model")
    monkeypatch.setattr(svc, "create_agent_run_input_message", fake_create_input_message)
    monkeypatch.setattr(svc, "persist_agent_run_record", fake_persist)
    monkeypatch.setattr(svc, "append_run_stream_event", fake_append)

    result = await svc.try_create_curated_qa_run(
        input_message=build_chat_input_message("测试问题"),
        agent_slug="agent-1",
        thread_id="thread-1",
        meta={"request_id": "req-1"},
        current_uid="u-1",
        db=db,
    )

    assert result["status"] == "completed"
    assert [event[1] for event in events] == ["metadata", "custom", "messages", "end"]
    assert events[2][2]["chunk"]["response"] == "人工确认答案"
    assert events[3][2]["status"] == "completed"
    assert _FakeQaRepo.qa_pair.hit_count == 1
    assert db.commits == 1


def test_curated_qa_skips_image_attachment_and_evaluation_inputs():
    assert svc._eligible_for_curated_qa(build_chat_input_message("普通问题"), {}) is True
    assert svc._eligible_for_curated_qa(build_chat_input_message("图片问题", "base64"), {}) is False
    assert (
        svc._eligible_for_curated_qa(
            build_chat_input_message("附件问题"),
            {"attachment_file_ids": ["file-1"]},
        )
        is False
    )
    assert (
        svc._eligible_for_curated_qa(
            build_chat_input_message("评测问题"),
            {"source": "agent_evaluation"},
        )
        is False
    )
