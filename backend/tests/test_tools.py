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
