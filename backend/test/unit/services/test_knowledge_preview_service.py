from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from yuxi.services.knowledge_preview_service import (
    INSUFFICIENT_ANSWER,
    KnowledgePreviewModelError,
    KnowledgePreviewRetrievalError,
    KnowledgePreviewService,
)


def _chunk(chunk_id: str, file_id: str, content: str, score: float = 0.8) -> dict:
    return {
        "content": content,
        "metadata": {
            "chunk_id": chunk_id,
            "file_id": file_id,
            "source": f"{file_id}.txt",
        },
        "score": score,
    }


def _file(file_id: str, *, current: bool, active: bool = True, kb_id: str = "kb-1") -> SimpleNamespace:
    return SimpleNamespace(
        file_id=file_id,
        kb_id=kb_id,
        is_current=current,
        is_active=active,
        document_version=2 if current else 1,
        previous_version_id=None,
        activated_at=datetime(2026, 8, 10),
        created_at=datetime(2026, 8, 9),
    )


def _database() -> dict:
    return {
        "kb_id": "kb-1",
        "kb_type": "milvus",
        "llm_model_spec": "provider:answer-model",
        "query_params": {
            "options": {
                "search_mode": "hybrid",
                "use_reranker": True,
                "use_graph_retrieval": False,
            }
        },
    }


@pytest.mark.asyncio
async def test_preview_uses_current_results_for_answer_and_citations():
    current = _chunk("chunk-v2", "file-v2", "最大并发用户数为 200。", 0.95)
    history = _chunk("chunk-v1", "file-v1", "最大并发用户数为 100。", 0.99)
    manager = SimpleNamespace(
        get_database_info=AsyncMock(return_value=_database()),
        aquery=AsyncMock(return_value=[history, current]),
    )
    repository = SimpleNamespace(
        list_by_file_ids=AsyncMock(return_value=[_file("file-v1", current=False), _file("file-v2", current=True)])
    )
    model = SimpleNamespace(call=AsyncMock(return_value=SimpleNamespace(content="最大并发用户数为 200。")))
    model_selector = Mock(return_value=model)
    service = KnowledgePreviewService(
        knowledge_manager=manager,
        file_repository=repository,
        model_selector=model_selector,
    )

    result = await service.preview(kb_id="kb-1", query="最大并发是多少？", meta={})

    assert result["answer"] == "最大并发用户数为 200。"
    assert [item["id"] for item in result["citations"]] == ["chunk-v2"]
    assert [item["id"] for item in result["retrieved_chunks"]] == ["chunk-v2"]
    assert result["retrieved_chunks"][0]["metadata"]["document_version"] == 2
    assert result["retrieval"] == {
        "mode": "hybrid",
        "top_k": 1,
        "rerank_enabled": True,
        "rerank_applied": False,
        "graph_enabled": False,
    }
    manager.aquery.assert_awaited_once_with(
        "最大并发是多少？",
        kb_id="kb-1",
        agent_call=True,
    )
    messages = model.call.await_args.args[0]
    assert "最大并发用户数为 200" in messages[1]["content"]
    assert "最大并发用户数为 100" not in messages[1]["content"]


@pytest.mark.asyncio
async def test_preview_without_results_refuses_without_calling_model():
    manager = SimpleNamespace(
        get_database_info=AsyncMock(return_value=_database()),
        aquery=AsyncMock(return_value=[]),
    )
    model_selector = Mock()
    service = KnowledgePreviewService(
        knowledge_manager=manager,
        file_repository=SimpleNamespace(list_by_file_ids=AsyncMock(return_value=[])),
        model_selector=model_selector,
    )

    result = await service.preview(kb_id="kb-1", query="未知问题", meta={"search_mode": "vector"})

    assert result["answer"] == INSUFFICIENT_ANSWER
    assert result["citations"] == []
    assert result["retrieved_chunks"] == []
    assert result["retrieval"]["mode"] == "vector"
    model_selector.assert_not_called()


