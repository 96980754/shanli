"""人工问答对（curated QA）命中路径测试：POST 只检测+持久化，组装/流式在 worker 生成器。"""

import json
from types import SimpleNamespace

import pytest

from yuxi.services import curated_qa_run_service as svc
from yuxi.services.input_message_service import build_chat_input_message

_EXTRA_SOURCES = [
    {
        "id": "kb-1:c1",
        "kb_id": "kb-1",
        "file_id": "f-1",
        "content": "规格正文一",
        "metadata": {"file_id": "f-1", "chunk_id": "c1", "source": "规格书A.pdf"},
    },
    {
        "id": "kb-2:c2",
        "kb_id": "kb-2",
        "file_id": "f-2",
        "content": "说明正文二",
        "metadata": {"file_id": "f-2", "chunk_id": "c2", "source": "说明书B.docx"},
    },
]


class _FakeDb:
    def __init__(self):
        self.commits = 0

    async def commit(self):
        self.commits += 1


# ---------------------------------------------------------------- POST 契约

def _post_scope(*, existing_run=None):
    return SimpleNamespace(
        conversation=SimpleNamespace(id=10),
        agent_item=SimpleNamespace(),
        agent_backend=SimpleNamespace(),
        existing_run=existing_run,
    )


class _PostQaRepo:
    """精确命中固定问答对；mark_hit 可被断言是否被调用。"""

    qa_pair = SimpleNamespace(id=7, question="测试问题", answer="人工确认答案", hit_count=0)

    def __init__(self, _db):
        pass

    async def get_exact(self, **_kwargs):
        return self.qa_pair

    async def mark_hit(self, item):
        item.hit_count += 1


def _patch_post(monkeypatch: pytest.MonkeyPatch, *, scope, qa_repo_cls=_PostQaRepo, semantic_pair=None):
    calls = {"enqueued": [], "persisted_payload": None, "semantic_matches": [], "composed": []}
    _PostQaRepo.qa_pair = SimpleNamespace(id=7, question="测试问题", answer="人工确认答案", hit_count=0)

    async def fake_create_input_message(**_kwargs):
        return SimpleNamespace(id=11)

    async def fake_persist(**_kwargs):
        calls["persisted_payload"] = _kwargs.get("input_payload")
        return SimpleNamespace(
            id="run-1",
            conversation_thread_id="thread-1",
            status="pending",
            request_id=_kwargs["request_id"],
        ), True

    async def fake_enqueue(run_id):
        calls["enqueued"].append(run_id)

    async def fake_semantic_match(_repo, agent_slug, question):
        calls["semantic_matches"].append((agent_slug, question))
        return semantic_pair

    async def fake_compose(*_args, **_kwargs):
        calls["composed"].append(1)
        return "不应在 POST 内组装"

    monkeypatch.setattr(svc, "CuratedQARepository", qa_repo_cls)
    monkeypatch.setattr(svc, "prepare_agent_run_creation_scope", _scope_wrapper(scope))
    monkeypatch.setattr(svc, "resolve_agent_run_model_spec", lambda *_args, **_kwargs: "provider:model")
    monkeypatch.setattr(svc, "create_agent_run_input_message", fake_create_input_message)
    monkeypatch.setattr(svc, "persist_agent_run_record", fake_persist)
    monkeypatch.setattr(svc, "enqueue_agent_run", fake_enqueue)
    monkeypatch.setattr(svc, "_semantic_match_curated_qa", fake_semantic_match)
    monkeypatch.setattr(svc, "_compose_answer_from_reference", fake_compose)
    return calls


def _scope_wrapper(scope):
    async def fake_prepare(**_kwargs):
        return scope

    return fake_prepare


