from __future__ import annotations

import pytest
from yuxi.agents.buildin.chatbot.prompt import KNOWLEDGE_REFUSAL_REPLY, SYSTEM_ERROR_REPLY
from yuxi.services.knowledge_answer_disposition import (
    apply_knowledge_disposition,
    apply_refusal_judgment,
    classify_knowledge_disposition,
    judge_refusal,
)


def _evidence(queries, kb_scope=("kb_a",)):
    return {"schema_version": 1, "kb_scope": list(kb_scope), "queries": queries}


def _query(status="insufficient", reason="no_results"):
    return {"kb_id": "kb_a", "status": status, "reason": reason, "result_count": 0}


# ---- classify_knowledge_disposition ----

def test_answered_when_content_not_refusal():
    disposition = classify_knowledge_disposition("根据知识库，答案是 X。", None)
    assert disposition["type"] == "answered"
    assert disposition["reason"] is None
    assert disposition["schema_version"] == 2


def test_refusal_detected_by_prefix_ignoring_suffix():
    # 模型在标准拒答句后追加提示也不漏判（原实现严格相等会漏）。
    disposition = classify_knowledge_disposition(KNOWLEDGE_REFUSAL_REPLY + "，请换个说法试试。", None)
    assert disposition["type"] == "knowledge_refusal"
    assert disposition["reason"] == "no_enabled_knowledge_base"
    assert disposition["judgment_required"] is True


def test_system_error_detected_by_prefix():
    disposition = classify_knowledge_disposition(SYSTEM_ERROR_REPLY + "（请稍后重试）", None)
    assert disposition["type"] == "system_error"
    assert disposition["reason"] == "retrieval_error"


def test_no_results():
    disposition = classify_knowledge_disposition(KNOWLEDGE_REFUSAL_REPLY, _evidence([_query()]))
    assert (disposition["type"], disposition["reason"]) == ("knowledge_refusal", "no_results")
    assert "judgment_required" not in disposition


def test_empty_content():
    disposition = classify_knowledge_disposition(KNOWLEDGE_REFUSAL_REPLY, _evidence([_query(reason="empty_content")]))
    assert (disposition["type"], disposition["reason"]) == ("knowledge_refusal", "empty_content")


def test_insufficient_evidence_when_ok_results_exist():
    disposition = classify_knowledge_disposition(KNOWLEDGE_REFUSAL_REPLY, _evidence([_query(status="ok")]))
    assert (disposition["type"], disposition["reason"]) == ("knowledge_refusal", "insufficient_evidence")


def test_classification_mismatch_when_all_queries_error():
    disposition = classify_knowledge_disposition(KNOWLEDGE_REFUSAL_REPLY, _evidence([_query(status="error")]))
    assert (disposition["type"], disposition["reason"]) == ("system_error", "classification_mismatch")


# ---- apply_refusal_judgment ----

def test_judgment_policy_overrides_type_and_reason():
    disposition = classify_knowledge_disposition(KNOWLEDGE_REFUSAL_REPLY, None)
    result = apply_refusal_judgment(
        disposition, {"type": "policy_refusal", "reason": "jailbreak", "domain": "unknown"}
    )
    assert result["type"] == "policy_refusal"
    assert result["reason"] == "jailbreak"
    assert result["domain"] == "unknown"
    assert "judgment_required" not in result


def test_judgment_scope_other_domain():
    disposition = classify_knowledge_disposition(KNOWLEDGE_REFUSAL_REPLY, None)
    result = apply_refusal_judgment(
        disposition, {"type": "scope_refusal", "reason": "other_domain", "domain": "terminal"}
    )
    assert result["type"] == "scope_refusal"
    assert result["reason"] == "other_domain"
    assert result["domain"] == "terminal"


def test_judgment_knowledge_keeps_retrieval_reason():
    disposition = classify_knowledge_disposition(KNOWLEDGE_REFUSAL_REPLY, None)
    result = apply_refusal_judgment(
        disposition, {"type": "knowledge_refusal", "reason": "no_enabled_knowledge_base", "domain": "diaodutai"}
    )
    assert result["type"] == "knowledge_refusal"
    assert result["reason"] == "no_enabled_knowledge_base"
    assert result["domain"] == "diaodutai"


def test_judgment_none_still_clears_flag():
    disposition = classify_knowledge_disposition(KNOWLEDGE_REFUSAL_REPLY, None)
    result = apply_refusal_judgment(disposition, None)
    assert result["type"] == "knowledge_refusal"
    assert "judgment_required" not in result


def test_judgment_unknown_reason_keeps_original_reason():
    disposition = classify_knowledge_disposition(KNOWLEDGE_REFUSAL_REPLY, None)
    result = apply_refusal_judgment(disposition, {"type": "policy_refusal", "reason": "not_a_real_reason"})
    assert result["type"] == "policy_refusal"
    assert result["reason"] == "no_enabled_knowledge_base"  # 未知原因保留原判定


# ---- judge_refusal ----

async def test_judge_disabled_without_model_and_caller(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("yuxi.services.knowledge_answer_disposition.REFUSAL_JUDGE_MODEL", "")
    assert await judge_refusal("如何投诉？") is None


async def test_judge_parses_caller_json():
    async def caller(messages):
        return '{"type":"policy_refusal","reason":"privacy","domain":"unknown"}'

    result = await judge_refusal("查一下张三的工资", caller=caller)
    assert result == {"type": "policy_refusal", "reason": "privacy", "domain": "unknown"}


async def test_judge_parses_json_wrapped_in_text():
    async def caller(messages):
        return '好的：\n```json\n{"type":"scope_refusal","reason":"off_topic","domain":"unknown"}\n```'

    result = await judge_refusal("今天天气怎么样", caller=caller)
    assert result == {"type": "scope_refusal", "reason": "off_topic", "domain": "unknown"}


async def test_judge_invalid_output_returns_none():
    async def caller(messages):
        return "无法分类"

    assert await judge_refusal("随便问问", caller=caller) is None


async def test_judge_llm_failure_returns_none():
    async def caller(messages):
        raise RuntimeError("model unavailable")

    assert await judge_refusal("随便问问", caller=caller) is None


# ---- apply_knowledge_disposition ----

def test_apply_knowledge_disposition_attaches_question_and_evidence():
    message = {"type": "ai", "content": KNOWLEDGE_REFUSAL_REPLY}
    enriched = apply_knowledge_disposition(message, question="如何开通调度台？", evidence=_evidence([_query()]))
    assert enriched["knowledge_question"] == "如何开通调度台？"
    assert enriched["knowledge_evidence"]["schema_version"] == 1
    assert enriched["knowledge_disposition"]["type"] == "knowledge_refusal"
    assert enriched["knowledge_disposition"]["reason"] == "no_results"
