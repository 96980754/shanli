"""历史版本阅读/对比 worker run 测试：载荷校验 + 生成器（read/compare/截断/异常）+ 来源挂接。"""

import json
from types import SimpleNamespace

import pytest

from yuxi.services import document_version_run_service as svc
from yuxi.services.document_diff_service import DocumentDiffNotFoundError
from yuxi.services.document_version_run_service import (
    DocumentVersionAskRequest,
    DocumentVersionFile,
)
from yuxi.services.input_message_service import build_chat_input_message


class _FakeDb:
    def __init__(self):
        self.commits = 0

    async def commit(self):
        self.commits += 1


class _FakeDiffService:
    """按 file_id 提供版本正文；未登记时抛与真实服务一致的未找到错误。"""

    def __init__(self, texts: dict[str, str]):
        self._texts = texts

    async def get_version_text(self, *, kb_id: str, file_id: str) -> str:
        del kb_id
        text = self._texts.get(file_id)
        if text is None:
            raise DocumentDiffNotFoundError(f"文件不存在: {file_id}")
        return text


class _GenConversationRepo:
    def __init__(self, db):
        self._db = db
        self.added = None
        self.tool_calls = []

    async def add_message_by_thread_id(self, **kwargs):
        self.added = kwargs
        return SimpleNamespace(id=22)

    async def add_tool_call(self, **kwargs):
        self.tool_calls.append(kwargs)


class _GenRunRepo:
    def __init__(self, db):
        self._db = db
        self.output = None

    async def set_output_message(self, run_id, message_id):
        self.output = (run_id, message_id)


def _gen_meta(version_ask: dict) -> dict:
    return {
        "run_id": "run-1",
        "request_id": "req-1",
        "model_spec": "provider:model",
        "uid": "user-1",
        "version_ask": version_ask,
    }


def _ask(
    *,
    action: str,
    files: list[dict],
    kb_id: str = "kb-1",
    title: str | None = "运营手册",
) -> dict:
    return {
        "kb_id": kb_id,
        "action": action,
        "file_ids": [item["file_id"] for item in files],
        "title": title,
        "versions": [
            {
                "file_id": item["file_id"],
                "document_version": item.get("document_version"),
                "filename": item["filename"],
                "is_current": bool(item.get("is_current", False)),
            }
            for item in files
        ],
    }