@pytest.mark.asyncio
async def test_try_create_exact_hit_persists_run_and_enqueues_without_streaming(monkeypatch):
    """POST 命中只落 run + 投递队列，返回 pending；不再组装回答/写事件/mark_hit。"""
    db = _FakeDb()
    calls = _patch_post(monkeypatch, scope=_post_scope())

    result = await svc.try_create_curated_qa_run(
        input_message=build_chat_input_message("测试问题"),
        agent_slug="agent-1",
        thread_id="thread-1",
        meta={"request_id": "req-1"},
        current_uid="user-1",
        db=db,
    )

    assert result["run_id"] == "run-1"
    assert result["status"] == "pending"
    assert calls["enqueued"] == ["run-1"]
    assert calls["persisted_payload"] == {
        "model_spec": "provider:model",
        "answer_source": "curated_qa",
        "curated_qa_id": 7,
    }
    # 组装/检索/mark_hit 都留给 worker 生成器
    assert calls["composed"] == []
    assert calls["semantic_matches"] == []
    assert _PostQaRepo.qa_pair.hit_count == 0


@pytest.mark.asyncio
async def test_try_create_semantic_hit_stores_semantic_source_and_payload(monkeypatch):
    """语义命中落 curated_qa_semantic 与命中问答对 id；引导组装留 worker，POST 不再调用模型。"""
    db = _FakeDb()
    semantic_pair = SimpleNamespace(id=9, question="原问题", answer="人工确认答案", hit_count=0)

    class _SemanticQaRepo(_PostQaRepo):
        async def get_exact(self, **_kwargs):
            return None

        async def mark_hit(self, item):
            item.hit_count += 1

    calls = _patch_post(monkeypatch, scope=_post_scope(), qa_repo_cls=_SemanticQaRepo, semantic_pair=semantic_pair)

    result = await svc.try_create_curated_qa_run(
        input_message=build_chat_input_message("改述问题"),
        agent_slug="agent-1",
        thread_id="thread-1",
        meta={"request_id": "req-1"},
        current_uid="user-1",
        db=db,
    )

    assert result["status"] == "pending"
    assert calls["persisted_payload"]["answer_source"] == "curated_qa_semantic"
    assert calls["persisted_payload"]["curated_qa_id"] == 9
    assert calls["semantic_matches"] == [("agent-1", "改述问题")]
    assert calls["composed"] == []
    assert semantic_pair.hit_count == 0


@pytest.mark.asyncio
async def test_try_create_no_match_returns_none_without_enqueue(monkeypatch):
    db = _FakeDb()
    calls = _patch_post(monkeypatch, scope=_post_scope(), semantic_pair=None)
    calls["enqueued"].clear()

    class _EmptyRepo:
        def __init__(self, _db):
            pass

        async def get_exact(self, **_kwargs):
            return None

    monkeypatch.setattr(svc, "CuratedQARepository", _EmptyRepo)

    result = await svc.try_create_curated_qa_run(
        input_message=build_chat_input_message("没有命中"),
        agent_slug="agent-1",
        thread_id="thread-1",
        meta={"request_id": "req-1"},
        current_uid="user-1",
        db=db,
    )

    assert result is None
    assert calls["enqueued"] == []


@pytest.mark.asyncio
async def test_try_create_existing_run_short_circuits(monkeypatch):
    """重复请求命中已有 run 时直接返回其视图，不重复建 run/投递。"""
    db = _FakeDb()
    existing = SimpleNamespace(id="run-9", conversation_thread_id="thread-1", status="pending", request_id="req-9")
    calls = _patch_post(monkeypatch, scope=_post_scope(existing_run=existing))

    result = await svc.try_create_curated_qa_run(
        input_message=build_chat_input_message("测试问题"),
        agent_slug="agent-1",
        thread_id="thread-1",
        meta={"request_id": "req-1"},
        current_uid="user-1",
        db=db,
    )

    assert result["run_id"] == "run-9"
    assert calls["enqueued"] == []
    assert calls["persisted_payload"] is None
    assert _PostQaRepo.qa_pair.hit_count == 0