@pytest.mark.asyncio
async def test_preview_can_keep_retrieval_only_mode_without_calling_model():
    manager = SimpleNamespace(
        get_database_info=AsyncMock(return_value=_database()),
        aquery=AsyncMock(return_value=[_chunk("chunk-v2", "file-v2", "当前内容")]),
    )
    repository = SimpleNamespace(list_by_file_ids=AsyncMock(return_value=[_file("file-v2", current=True)]))
    model_selector = Mock()
    service = KnowledgePreviewService(
        knowledge_manager=manager,
        file_repository=repository,
        model_selector=model_selector,
    )

    result = await service.preview(
        kb_id="kb-1",
        query="问题",
        meta={},
        generate_answer=False,
    )

    assert result["answer"] is None
    assert result["citations"] == []
    assert [item["id"] for item in result["retrieved_chunks"]] == ["chunk-v2"]
    model_selector.assert_not_called()


@pytest.mark.asyncio
async def test_preview_reports_retrieval_failure_without_model_call():
    manager = SimpleNamespace(
        get_database_info=AsyncMock(return_value=_database()),
        aquery=AsyncMock(side_effect=RuntimeError("internal endpoint")),
    )
    model_selector = Mock()
    service = KnowledgePreviewService(
        knowledge_manager=manager,
        file_repository=SimpleNamespace(),
        model_selector=model_selector,
    )

    with pytest.raises(KnowledgePreviewRetrievalError):
        await service.preview(kb_id="kb-1", query="问题", meta={})

    model_selector.assert_not_called()


@pytest.mark.asyncio
async def test_preview_reports_provider_failure_safely():
    manager = SimpleNamespace(
        get_database_info=AsyncMock(return_value=_database()),
        aquery=AsyncMock(return_value=[_chunk("chunk-v2", "file-v2", "当前内容")]),
    )
    repository = SimpleNamespace(list_by_file_ids=AsyncMock(return_value=[_file("file-v2", current=True)]))
    service = KnowledgePreviewService(
        knowledge_manager=manager,
        file_repository=repository,
        model_selector=Mock(side_effect=RuntimeError("secret provider URL")),
    )

    with pytest.raises(KnowledgePreviewModelError, match="model unavailable"):
        await service.preview(kb_id="kb-1", query="问题", meta={})


@pytest.mark.asyncio
async def test_preview_rejects_same_file_id_from_another_database():
    manager = SimpleNamespace(
        get_database_info=AsyncMock(return_value=_database()),
        aquery=AsyncMock(return_value=[_chunk("chunk-other", "file-other", "其它知识库内容")]),
    )
    repository = SimpleNamespace(
        list_by_file_ids=AsyncMock(return_value=[_file("file-other", current=True, kb_id="kb-2")])
    )
    model_selector = Mock()
    service = KnowledgePreviewService(
        knowledge_manager=manager,
        file_repository=repository,
        model_selector=model_selector,
    )

    result = await service.preview(kb_id="kb-1", query="问题", meta={})

    assert result["answer"] == INSUFFICIENT_ANSWER
    assert result["citations"] == []
    model_selector.assert_not_called()


@pytest.mark.asyncio
async def test_preview_derives_current_version_for_replacement_chain():
    current = _file("file-v2", current=True)
    current.document_version = 1
    current.previous_version_id = "file-v1"
    history = _file("file-v1", current=False)
    manager = SimpleNamespace(
        get_database_info=AsyncMock(return_value=_database()),
        aquery=AsyncMock(return_value=[_chunk("chunk-v2", "file-v2", "当前内容")]),
    )
    repository = SimpleNamespace(
        list_by_file_ids=AsyncMock(return_value=[current]),
        list_version_chains_for_current_files=AsyncMock(
            return_value={"file-v2": [current, history]}
        ),
    )
    model = SimpleNamespace(call=AsyncMock(return_value=SimpleNamespace(content="当前回答")))
    service = KnowledgePreviewService(
        knowledge_manager=manager,
        file_repository=repository,
        model_selector=Mock(return_value=model),
    )

    result = await service.preview(kb_id="kb-1", query="问题", meta={})

    assert result["retrieved_chunks"][0]["metadata"]["document_version"] == 2
    repository.list_version_chains_for_current_files.assert_awaited_once_with(
        kb_id="kb-1",
        file_ids=["file-v2"],
    )
