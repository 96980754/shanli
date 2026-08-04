from __future__ import annotations

import json

from yuxi.agents.buildin.chatbot.prompt import KNOWLEDGE_REFUSAL_REPLY, SYSTEM_ERROR_REPLY
from yuxi.services.knowledge_answer_disposition import (
    apply_knowledge_disposition,
    build_knowledge_evidence,
    classify_knowledge_disposition,
    is_final_assistant_message,
    parse_query_kb_output,
)


def test_parse_query_kb_output_requires_structured_protocol():
    payload = {
        "schema_version": 1,
        "status": "insufficient",
        "reason": "no_results",
        "kb_id": "kb-1",
        "results": [],
        "error": None,
    }

    assert parse_query_kb_output(json.dumps(payload)) == {
        "kb_id": "kb-1",
        "status": "insufficient",
        "reason": "no_results",
        "result_count": 0,
    }
    assert parse_query_kb_output("{'status': 'insufficient'}") is None


def test_build_knowledge_evidence_uses_current_human_turn_only():
    messages = [
        {"type": "human", "content": "旧问题"},
        {
            "type": "tool",
            "name": "query_kb",
            "content": json.dumps(
                {"schema_version": 1, "status": "ok", "reason": None, "kb_id": "old", "results": [{}]}
            ),
        },
        {"type": "human", "content": "当前问题"},
        {
            "type": "tool",
            "name": "query_kb",
            "content": json.dumps(
                {
                    "schema_version": 1,
                    "status": "insufficient",
                    "reason": "no_results",
                    "kb_id": "kb-2",
                    "results": [],
                }
            ),
        },
    ]

    question, evidence = build_knowledge_evidence(messages)

    assert question == "当前问题"
    assert evidence["kb_scope"] == ["kb-2"]
    assert len(evidence["queries"]) == 1


def test_classify_knowledge_refusal_reasons():
    no_results = {
        "schema_version": 1,
        "kb_scope": ["kb-1"],
        "queries": [{"kb_id": "kb-1", "status": "insufficient", "reason": "no_results", "result_count": 0}],
    }
    candidates = {
        "schema_version": 1,
        "kb_scope": ["kb-1"],
        "queries": [{"kb_id": "kb-1", "status": "ok", "reason": None, "result_count": 2}],
    }

    assert classify_knowledge_disposition(KNOWLEDGE_REFUSAL_REPLY, no_results)["reason"] == "no_results"
    assert classify_knowledge_disposition(KNOWLEDGE_REFUSAL_REPLY, candidates)["reason"] == "insufficient_evidence"
    assert classify_knowledge_disposition(KNOWLEDGE_REFUSAL_REPLY, None)["reason"] == "no_enabled_knowledge_base"
    assert classify_knowledge_disposition(SYSTEM_ERROR_REPLY, no_results)["type"] == "system_error"


def test_only_exact_fixed_reply_is_classified_as_refusal():
    evidence = {
        "schema_version": 1,
        "kb_scope": ["kb-1"],
        "queries": [{"kb_id": "kb-1", "status": "insufficient", "reason": "no_results", "result_count": 0}],
    }

    assert classify_knowledge_disposition(f"{KNOWLEDGE_REFUSAL_REPLY} 建议联系管理员。", evidence)["type"] == "answered"


def test_final_assistant_predicate_and_metadata_projection():
    assert is_final_assistant_message({"content": "answer", "tool_calls": []}) is True
    assert is_final_assistant_message({"content": "", "tool_calls": [{"name": "query_kb"}]}) is False

    message = apply_knowledge_disposition(
        {"content": KNOWLEDGE_REFUSAL_REPLY, "tool_calls": []},
        question="未覆盖的问题",
        evidence={
            "schema_version": 1,
            "kb_scope": ["kb-1"],
            "queries": [
                {"kb_id": "kb-1", "status": "insufficient", "reason": "no_results", "result_count": 0}
            ],
        },
    )

    assert message["knowledge_question"] == "未覆盖的问题"
    assert message["knowledge_disposition"] == {
        "schema_version": 1,
        "type": "knowledge_refusal",
        "reason": "no_results",
    }