def test_eligible_for_curated_qa_gating():
    assert svc._eligible_for_curated_qa(build_chat_input_message("普通问题"), {}) is True
    assert svc._eligible_for_curated_qa(build_chat_input_message("图片问题", "base64"), {}) is False
    assert (
        svc._eligible_for_curated_qa(build_chat_input_message("附件问题"), {"attachment_file_ids": ["file-1"]})
        is False
    )
    assert (
        svc._eligible_for_curated_qa(build_chat_input_message("评测问题"), {"source": "agent_evaluation"}) is False
    )


# ---------------------------------------------------------------- worker 生成器契约


def _gen_meta(**overrides):
    meta = {
        "run_id": "run-1",
        "request_id": "req-1",
        "model_spec": "provider:model",
        "answer_source": "curated_qa",
        "curated_qa_id": 7,
        "uid": "user-1",
    }
    meta.update(overrides)
    return meta


def _qa_pair(**overrides):
    base = dict(id=7, question="测试问题", answer="人工确认答案", hit_count=0)
    base.update(overrides)
    return SimpleNamespace(**base)


class _GenQaRepo:
    """构造时绑定问答对；get 按 id 返回，mark_hit 累加计数。"""

    def __init__(self, db, qa_pair):
        self._db = db
        self.qa_pair = qa_pair

    async def get(self, qa_id):
        if self.qa_pair is None:
            return None
        return self.qa_pair if qa_id == self.qa_pair.id else None

    async def get_enabled(self, qa_id):
        if self.qa_pair is None or not getattr(self.qa_pair, "enabled", True):
            return None
        return self.qa_pair if qa_id == self.qa_pair.id else None

    async def mark_hit(self, item):
        item.hit_count += 1


class _GenConversationRepo:
    def __init__(self, db):
        self._db = db
        self.added = None

    async def add_message_by_thread_id(self, **kwargs):
        self.added = kwargs
        return SimpleNamespace(id=22)


class _GenRunRepo:
    def __init__(self, db):
        self._db = db
        self.output = None

    async def set_output_message(self, run_id, message_id):
        self.output = (run_id, message_id)


class _GeneratorHarness:
    def __init__(self, monkeypatch, *, qa_pair=None, meta=None, retrieve=None, compose_kb=None, attach=None):
        self.meta = _gen_meta(**(meta or {}))
        self.qa_pair = qa_pair or _qa_pair()
        self.qa_repo = _GenQaRepo(None, self.qa_pair)
        self.conv_repo = _GenConversationRepo(None)
        self.run_repo = _GenRunRepo(None)
        self.db = _FakeDb()
        self.retrieve_calls = []
        self.attach_calls = []
        self.scope = SimpleNamespace(agent_item=SimpleNamespace(), agent_backend=SimpleNamespace())

        async def fake_prepare(**_kwargs):
            return self.scope

        async def fake_retrieve(**_kwargs):
            self.retrieve_calls.append(1)
            return retrieve if retrieve is not None else []

        async def fake_compose_kb(*_args, **_kwargs):
            return compose_kb or ""

        async def fake_attach(**kwargs):
            self.attach_calls.append(kwargs)

        monkeypatch.setattr(svc, "CuratedQARepository", lambda db: self.qa_repo)
        monkeypatch.setattr(svc, "ConversationRepository", lambda db: self.conv_repo)
        monkeypatch.setattr(svc, "AgentRunRepository", lambda db: self.run_repo)
        monkeypatch.setattr(svc, "prepare_agent_run_creation_scope", fake_prepare)
        monkeypatch.setattr(svc, "_retrieve_extra_sources", fake_retrieve)
        monkeypatch.setattr(svc, "_compose_answer_from_knowledge", fake_compose_kb)
        monkeypatch.setattr(svc, "_attach_extra_retrieval_tool_call", fake_attach)

    async def run(self, content="测试问题"):
        stream = svc.stream_curated_qa_answer(
            agent_slug="agent-1",
            thread_id="thread-1",
            meta=self.meta,
            input_message=build_chat_input_message(content),
            current_user=SimpleNamespace(uid="user-1"),
            db=self.db,
        )
        chunks = []
        async for data in stream:
            for line in data.decode("utf-8").splitlines():
                line = line.strip()
                if line:
                    chunks.append(json.loads(line))
        return chunks


