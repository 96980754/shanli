from types import SimpleNamespace

import pytest

from app.services.tools import KnowledgeTools


@pytest.mark.asyncio
async def test_bm25_search_returns_exact_model_match_first():
    tools = KnowledgeTools()
    tools.build_bm25_index(
        "kb-1",
        [
            {"chunk_id": "a", "content": "P368 支持 SOS 报警和定位。", "score": 0.0},
            {"chunk_id": "b", "content": "普通终端支持语音通话。", "score": 0.0},
        ],
    )

    result = await tools.bm25_search("P368 SOS", "kb-1", top_k=1)

    assert result[0]["chunk_id"] == "a"
    assert result[0]["score"] > 0


@pytest.mark.asyncio
async def test_tools_reload_policy_for_retrieval_and_bm25(tmp_path):
    policy_path = tmp_path / "policy.yaml"
    policy_path.write_text(
        """
type_weight: {WP: 1.0, UM: 0.9, OTH: 0.3}
product_weight: {MC: 1.0, MS: 0.9, GEN: 0.8}
priority_boost: {P0: 1.2, P1: 1.0, P2: 0.8}
formula: {similarity_ratio: 0.75, type_ratio: 0.10, product_ratio: 0.10, priority_ratio: 0.05}
top_k: {initial: 100, after_rerank: 20, final: 10}
""",
        encoding="utf-8",
    )
    tools = KnowledgeTools()
    tools.set_retrieval_policy_path(policy_path)
    chunks = [
        {"chunk_id": "mc", "content": "MCSTARS deployment guide", "score": 0.91, "document_type": "UM", "product": "MC", "priority": "P0"},
        {"chunk_id": "ms", "content": "MCSTARS deployment guide", "score": 0.93, "document_type": "UM", "product": "MS", "priority": "P0"},
    ]
    tools.vector_chunks_by_kb["kb-1"] = chunks
    tools.build_bm25_index("kb-1", chunks)

    retrieved = await tools.retrieve("MCSTARS deployment", "kb-1", top_k=1)
    bm25 = await tools.bm25_search("MCSTARS deployment", "kb-1", top_k=1)

    assert retrieved[0]["chunk_id"] == "mc"
    assert bm25[0]["chunk_id"] == "mc"
    assert "metadata_score" in retrieved[0]

@pytest.mark.asyncio
async def test_retrieval_policy_applies_after_rerank_before_final_limit(monkeypatch):
    tools = KnowledgeTools()
    fake_policy = SimpleNamespace(
        top_k=SimpleNamespace(initial=5, after_rerank=2, final=4),
        detect_products=lambda query: set(),
        rerank=lambda chunks, matched_products: list(chunks),
    )
    monkeypatch.setattr(tools, "_retrieval_policy", lambda: fake_policy)
    tools.vector_chunks_by_kb["kb-1"] = [
        {"chunk_id": "a", "content": "policy", "score": 0.9},
        {"chunk_id": "b", "content": "policy", "score": 0.8},
        {"chunk_id": "c", "content": "policy", "score": 0.7},
        {"chunk_id": "d", "content": "policy", "score": 0.6},
        {"chunk_id": "e", "content": "policy", "score": 0.5},
    ]

    result = await tools.retrieve("policy", "kb-1", top_k=4)

    assert [item["chunk_id"] for item in result] == ["a", "b"]


@pytest.mark.asyncio
async def test_tools_reloads_changed_policy_without_rebuilding_index(tmp_path):
    policy_path = tmp_path / "policy.yaml"
    policy_path.write_text(
        """
type_weight: {WP: 1.0, UM: 0.1, OTH: 0.3}
product_weight: {GEN: 0.8}
priority_boost: {P0: 1.2, P2: 0.8}
formula: {similarity_ratio: 0.0, type_ratio: 0.8, product_ratio: 0.0, priority_ratio: 0.2}
top_k: {initial: 100, after_rerank: 20, final: 10}
""",
        encoding="utf-8",
    )
    tools = KnowledgeTools()
    tools.set_retrieval_policy_path(policy_path)
    chunks = [
        {"chunk_id": "whitepaper", "content": "deployment", "score": 1.0, "document_type": "WP", "product": "GEN", "priority": "P0"},
        {"chunk_id": "manual", "content": "deployment", "score": 1.0, "document_type": "UM", "product": "GEN", "priority": "P2"},
    ]
    tools.vector_chunks_by_kb["kb-1"] = chunks

    first = await tools.retrieve("deployment", "kb-1", top_k=1)
    policy_path.write_text(
        """
type_weight: {WP: 0.1, UM: 1.0, OTH: 0.3}
product_weight: {GEN: 0.8}
priority_boost: {P0: 0.8, P2: 1.2}
formula: {similarity_ratio: 0.0, type_ratio: 0.8, product_ratio: 0.0, priority_ratio: 0.2}
top_k: {initial: 100, after_rerank: 20, final: 10}
""",
        encoding="utf-8",
    )
    second = await tools.retrieve("deployment", "kb-1", top_k=1)

    assert first[0]["chunk_id"] == "whitepaper"
    assert second[0]["chunk_id"] == "manual"
