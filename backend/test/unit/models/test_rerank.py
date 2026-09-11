from typing import Any

import pytest

from yuxi.models.rerank import OpenAIReranker


class FakeResponse:
    def __init__(self, payload: dict[str, Any]):
        self.payload = payload

    async def json(self) -> dict[str, Any]:
        return self.payload

    def raise_for_status(self) -> None:
        return None


class FakePost:
    def __init__(self, payload: dict[str, Any]):
        self.payload = payload

    async def __aenter__(self) -> FakeResponse:
        return FakeResponse(self.payload)

    async def __aexit__(self, *exc_info) -> bool:
        return False


class FakeSession:
    def __init__(self, payload: dict[str, Any]):
        self.payload = payload
        self.closed = False

    def post(self, url: str, json: dict[str, Any]) -> FakePost:
        return FakePost(self.payload)

    async def close(self) -> None:
        self.closed = True


def make_reranker(payload: dict[str, Any]) -> OpenAIReranker:
    reranker = OpenAIReranker(model_name="test-model", api_key="key", base_url="http://localhost:1/v1/rerank")
    reranker.session = FakeSession(payload)
    return reranker


async def test_batch_rerank_aligns_scores_by_index():
    """供应商按分数降序返回（index 乱序），必须靠 index 对回原文档顺序。"""
    reranker = make_reranker(
        {
            "results": [
                {"index": 1, "relevance_score": 0.9},
                {"index": 2, "relevance_score": 0.3},
                {"index": 0, "relevance_score": 0.1},
            ]
        }
    )

    scores = await reranker._batch_rerank("query", ["doc-0", "doc-1", "doc-2"], max_length=512)

    assert scores == [0.1, 0.9, 0.3]


async def test_batch_rerank_refuses_to_align_without_index():
    """缺 index 时按返回顺序对齐会把分数错配到别的文档上，宁可让本次精排失败。"""
    reranker = make_reranker({"results": [{"relevance_score": 0.9}, {"relevance_score": 0.1}]})

    with pytest.raises(ValueError, match="index"):
        await reranker._batch_rerank("query", ["doc-0", "doc-1"], max_length=512)


async def test_batch_failure_uses_neutral_score(monkeypatch):
    """单批失败时的占位分必须归一化后仍是中性（0.5），不能高于真正不相关的片段。"""
    reranker = make_reranker({})

    async def failing_batch(query, documents, max_length):
        raise RuntimeError("provider down")

    monkeypatch.setattr(reranker, "_batch_rerank", failing_batch)

    scores = await reranker.acompute_score(["query", ["doc-0", "doc-1"]], normalize=True)

    assert scores == [0.5, 0.5]