@pytest.mark.asyncio
async def test_generator_exact_hit_streams_base_then_finished_without_extra(monkeypatch):
    """知识库检索无结果：回落人工答案，事件流 base→finished，mark_hit 恰一次。"""
    harness = _GeneratorHarness(monkeypatch)
    chunks = await harness.run()

    assert [chunk["status"] for chunk in chunks] == ["init", "stream_event", "loading", "finished"]
    pill = chunks[1]["event"]
    assert pill["method"] == "tools"
    assert pill["data"]["event"] == "tool-started"
    assert pill["data"]["tool_name"] == "query_kbs"

    base_delta = chunks[2]["stream_event"]
    assert base_delta["type"] == "message_delta"
    assert base_delta["message_id"] == "curated-qa-run-1"
    assert base_delta["content"] == "人工确认答案"

    assert harness.conv_repo.added["content"] == "人工确认答案"
    meta = harness.conv_repo.added["extra_metadata"]
    assert meta["answer_source"] == "curated_qa"
    assert meta["human_confirmed"] is True  # 类别3：人工确认答案，非模型逐库检索作答
    # 命中问答对走独立快答 run，不落知识 disposition/横幅/转人工标记（多轮线程视为已正常作答）。
    assert "knowledge_disposition" not in meta
    assert "handoff_available" not in meta
    assert "knowledge_no_evidence" not in meta
    assert harness.run_repo.output == ("run-1", 22)
    assert harness.qa_pair.hit_count == 1
    assert harness.attach_calls == []
    assert chunks[-1]["status"] == "finished"


@pytest.mark.asyncio
async def test_generator_kb_hit_answers_from_knowledge_first(monkeypatch):
    """知识库优先（二期反馈 2026-09-14 问答对调优）：检索有依据时以库内最新为准作答。

    回答为知识库组织结果而非人工答案，answer_source=curated_qa_kb_first、
    human_confirmed=False，检索片段作为来源挂到消息上。
    """
    harness = _GeneratorHarness(
        monkeypatch,
        retrieve=_EXTRA_SOURCES,
        compose_kb="以知识库为准的最新答案，详见《规格书A.pdf》第 3 章。",
    )
    chunks = await harness.run()

    assert [chunk["status"] for chunk in chunks] == ["init", "stream_event", "loading", "finished"]
    assert chunks[1]["event"]["data"]["event"] == "tool-started"

    answer_delta = chunks[2]["stream_event"]
    assert answer_delta["content"] == "以知识库为准的最新答案，详见《规格书A.pdf》第 3 章。"

    assert harness.conv_repo.added["content"] == "以知识库为准的最新答案，详见《规格书A.pdf》第 3 章。"
    meta = harness.conv_repo.added["extra_metadata"]
    assert meta["answer_source"] == "curated_qa_kb_first"
    assert meta["human_confirmed"] is False  # 类别1：以知识库证据作答
    assert meta["curated_qa_id"] == 7
    assert harness.attach_calls == [
        {
            "db": harness.db,
            "message_id": 22,
            "question": "测试问题",
            "sources": _EXTRA_SOURCES,
        }
    ]
    assert harness.qa_pair.hit_count == 1


@pytest.mark.asyncio
async def test_generator_retrieval_irrelevant_falls_back_to_curated_answer(monkeypatch):
    """检索有返回但片段与问题无关（组织不出回答）时：回落人工答案，不挂来源。

    回归：曾按 extra_sources 非空就 attach query_kbs，导致对 C++ 这类通用问题补检索
    命中的无关文档（如产品白皮书）被前端当成回答来源展示。
    """
    harness = _GeneratorHarness(
        monkeypatch,
        retrieve=_EXTRA_SOURCES,
        compose_kb="",  # 组织模型认为片段与问题无关，判定「无法回答」
    )
    chunks = await harness.run()

    assert [chunk["status"] for chunk in chunks] == ["init", "stream_event", "loading", "finished"]
    assert harness.conv_repo.added["content"] == "人工确认答案"
    assert harness.conv_repo.added["extra_metadata"]["answer_source"] == "curated_qa"
    assert harness.attach_calls == []
    assert harness.qa_pair.hit_count == 1


