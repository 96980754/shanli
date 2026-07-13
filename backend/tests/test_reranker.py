from app.services.reranker import RuleReranker
from app.services.retrieval_policy import RetrievalPolicy


def test_reranker_uses_retrieval_policy_when_provided(tmp_path):
    policy_path = tmp_path / "policy.yaml"
    policy_path.write_text(
        """
type_weight: {WP: 1.0, DG: 0.8, OTH: 0.3}
product_weight: {MC: 1.0, MS: 0.9, GEN: 0.8}
priority_boost: {P0: 1.2, P1: 1.0, P2: 0.8}
formula: {similarity_ratio: 0.75, type_ratio: 0.10, product_ratio: 0.10, priority_ratio: 0.05}
top_k: {initial: 100, after_rerank: 20, final: 10}
""",
        encoding="utf-8",
    )
    result = RuleReranker().rerank(
        "MCSTARS 部署",
        [
            {"chunk_id": "mc", "score": 0.91, "document_type": "DG", "product": "MC", "priority": "P0"},
            {"chunk_id": "ms", "score": 0.93, "document_type": "DG", "product": "MS", "priority": "P0"},
        ],
        top_k=1,
        policy=RetrievalPolicy.load(policy_path),
        matched_products={"MC"},
    )

    assert result[0]["chunk_id"] == "mc"
    assert "metadata_score" in result[0]


def test_reranker_prefers_chunks_with_query_terms_in_content_and_heading():
    reranker = RuleReranker()
    chunks = [
        {
            "chunk_id": "a",
            "content": "设备支持普通语音通话。",
            "heading": "基础功能",
            "score": 0.9,
            "chunk_index": 1,
        },
        {
            "chunk_id": "b",
            "content": "SOS 报警可以在报警设置里关闭。",
            "heading": "SOS 报警设置",
            "score": 0.7,
            "chunk_index": 3,
        },
    ]

    result = reranker.rerank("如何关闭 SOS 报警", chunks, top_k=1)

    assert result[0]["chunk_id"] == "b"
    assert "rerank_score" in result[0]
