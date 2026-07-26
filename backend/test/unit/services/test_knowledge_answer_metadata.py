import json

from yuxi.services.knowledge_answer_metadata import (
    apply_knowledge_evidence,
    extract_query_kb_evidence,
    is_final_assistant_message,
    merge_knowledge_evidence,
    message_text,
)


def _query_tool_message(
    *,
    status: str,
    reason: str | None = None,
    score: float | None = None,
    citations: list[dict] | None = None,
) -> dict:
    return {
        "type": "tool",
        "name": "query_kb",
        "tool_call_id": "tool-call-1",
        "content": json.dumps(
            {
                "kb_id": "kb-1",
                "status": status,
                "reason": reason,
                "top_score": score,
                "score_type": "score",
                "results": [],
                "citations": citations or [],
            },
            ensure_ascii=False,
        ),
    }


def test_message_text_reads_string_and_text_blocks():
    assert message_text("问题") == "问题"
    assert message_text([{"type": "text", "text": "第一段"}, {"text": "第二段"}]) == "第一段\n第二段"


def test_extract_sufficient_query_evidence():
    tool_message = _query_tool_message(
        status="sufficient",
        score=0.91,
        citations=[
            {
                "citation_id": "c1",
                "kb_id": "kb-1",
                "file_id": "file-1",
                "chunk_id": "chunk-1",
                "file_name": "test.docx",
                "quote": "最大并发用户数为 137 人。",
                "score": 0.91,
            }
        ],
    )

    evidence = extract_query_kb_evidence(tool_message)

    assert evidence is not None
    assert evidence["answer_status"] == "answered"
    assert evidence["reason"] is None
    assert evidence["top_score"] == 0.91
    assert evidence["kb_ids"] == ["kb-1"]
    assert len(evidence["citations"]) == 1
    assert evidence["citations"][0]["chunk_id"] == "chunk-1"


def test_extract_insufficient_query_evidence():
    evidence = extract_query_kb_evidence(
        _query_tool_message(
            status="insufficient",
            reason="low_relevance",
            score=0.5656,
        )
    )

    assert evidence is not None
    assert evidence["answer_status"] == "refused"
    assert evidence["reason"] == "low_relevance"
    assert evidence["citations"] == []


def test_non_query_tool_is_ignored():
    assert (
        extract_query_kb_evidence(
            {
                "type": "tool",
                "name": "list_kbs",
                "content": "[]",
            }
        )
        is None
    )


def test_merge_evidence_deduplicates_citations():
    first = extract_query_kb_evidence(
        _query_tool_message(
            status="sufficient",
            score=0.80,
            citations=[
                {
                    "citation_id": "c1",
                    "kb_id": "kb-1",
                    "file_id": "file-1",
                    "chunk_id": "chunk-1",
                    "quote": "A",
                }
            ],
        )
    )
    second = extract_query_kb_evidence(
        _query_tool_message(
            status="sufficient",
            score=0.90,
            citations=[
                {
                    "citation_id": "c2",
                    "kb_id": "kb-1",
                    "file_id": "file-1",
                    "chunk_id": "chunk-1",
                    "quote": "A",
                }
            ],
        )
    )

    merged = merge_knowledge_evidence(first, second)

    assert merged["answer_status"] == "answered"
    assert merged["top_score"] == 0.90
    assert len(merged["citations"]) == 1
    assert len(merged["searches"]) == 2


def test_final_assistant_message_must_not_have_tool_calls():
    assert is_final_assistant_message(
        {
            "type": "ai",
            "content": "最终回答",
            "tool_calls": [],
        }
    )

    assert not is_final_assistant_message(
        {
            "type": "ai",
            "content": "我来查询",
            "tool_calls": [{"name": "query_kb"}],
        }
    )


def test_apply_knowledge_evidence_adds_persistent_metadata():
    evidence = {
        "answer_status": "refused",
        "reason": "low_relevance",
        "citations": [],
        "kb_ids": ["kb-1"],
        "searches": [],
    }

    enriched = apply_knowledge_evidence(
        {
            "type": "ai",
            "content": "抱歉，在现有知识库中未找到相关依据，已通知管理员补充。",
        },
        evidence,
        question="TEST-C100 的电池容量是多少？",
    )

    assert enriched["answer_status"] == "refused"
    assert enriched["refusal_reason"] == "low_relevance"
    assert enriched["citations"] == []
    assert enriched["knowledge_evidence"] == evidence
    assert enriched["knowledge_question"] == "TEST-C100 的电池容量是多少？"