@pytest.mark.asyncio
async def test_generator_retrieval_failure_falls_back_to_base_answer(monkeypatch):
    """检索抛错回落人工答案，照常落库并正常 finished。"""

    async def fake_retrieve(**_kwargs):
        raise RuntimeError("检索服务不可用")

    harness = _GeneratorHarness(monkeypatch, retrieve=None, compose_kb="")
    monkeypatch.setattr(svc, "_retrieve_extra_sources", fake_retrieve)
    chunks = await harness.run()

    assert [chunk["status"] for chunk in chunks] == ["init", "stream_event", "loading", "finished"]
    assert harness.conv_repo.added["content"] == "人工确认答案"
    assert harness.qa_pair.hit_count == 1


@pytest.mark.asyncio
async def test_generator_missing_qa_pair_emits_error(monkeypatch):
    """问答对被删除时生成器直接发 error，不落库也不 mark_hit。"""
    harness = _GeneratorHarness(monkeypatch, meta={"curated_qa_id": 999})
    harness.qa_repo.qa_pair = None
    chunks = await harness.run()

    assert [chunk["status"] for chunk in chunks] == ["error"]
    assert chunks[0]["error_type"] == "curated_qa_missing"
    assert harness.conv_repo.added is None
    assert harness.qa_pair.hit_count == 0


@pytest.mark.asyncio
async def test_generator_semantic_hit_composes_reference_answer(monkeypatch):
    """语义命中的基础答案由模型按参考组织（worker 内），POST 不再组装。"""
    semantic_pair = _qa_pair(id=9, question="原问题", answer="人工确认答案")

    async def fake_compose(model_spec, question, pair):
        assert pair is semantic_pair
        return "参考改写后的答案"

    harness = _GeneratorHarness(
        monkeypatch,
        qa_pair=semantic_pair,
        meta={"curated_qa_id": 9, "answer_source": "curated_qa_semantic"},
        compose_kb="",
    )
    monkeypatch.setattr(svc, "_compose_answer_from_reference", fake_compose)
    chunks = await harness.run(content="改述问题")

    base_delta = next(chunk for chunk in chunks if chunk["status"] == "loading")
    assert base_delta["stream_event"]["content"] == "参考改写后的答案"
    assert harness.conv_repo.added["content"] == "参考改写后的答案"
    assert harness.conv_repo.added["extra_metadata"]["answer_source"] == "curated_qa_semantic"
    assert semantic_pair.hit_count == 1


# ---------------------------------------------------------------- 组件级

class _ToolCallConvRepo:
    def __init__(self, _db):
        self.tool_call = None

    async def add_tool_call(self, **kwargs):
        self.tool_call = kwargs
        return SimpleNamespace(id=33)


@pytest.mark.asyncio
async def test_attach_extra_retrieval_tool_call_serializes_sources(monkeypatch):
    repo = _ToolCallConvRepo(None)
    monkeypatch.setattr(svc, "ConversationRepository", lambda db: repo)

    await svc._attach_extra_retrieval_tool_call(
        db=_FakeDb(),
        message_id=22,
        question="测试问题",
        sources=_EXTRA_SOURCES,
    )

    call = repo.tool_call
    assert call["tool_name"] == "query_kbs"
    assert call["status"] == "success"
    assert call["tool_input"]["query_text"] == "测试问题"
    assert call["tool_input"]["kb_ids"] == ["kb-1", "kb-2"]
    payload = json.loads(call["tool_output"])
    assert payload["schema_version"] == 1
    assert payload["status"] == "ok"
    assert len(payload["results"]) == 2


