from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from yuxi.agents.buildin.chatbot.prompt import IDENTITY_REPLY, KNOWLEDGE_REFUSAL_REPLY, SYSTEM_ERROR_REPLY
from yuxi.config.app import config as runtime_config
from yuxi.services.knowledge_answer_disposition import (
    apply_knowledge_disposition,
    apply_refusal_judgment,
    build_judge_system_prompt,
    build_knowledge_evidence,
    classify_domain_by_keywords,
    classify_knowledge_disposition,
    collect_turn_tool_names,
    is_handoff_disposition,
    judge_refusal,
    no_evidence_disposition,
    should_revoke_no_evidence,
)


def _evidence(queries, kb_scope=("kb_a",)):
    return {"schema_version": 1, "kb_scope": list(kb_scope), "queries": queries}


def _query(status="insufficient", reason="no_results"):
    return {"kb_id": "kb_a", "status": status, "reason": reason, "result_count": 0}


# ---- build_knowledge_evidence ----


def _query_kbs_output(status="ok", kb_id="kb_a", results=None):
    return {
        "schema_version": 1,
        "status": status,
        "kb_id": kb_id,
        "reason": None,
        "results": results if results is not None else [{"content": "片段"}],
    }


def test_build_knowledge_evidence_recognizes_query_kb_and_query_kbs():
    # 修复前仅认 query_kb（单库），query_kbs（跨库）检索消息被跳过导致证据丢弃。
    messages = [
        {"type": "human", "content": "调度台怎么开通？"},
        {"type": "tool", "name": "ocr_parse_file", "content": "{}"},
        {"type": "tool", "name": "query_kb", "content": json.dumps(_query_kbs_output(kb_id="kb_a"))},
        {"type": "tool", "name": "query_kbs", "content": json.dumps(_query_kbs_output(kb_id="kb_b"))},
    ]
    question, evidence = build_knowledge_evidence(messages)
    assert question == "调度台怎么开通？"
    assert evidence is not None
    assert evidence["kb_scope"] == ["kb_a", "kb_b"]
    assert {query["kb_id"] for query in evidence["queries"]} == {"kb_a", "kb_b"}


def test_query_kbs_evidence_prevents_no_kb_mislabel():
    # 跨库检索空结果的拒答，修复前会被误判 no_enabled_knowledge_base（无证据分支）。
    messages = [
        {"type": "human", "content": "查询运维平台操作日志"},
        {
            "type": "tool",
            "name": "query_kbs",
            "content": json.dumps(_query_kbs_output(status="insufficient", results=[])),
        },
        {"type": "ai", "content": KNOWLEDGE_REFUSAL_REPLY},
    ]
    _, evidence = build_knowledge_evidence(messages)
    assert evidence is not None
    disposition = classify_knowledge_disposition(KNOWLEDGE_REFUSAL_REPLY, evidence)
    assert (disposition["type"], disposition["reason"]) == ("knowledge_refusal", "no_results")
    assert disposition["judgment_required"] is True


def test_build_knowledge_evidence_none_without_query_tool():
    messages = [{"type": "human", "content": "随便聊聊"}, {"type": "ai", "content": "你好"}]
    question, evidence = build_knowledge_evidence(messages)
    assert question == "随便聊聊"
    assert evidence is None


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
    disposition = classify_knowledge_disposition(KNOWLEDGE_REFUSAL_REPLY, _evidence([_query(reason="empty_content")]))
    assert (disposition["type"], disposition["reason"]) == ("knowledge_refusal", "empty_content")
    assert disposition["judgment_required"] is True


def test_insufficient_evidence_when_ok_results_exist():
    disposition = classify_knowledge_disposition(KNOWLEDGE_REFUSAL_REPLY, _evidence([_query(status="ok")]))
    assert (disposition["type"], disposition["reason"]) == ("knowledge_refusal", "insufficient_evidence")
    assert disposition["judgment_required"] is True


def test_classification_mismatch_when_all_queries_error():
    disposition = classify_knowledge_disposition(KNOWLEDGE_REFUSAL_REPLY, _evidence([_query(status="error")]))
    assert (disposition["type"], disposition["reason"]) == ("system_error", "classification_mismatch")


# ---- apply_refusal_judgment ----


def test_judgment_policy_overrides_type_and_reason():
    disposition = classify_knowledge_disposition(KNOWLEDGE_REFUSAL_REPLY, None)
    result = apply_refusal_judgment(disposition, {"type": "policy_refusal", "reason": "jailbreak", "domain": "unknown"})
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