class _GeneratorHarness:
    def __init__(
        self,
        monkeypatch,
        *,
        version_ask: dict,
        texts: dict[str, str],
        answer: str = "历史版本内容概述。",
    ):
        self.meta = _gen_meta(version_ask)
        self.conv_repo = _GenConversationRepo(None)
        self.run_repo = _GenRunRepo(None)
        self.db = _FakeDb()
        self.compose_calls = []

        async def fake_compose(**_kwargs):
            self.compose_calls.append(_kwargs)
            return answer

        monkeypatch.setattr(svc, "ConversationRepository", lambda db: self.conv_repo)
        monkeypatch.setattr(svc, "AgentRunRepository", lambda db: self.run_repo)
        monkeypatch.setattr(svc, "DocumentDiffService", lambda: _FakeDiffService(texts))
        monkeypatch.setattr(svc, "compose_document_version_answer", fake_compose)

    async def run(self, content="查看《运营手册》历史版本 V1.1 的内容"):
        stream = svc.stream_document_version_answer(
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


def _payload_of(harness: _GeneratorHarness) -> dict:
    assert len(harness.conv_repo.tool_calls) == 1
    return json.loads(harness.conv_repo.tool_calls[0]["tool_output"])


# ---------------------------------------------------------------- 载荷校验

def test_validate_action_read_requires_exactly_one_file():
    assert DocumentVersionAskRequest(
        kb_id="kb-1", action="read", file_ids=["f-1"], versions=[]
    ).validate_action() is None
    assert (
        DocumentVersionAskRequest(
            kb_id="kb-1", action="read", file_ids=["f-1", "f-2"], versions=[]
        ).validate_action()
        is not None
    )


def test_validate_action_compare_requires_two_distinct_files():
    assert DocumentVersionAskRequest(
        kb_id="kb-1", action="compare", file_ids=["f-1", "f-2"], versions=[]
    ).validate_action() is None
    assert (
        DocumentVersionAskRequest(kb_id="kb-1", action="compare", file_ids=["f-1"], versions=[]).validate_action()
        is not None
    )
    assert (
        DocumentVersionAskRequest(
            kb_id="kb-1", action="compare", file_ids=["f-1", "f-1"], versions=[]
        ).validate_action()
        is not None
    )


def test_document_version_file_round_trip():
    item = DocumentVersionFile(file_id="f-1", document_version=1.1, filename="运营手册_V1.1.docx", is_current=False)
    assert item.model_dump()["document_version"] == 1.1
    assert item.model_dump()["is_current"] is False


# ---------------------------------------------------------------- 生成器 read

@pytest.mark.asyncio
async def test_generator_read_streams_then_persists_single_message(monkeypatch):
    """read 事件序 init→胶囊→正文→finished；落库恰一条带 document_version_answer 的回答。"""
    version_ask = _ask(
        action="read",
        files=[{"file_id": "f-1", "document_version": 1.1, "filename": "运营手册_V1.1.docx"}],
    )
    harness = _GeneratorHarness(monkeypatch, version_ask=version_ask, texts={"f-1": "历史版正文内容"})
    chunks = await harness.run()

    assert [chunk["status"] for chunk in chunks] == ["init", "stream_event", "loading", "finished"]

    pill = chunks[1]["event"]
    assert pill["method"] == "tools"
    assert pill["data"]["event"] == "tool-started"
    assert pill["data"]["tool_name"] == "read_document_version"

    delta = chunks[2]["stream_event"]
    assert delta["type"] == "message_delta"
    assert delta["message_id"] == "doc-version-run-1"
    assert delta["content"] == "历史版本内容概述。"

    added = harness.conv_repo.added
    assert added["content"] == "历史版本内容概述。"
    assert added["role"] == "assistant"
    assert added["extra_metadata"]["document_version_answer"] is True
    assert added["extra_metadata"]["document_version_meta"]["action"] == "read"
    assert harness.run_repo.output == ("run-1", 22)
    assert harness.db.commits >= 1

    payload = _payload_of(harness)
    assert payload["schema_version"] == 1
    assert payload["status"] == "ok"
    assert payload["kb_id"] == "kb-1"
    assert payload["results"][0]["file_id"] == "f-1"
    assert payload["results"][0]["metadata"]["source"] == "运营手册_V1.1.docx"
    assert payload["results"][0]["content"]  # 非空片段


@pytest.mark.asyncio
async def test_generator_compare_preserves_two_docs_order_and_labels(monkeypatch):
    """compare 读两个版本并保序传给模型；来源挂两枚不同文件卡。"""
    version_ask = _ask(
        action="compare",
        files=[
            {"file_id": "f-1", "document_version": 1.1, "filename": "运营手册_V1.1.docx"},
            {
                "file_id": "f-2",
                "document_version": 1.2,
                "filename": "运营手册_V1.2.docx",
                "is_current": True,
            },
        ],
    )
    texts = {"f-1": "旧版：第1章 A。", "f-2": "新版：第1章 B。"}
    harness = _GeneratorHarness(monkeypatch, version_ask=version_ask, texts=texts, answer="对比结果")
    chunks = await harness.run()

    assert [chunk["status"] for chunk in chunks] == ["init", "stream_event", "loading", "finished"]

    composed_docs = harness.compose_calls[0]["docs"]
    assert [doc["file_id"] for doc in composed_docs] == ["f-1", "f-2"]
    assert composed_docs[0]["is_current"] is False
    assert composed_docs[1]["is_current"] is True
    assert harness.compose_calls[0]["version_ask"]["action"] == "compare"

    payload = _payload_of(harness)
    assert [result["file_id"] for result in payload["results"]] == ["f-1", "f-2"]
    assert payload["results"][0]["metadata"]["source"] == "运营手册_V1.1.docx"
    assert payload["results"][1]["metadata"]["source"] == "运营手册_V1.2.docx"


@pytest.mark.asyncio
async def test_generator_long_document_truncates_and_appends_notice(monkeypatch):
    """超过预算的正文保头尾丢中段，回答末尾追加省略说明（不丢正文阅读）。"""
    version_ask = _ask(
        action="read",
        files=[{"file_id": "f-long", "document_version": 2.0, "filename": "长文_V2.0.docx"}],
    )
    long_text = "".join(f"第{i}节 这是很长的正文内容。\n" for i in range(500))
    monkeypatch.setattr(svc, "_max_input_chars", lambda: 120)
    harness = _GeneratorHarness(monkeypatch, version_ask=version_ask, texts={"f-long": long_text})
    chunks = await harness.run()

    assert [chunk["status"] for chunk in chunks] == ["init", "stream_event", "loading", "finished"]
    delta_content = chunks[2]["stream_event"]["content"]
    assert "中段未纳入" in delta_content
    assert harness.conv_repo.added["content"] == delta_content
    assert harness.db.commits >= 1


@pytest.mark.asyncio
async def test_generator_missing_source_emits_error_and_persists_nothing(monkeypatch):
    """归档文件不存在时生成器直接发 error，不落任何半截回答。"""
    version_ask = _ask(
        action="read",
        files=[{"file_id": "missing", "document_version": 0.9, "filename": "无此文档_V0.9.docx"}],
    )
    harness = _GeneratorHarness(monkeypatch, version_ask=version_ask, texts={})
    chunks = await harness.run()

    assert [chunk["status"] for chunk in chunks] == ["init", "stream_event", "error"]
    assert chunks[-1]["error_type"] == "document_version_source_unavailable"
    assert harness.conv_repo.added is None
    assert harness.conv_repo.tool_calls == []
    assert harness.run_repo.output is None
    assert harness.db.commits == 0
