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


@pytest.mark.asyncio
async def test_curated_qa_semantic_hit_composes_answer_from_reference(monkeypatch):
    db = _FakeDb()
    events = []
    semantic_pair = SimpleNamespace(id=9, question="原问题", answer="人工确认答案", hit_count=0)

    class _SemanticQaRepo:
        def __init__(self, _db):
            pass

        async def get_exact(self, **_kwargs):
            return None

        async def mark_hit(self, item):
            item.hit_count += 1

    class _SemanticConversationRepo:
        def __init__(self, _db):
            pass

        async def add_message_by_thread_id(self, **kwargs):
            assert kwargs["content"] == "参考改写后的答案"
            assert kwargs["extra_metadata"]["answer_source"] == "curated_qa_semantic"
            assert kwargs["extra_metadata"]["curated_qa_id"] == 9
            return SimpleNamespace(id=22)

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

    async def fake_semantic_match(repo, agent_slug, question):
        assert agent_slug == "agent-1"
        return semantic_pair

    async def fake_compose(model_spec, question, pair):
        assert pair is semantic_pair
        return "参考改写后的答案"

    monkeypatch.setattr(svc, "CuratedQARepository", _SemanticQaRepo)
    monkeypatch.setattr(svc, "AgentRunRepository", _FakeRunRepo)
    monkeypatch.setattr(svc, "ConversationRepository", _SemanticConversationRepo)
    monkeypatch.setattr(svc, "prepare_agent_run_creation_scope", fake_prepare)
    monkeypatch.setattr(svc, "resolve_agent_run_model_spec", lambda *_args, **_kwargs: "provider:model")
    monkeypatch.setattr(svc, "create_agent_run_input_message", fake_create_input_message)
    monkeypatch.setattr(svc, "persist_agent_run_record", fake_persist)
    monkeypatch.setattr(svc, "append_run_stream_event", fake_append)
    monkeypatch.setattr(svc, "_semantic_match_curated_qa", fake_semantic_match)
    monkeypatch.setattr(svc, "_compose_answer_from_reference", fake_compose)

    result = await svc.try_create_curated_qa_run(
        input_message=build_chat_input_message("改述问题"),
        agent_slug="agent-1",
        thread_id="thread-1",
        meta={"request_id": "req-1"},
        current_uid="u-1",
        db=db,
    )

    assert result["status"] == "completed"
    assert events[2][2]["chunk"]["response"] == "参考改写后的答案"
    assert events[3][2]["chunk"]["meta"]["answer_source"] == "curated_qa_semantic"
    assert semantic_pair.hit_count == 1


@pytest.mark.asyncio
async def test_curated_qa_semantic_no_match_falls_back_to_normal_flow(monkeypatch):
    db = _FakeDb()

    class _EmptyQaRepo:
        def __init__(self, _db):
            pass

        async def get_exact(self, **_kwargs):
            return None

    async def fake_semantic_match(repo, agent_slug, question):
        return None

    monkeypatch.setattr(svc, "CuratedQARepository", _EmptyQaRepo)
    monkeypatch.setattr(svc, "_semantic_match_curated_qa", fake_semantic_match)

    result = await svc.try_create_curated_qa_run(
        input_message=build_chat_input_message("改述问题"),
        agent_slug="agent-1",
        thread_id="thread-1",
        meta={"request_id": "req-1"},
        current_uid="u-1",
        db=db,
    )

    assert result is None


@pytest.mark.asyncio
async def test_curated_qa_exact_hit_skips_semantic_match(monkeypatch):
    db = _FakeDb()
    called = []

    async def fake_semantic_match(repo, agent_slug, question):
        called.append(question)
        return None

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

    async def fake_append(*_args, **_kwargs):
        return None

    monkeypatch.setattr(svc, "CuratedQARepository", _FakeQaRepo)
    monkeypatch.setattr(svc, "AgentRunRepository", _FakeRunRepo)
    monkeypatch.setattr(svc, "ConversationRepository", _FakeConversationRepo)
    monkeypatch.setattr(svc, "prepare_agent_run_creation_scope", fake_prepare)
    monkeypatch.setattr(svc, "resolve_agent_run_model_spec", lambda *_args, **_kwargs: "provider:model")
    monkeypatch.setattr(svc, "create_agent_run_input_message", fake_create_input_message)
    monkeypatch.setattr(svc, "persist_agent_run_record", fake_persist)
    monkeypatch.setattr(svc, "append_run_stream_event", fake_append)
    monkeypatch.setattr(svc, "_semantic_match_curated_qa", fake_semantic_match)

    await svc.try_create_curated_qa_run(
        input_message=build_chat_input_message("测试问题"),
        agent_slug="agent-1",
        thread_id="thread-1",
        meta={"request_id": "req-1"},
        current_uid="u-1",
        db=db,
    )

    # 精确命中时不再做语义匹配，直接输出原答案
    assert called == []
