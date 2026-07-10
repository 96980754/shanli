import pytest

from app.services.rag_service import RAGService


class FakeLLM:
    async def generate_with_tools(self, messages, tools=None, tool_choice="auto", stream=False):
        if tools and tool_choice == "auto":
            return {
                "tool_calls": [
                    {
                        "id": "call-1",
                        "name": "retrieve",
                        "arguments": {"query": "SOS 报警怎么关闭", "top_k": 5},
                    }
                ]
            }
        return {"content": "SOS 报警可以在设置中关闭。", "tool_calls": []}


class HybridFakeLLM:
    async def generate_with_tools(self, messages, tools=None, tool_choice="auto", stream=False):
        if tools and tool_choice == "auto":
            return {
                "tool_calls": [
                    {
                        "id": "call-1",
                        "name": "retrieve",
                        "arguments": {"query": "P368 model", "top_k": 5},
                    }
                ]
            }
        return {"content": "P368 model setup guide found.", "tool_calls": []}


@pytest.mark.asyncio
async def test_rag_service_uses_tool_results_for_answer_sources():
    service = RAGService(llm=FakeLLM())
    service.tools.vector_chunks_by_kb["kb-1"] = [
        {
            "chunk_id": "c1",
            "content": "SOS 报警可以在设置中的报警设置里关闭。",
            "score": 0.9,
            "doc_title": "P368用户手册",
        }
    ]

    result = await service.ask("SOS 报警怎么关闭", kb_id="kb-1")

    assert result["answer"] == "SOS 报警可以在设置中关闭。"
    assert result["sources"][0]["chunk_id"] == "c1"


@pytest.mark.asyncio
async def test_rag_service_merges_retrieve_and_bm25_sources():
    service = RAGService(llm=HybridFakeLLM())
    service.tools.vector_chunks_by_kb["kb-1"] = [
        {
            "chunk_id": "semantic-1",
            "content": "P368 setup guide overview.",
            "score": 0.7,
            "doc_title": "SemanticManual",
        }
    ]
    service.tools.build_bm25_index(
        "kb-1",
        [
            {
                "chunk_id": "bm25-1",
                "content": "P368 unique model setup guide.",
                "score": 0.0,
                "doc_title": "KeywordManual",
            }
        ],
    )

    result = await service.ask("P368 model", kb_id="kb-1")

    source_ids = [source["chunk_id"] for source in result["sources"]]
    assert "semantic-1" in source_ids
    assert "bm25-1" in source_ids