async def test_judge_reads_content_from_real_adapter(monkeypatch: pytest.MonkeyPatch):
    # 回归：真实 adapter.call 返回带 .content 的响应对象，修复前直接 str() 取不到模型文本、
    # 恒返回 None（REFUSAL_JUDGE_MODEL 启用后此路径才被执行到）。
    class FakeAdapter:
        async def call(self, messages):
            return SimpleNamespace(content='{"type":"scope_refusal","reason":"off_topic","domain":"unknown"}')

    monkeypatch.setattr("yuxi.services.knowledge_answer_disposition.REFUSAL_JUDGE_MODEL", "deepseek:test")
    monkeypatch.setattr("yuxi.services.knowledge_answer_disposition.select_model", lambda spec: FakeAdapter())

    result = await judge_refusal("介绍一下linux的epoll")
    assert result == {"type": "scope_refusal", "reason": "off_topic", "domain": "unknown"}


# ---- apply_knowledge_disposition ----


def test_apply_knowledge_disposition_attaches_question_and_evidence():
    message = {"type": "ai", "content": KNOWLEDGE_REFUSAL_REPLY}
    enriched = apply_knowledge_disposition(message, question="如何开通调度台？", evidence=_evidence([_query()]))
    assert enriched["knowledge_question"] == "如何开通调度台？"
    assert enriched["knowledge_evidence"]["schema_version"] == 1
    assert enriched["knowledge_disposition"]["type"] == "knowledge_refusal"
    assert enriched["knowledge_disposition"]["reason"] == "no_results"


# ---- collect_turn_tool_names / ② 兜底判定 ----


def test_collect_turn_tool_names_resets_at_latest_human():
    messages = [
        {"type": "human", "content": "上一个问题"},
        {"type": "tool", "name": "query_kb", "content": "{}"},
        {"type": "ai", "content": "上一轮回答"},
        {"type": "human", "content": "那这个参数呢"},
        {"type": "tool", "name": "search_web", "content": "{}"},
        {"type": "tool", "name": "query_kb", "content": "{}"},
    ]
    assert collect_turn_tool_names(messages) == {"search_web", "query_kb"}


def test_is_handoff_disposition_rules():
    assert is_handoff_disposition({"type": "knowledge_refusal", "reason": "no_results"}) is True
    assert is_handoff_disposition({"type": "scope_refusal", "reason": "off_topic"}) is False  # 决策①：跑题不转人工
    assert is_handoff_disposition({"type": "policy_refusal", "reason": "jailbreak"}) is False
    assert is_handoff_disposition(None) is False


def test_no_evidence_exempt_identity_ack_and_empty():
    # 零检索轮在「本轮是否检索过」门前即豁免（不依赖身份/致谢文案匹配）。
    assert should_revoke_no_evidence(IDENTITY_REPLY, None, set()) is False
    assert should_revoke_no_evidence("您好！" + IDENTITY_REPLY, None, set()) is False
    assert should_revoke_no_evidence("你好，" + IDENTITY_REPLY, None, set()) is False
    assert should_revoke_no_evidence("谢谢！", None, set()) is False
    assert should_revoke_no_evidence("   ", None, set()) is False
    # 次层防护：即便本轮检索过，身份固定答复/寒暄致谢正文仍豁免。
    insufficient = _evidence([_query(status="insufficient")])
    assert should_revoke_no_evidence("您好！" + IDENTITY_REPLY, insufficient, {"query_kb"}) is False
    assert should_revoke_no_evidence("谢谢！", insufficient, {"query_kb"}) is False


def test_no_evidence_revokes_query_attempt_with_no_usable_result():
    # epoll 场景：业务内问题本轮检索过但无可用结果、仍凭通用知识硬答 → 改写。
    assert (
        should_revoke_no_evidence(
            "Linux 的 epoll 是 Linux 下的 IO 事件通知机制……",
            _evidence([_query(status="insufficient")]),
            {"query_kb"},
        )
        is True
    )


def test_no_evidence_exempts_zero_query_turn():
    # 零检索轮一律不改写：问候/致谢/闲聊、及业务内“该查未查”的硬答都不凭回答文案反推。
    assert should_revoke_no_evidence("Linux 的 epoll 是……（通用知识硬答）", None, set()) is False
    assert should_revoke_no_evidence("你好，很高兴为您服务。", None, set()) is False
    assert should_revoke_no_evidence("谢谢你的帮助！很高兴能帮到你。", None, set()) is False
    assert should_revoke_no_evidence("You're welcome.", None, set()) is False


def test_no_evidence_exempt_when_ok_kb_evidence():
    assert should_revoke_no_evidence("调度台支持 CAT1 接入。", _evidence([_query(status="ok")]), {"query_kb"}) is False


def test_no_evidence_exempt_when_legit_non_kb_tool_used():
    # 文件/图片/文档/联网等合法非 KB 来源豁免零依据改写（即便本轮也检索过）。
    assert should_revoke_no_evidence("根据该文档第 2 页……", None, {"query_kb", "read_file"}) is False
    assert should_revoke_no_evidence("识别为 F10 产品。", None, {"ocr_parse_file", "query_kb"}) is False


