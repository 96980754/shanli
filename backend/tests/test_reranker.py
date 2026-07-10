from app.services.reranker import RuleReranker


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
