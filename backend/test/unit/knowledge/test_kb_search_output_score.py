from __future__ import annotations

from yuxi.knowledge.base import KnowledgeBase


def test_build_search_output_uses_rerank_score_as_display_score() -> None:
    """精排分是排序轴，就必须同时是展示分；原始检索分另存备查。"""
    output = KnowledgeBase.build_search_output(
        "kb-1",
        [{"content": "正文", "metadata": {"chunk_id": "c-1"}, "score": 0.92, "rerank_score": 0.58}],
    )

    metadata = output["results"][0]["metadata"]
    assert metadata["score"] == 0.58
    assert metadata["rerank_score"] == 0.58
    assert metadata["retrieval_score"] == 0.92


def test_build_search_output_keeps_retrieval_score_without_rerank() -> None:
    output = KnowledgeBase.build_search_output(
        "kb-1",
        [{"content": "正文", "metadata": {"chunk_id": "c-1"}, "score": 0.92}],
    )

    metadata = output["results"][0]["metadata"]
    assert metadata["score"] == 0.92
    assert "rerank_score" not in metadata