@pytest.mark.asyncio
async def test_compose_kb_first_pins_citations_and_kb_priority(monkeypatch):
    """知识库优先组织：提示词要求以检索片段为准（冲突不沿用参考答案），无法回答时返回空。"""
    captured = {}

    class _FakeModel:
        async def call(self, messages, **kwargs):
            captured["messages"] = messages
            return SimpleNamespace(content="以知识库为准的答案")

    monkeypatch.setattr("yuxi.models.chat.select_model", lambda _spec: _FakeModel())
    result = await svc._compose_answer_from_knowledge(
        "provider:model", "项目何时发布？", _EXTRA_SOURCES, _qa_pair()
    )
    assert result == "以知识库为准的答案"
    system_content = captured["messages"][0]["content"]
    user_content = captured["messages"][1]["content"]
    # 知识库优先的关键约束：冲突以检索片段为准（与旧「以原回答为准」相反）
    assert "无法回答" in system_content and "以检索片段" in system_content
    assert "《规格书A.pdf》" in user_content
    assert "人工确认答案" in user_content  # 人工答案仅作参考出现在输入里

    class _NoAnswerModel:
        async def call(self, messages, **kwargs):
            return SimpleNamespace(content="无法回答")

    monkeypatch.setattr("yuxi.models.chat.select_model", lambda _spec: _NoAnswerModel())
    suppressed = await svc._compose_answer_from_knowledge(
        "provider:model", "项目何时发布？", _EXTRA_SOURCES, _qa_pair()
    )
    assert suppressed == ""


@pytest.mark.asyncio
async def test_retrieve_extra_sources_scopes_to_agent_kbs_and_filters_duplicates(monkeypatch):
    calls = {"kb_ids": []}

    class _FakeExecuteDb:
        async def execute(self, _stmt):
            return SimpleNamespace(scalar_one_or_none=lambda: SimpleNamespace(uid="user-1", role="user"))

    async def fake_normalize(_context, *, db, user, context_schema):
        calls["user_uid"] = user.uid
        return {"knowledges": ["kb-1", "kb-2", "kb-down"]}

    async def fake_aquery(query, kb_id, agent_call=False, **kwargs):
        calls["kb_ids"].append(kb_id)
        if kb_id == "kb-down":
            raise RuntimeError("检索服务不可用")
        if kb_id == "kb-1":
            return [
                {"content": "片段一", "score": 0.9, "metadata": {"file_id": "f1", "chunk_id": "c1"}},
                {"content": "", "metadata": {"file_id": "f1", "chunk_id": "c2"}},
            ]
        return [
            {"content": "片段二", "score": 0.8, "metadata": {"file_id": "f2", "chunk_id": "c3"}},
            {"content": "片段一", "score": 0.9, "metadata": {"file_id": "f1", "chunk_id": "c1"}},
        ]

    def fake_build_output(kb_id, raw):
        results = []
        for chunk in raw:
            if not isinstance(chunk, dict):
                continue
            metadata = dict(chunk.get("metadata") or {})
            file_id = metadata.get("file_id") or ""
            chunk_id = metadata.get("chunk_id")
            results.append(
                {
                    "id": str(chunk_id or file_id),
                    "kb_id": kb_id,
                    "file_id": str(file_id),
                    "content": str(chunk.get("content") or ""),
                    "metadata": metadata,
                }
            )
        return {"kb_id": kb_id, "results": results}

    monkeypatch.setattr("yuxi.agents.context.normalize_agent_context_config", fake_normalize)
    monkeypatch.setattr("yuxi.knowledge.base.KnowledgeBase.build_search_output", staticmethod(fake_build_output))
    monkeypatch.setattr("yuxi.knowledge.runtime.knowledge_base", SimpleNamespace(aquery=fake_aquery))

    merged = await svc._retrieve_extra_sources(
        db=_FakeExecuteDb(),
        current_uid="user-1",
        agent_item=SimpleNamespace(config_json={}),
        agent_backend=SimpleNamespace(context_schema=object),
        question="测试问题",
    )

    assert calls["user_uid"] == "user-1"
    assert calls["kb_ids"] == ["kb-1", "kb-2", "kb-down"]
    assert [item["content"] for item in merged] == ["片段一", "片段二"]