def test_no_evidence_exempt_on_continuation_after_evidence():
    # 紧邻带 ok 证据回答的续答轮，即便本轮检索无果也豁免。
    assert (
        should_revoke_no_evidence(
            "那这个参数呢",
            _evidence([_query(status="insufficient")]),
            {"query_kb"},
            continuation_with_evidence=True,
        )
        is False
    )


def test_no_evidence_disposition_rewrites_answered_after_query_attempt_without_result():
    evidence = _evidence([_query(status="insufficient")])
    message = {"type": "ai", "content": "这是模型在检索无果后仍作答的一段话。"}
    enriched = apply_knowledge_disposition(message, question="介绍一下linux的epoll", evidence=evidence)
    disposition = no_evidence_disposition(enriched, evidence=evidence, tool_names={"query_kb"})
    assert disposition is not None
    assert disposition["type"] == "knowledge_refusal"
    assert disposition["reason"] == "no_evidence_output"
    assert disposition["judgment_required"] is True


def test_no_evidence_disposition_leaves_zero_query_answer_alone():
    message = {"type": "ai", "content": "这是模型凭通用知识硬答的一段话，没有任何检索。"}
    enriched = apply_knowledge_disposition(message, question="介绍一下linux的epoll", evidence=None)
    assert no_evidence_disposition(enriched, evidence=None, tool_names=set()) is None


def test_no_evidence_disposition_ignores_refusal_or_grounded():
    refusal = apply_knowledge_disposition(
        {"type": "ai", "content": KNOWLEDGE_REFUSAL_REPLY}, question="q", evidence=None
    )
    assert no_evidence_disposition(refusal, evidence=None, tool_names=set()) is None
    ok_evidence = _evidence([_query(status="ok")])
    grounded = apply_knowledge_disposition(
        {"type": "ai", "content": "库内依据的回答。"},
        question="q",
        evidence=ok_evidence,
    )
    assert no_evidence_disposition(grounded, evidence=ok_evidence, tool_names={"query_kb"}) is None


def test_keyword_domain_prefers_longest_match_and_config_order():
    from yuxi.config.app import BusinessLine

    lines = [
        BusinessLine(code="mno", name="网优", keywords=["安卓"]),
        BusinessLine(code="terminal", name="终端", keywords=["安卓终端"]),
    ]
    assert classify_domain_by_keywords("安卓终端配置", lines) == "terminal"
    assert classify_domain_by_keywords("安卓问题", lines) == "mno"
    assert classify_domain_by_keywords("天气", lines) == "unknown"


def test_build_judge_system_prompt_custom_lines_include_unknown_tail():
    from yuxi.config.app import BusinessLine

    prompt = build_judge_system_prompt(lines=[BusinessLine(code="diaodutai", name="调度台")])
    assert "业务域取值：diaodutai（调度台）、unknown" in prompt


def test_build_judge_system_prompt_empty_lines_only_unknown():
    prompt = build_judge_system_prompt(lines=[])
    assert "业务域取值：unknown" in prompt


def test_build_judge_system_prompt_preserves_json_example_braces():
    """@DOMAIN_CHOICES@ 占位符替换不得破坏模板内示例 JSON 的 {}。"""
    prompt = build_judge_system_prompt(lines=[])
    assert '{"type": "knowledge_refusal"' in prompt
    assert "@DOMAIN_CHOICES@" not in prompt


def test_apply_refusal_judgment_keeps_listed_domain(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        runtime_config,
        "business_lines",
        [{"code": "diaodutai", "name": "调度台", "keywords": []}, {"code": "terminal", "name": "终端", "keywords": []}],
    )
    disposition = classify_knowledge_disposition(KNOWLEDGE_REFUSAL_REPLY, None)
    result = apply_refusal_judgment(
        disposition, {"type": "knowledge_refusal", "reason": "no_enabled_knowledge_base", "domain": "terminal"}
    )
    assert result["type"] == "knowledge_refusal"
    assert result["domain"] == "terminal"


def test_apply_refusal_judgment_falls_back_unknown_for_unlisted_domain(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(runtime_config, "business_lines", [{"code": "diaodutai", "name": "调度台", "keywords": []}])
    disposition = classify_knowledge_disposition(KNOWLEDGE_REFUSAL_REPLY, None)
    result = apply_refusal_judgment(
        disposition, {"type": "policy_refusal", "reason": "privacy", "domain": "不存在的业务线"}
    )
    assert result["type"] == "policy_refusal"
    assert result["reason"] == "privacy"
    assert result["domain"] == "unknown"


def test_apply_refusal_judgment_missing_domain_sets_unknown(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(runtime_config, "business_lines", [{"code": "diaodutai", "name": "调度台", "keywords": []}])
    disposition = classify_knowledge_disposition(KNOWLEDGE_REFUSAL_REPLY, None)
    result = apply_refusal_judgment(disposition, {"type": "scope_refusal", "reason": "ambiguous"})
    assert result["domain"] == "unknown"
