from __future__ import annotations

import json
import os
from typing import Any

from yuxi.agents.buildin.chatbot.prompt import (
    IDENTITY_REPLY,
    KNOWLEDGE_REFUSAL_REPLY,
    SYSTEM_ERROR_REPLY,
)
from yuxi.config.app import BusinessLine, resolve_business_lines, sanitize_business_domain
from yuxi.models import select_model
from yuxi.utils.logging_config import logger

# 知识检索工具：query_kb（单库）/ query_kbs（跨库）输出结构一致，证据解析需同时识别，
# 否则跨库检索的拒答会因证据被丢弃而误判 no_enabled_knowledge_base。
QUERY_KB_TOOL_NAMES = frozenset({"query_kb", "query_kbs"})

# 拒答判定 schema 版本：v2 起新增 scope_refusal / policy_refusal 类型与 domain 字段。
DISPOSITION_SCHEMA_VERSION = 2

# 可转人工的拒答类型；policy_refusal 属于「该拒绝」，不转普通客服。
# scope_refusal 仅 reason=off_topic（入口门确定性的跑题拒答）不转人工，见 is_handoff_disposition。
HANDOFF_REFUSAL_TYPES = {"knowledge_refusal", "scope_refusal"}

SCOPE_REFUSAL_REASONS = {"off_topic", "other_domain", "ambiguous"}
POLICY_REFUSAL_REASONS = {"policy_violation", "privacy", "jailbreak", "sensitive"}

# 无依据兜底改写使用的拒答 reason（决策②）：模型对业务内问题零工具硬答，
# 视为守规失败的知识缺口，转人工。
NO_EVIDENCE_OUTPUT_REASON = "no_evidence_output"

# 寒暄致谢语：紧邻的答后寒暄（谢谢/好的/嗯…）无需知识库依据，命中则豁免“无依据改写”。
_CONVERSATIONAL_ACKS = frozenset(
    {
        "谢谢",
        "好的",
        "嗯",
        "好",
        "行",
        "收到",
        "明白",
        "知道了",
        "好的谢谢",
        "谢谢你的帮助",
        "辛苦",
        "辛苦了",
        "不客气",
        "ok",
    }
)

# 拒答 judge：仅当检索无法解释拒答（无 query_kb 证据）时调用一次 LLM 分类。
# 用 REFUSAL_JUDGE_MODEL 配置判定模型；未配置时跳过（保持既有转人工行为）。
# 入口门（knowledge_scope_gate）复用同一模型做跑题确认。
REFUSAL_JUDGE_MODEL = os.getenv("REFUSAL_JUDGE_MODEL", "").strip()

JUDGE_SYSTEM_PROMPT_TEMPLATE = """\
你是企业知识库客服的拒答原因分类器。仅当系统无法回答用户问题时调用。把问题归类并只输出 JSON（不要输出任何其它文字）。

可选输出（type / reason / domain）：
- {"type": "knowledge_refusal", "reason": "no_enabled_knowledge_base", "domain": "<业务域或 unknown>"}
    用户在业务范围内提问，知识库应能覆盖但当前缺料（正常转人工补答）。
- {"type": "scope_refusal", "reason": "off_topic", "domain": "unknown"}
    问题与业务完全无关（闲聊/其它话题），直接拒答即可，不转人工。
- {"type": "policy_refusal", "reason": "policy_violation|privacy|jailbreak|sensitive", "domain": "unknown"}
    问题本身违规/涉隐私/诱导越狱/高争议，系统拒绝回答是正确的，不转普通客服，仅复核。

domain 归属业务线（agent 未预设业务域时由你判定最贴切的一条）；该标签同时决定转人工去向——
命中业务线绑定的客服团队会收到转接（未绑定的线回落到通用客服），请尽量选最贴切的业务线。
业务域取值：@DOMAIN_CHOICES@
"""


def build_judge_system_prompt(lines: list[BusinessLine] | None = None) -> str:
    """按配置业务线动态组装 judge 提示词：新增产品线后模型即可判到新线。

    lines 可注入以便测试；缺省读系统配置（设置页维护）。unknown 始终作为兜底可选项。
    """
    if lines is None:
        lines = resolve_business_lines()
    choices = "、".join(f"{line.code}（{line.name}）" for line in lines)
    domain_choices = f"{choices}、unknown" if choices else "unknown"
    return JUDGE_SYSTEM_PROMPT_TEMPLATE.replace("@DOMAIN_CHOICES@", domain_choices)


def _disposition(
    disposition_type: str,
    reason: str | None,
    *,
    domain: str | None = None,
    judgment_required: bool = False,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": DISPOSITION_SCHEMA_VERSION,
        "type": disposition_type,
        "reason": reason,
    }
    if domain is not None:
        payload["domain"] = domain
    if judgment_required:
        payload["judgment_required"] = True
    return payload


def parse_query_kb_output(content: Any) -> dict[str, Any] | None:
    if isinstance(content, dict):
        payload = content
    elif isinstance(content, str):
        try:
            payload = json.loads(content)
        except json.JSONDecodeError:
            return None
    else:
        return None

    if payload.get("schema_version") != 1 or payload.get("status") not in {"ok", "insufficient", "error"}:
        return None
    results = payload.get("results")
    if not isinstance(results, list):
        return None
    return {
        "kb_id": str(payload.get("kb_id") or ""),
        "status": payload["status"],
        "reason": payload.get("reason"),
        "result_count": len(results),
    }


def build_knowledge_evidence(messages: list[Any]) -> tuple[str, dict[str, Any] | None]:
    current_question = ""
    queries: list[dict[str, Any]] = []

    for message in messages:
        data = (
            message.model_dump()
            if hasattr(message, "model_dump")
            else dict(message)
            if isinstance(message, dict)
            else {}
        )
        message_type = data.get("type") or data.get("role")
        if message_type in {"human", "user"}:
            content = data.get("content")
            current_question = content.strip() if isinstance(content, str) else ""
            queries = []
            continue
        if message_type != "tool" or data.get("name") not in QUERY_KB_TOOL_NAMES:
            continue
        query = parse_query_kb_output(data.get("content"))
        if query is not None:
            queries.append(query)

    if not queries:
        return current_question, None

    kb_scope = sorted({query["kb_id"] for query in queries if query["kb_id"]})
    return current_question, {"schema_version": 1, "kb_scope": kb_scope, "queries": queries}


def collect_turn_tool_names(messages: list[Any]) -> set[str]:
    """收集最后一个 human 轮次内调用的工具名（与 build_knowledge_evidence 同口径，遇 human 重置）。"""
    tool_names: set[str] = set()
    for message in messages:
        data = (
            message.model_dump()
            if hasattr(message, "model_dump")
            else dict(message)
            if isinstance(message, dict)
            else {}
        )
        message_type = data.get("type") or data.get("role")
        if message_type in {"human", "user"}:
            tool_names = set()
            continue
        if message_type == "tool" and data.get("name"):
            tool_names.add(str(data["name"]))
    return tool_names


def has_kb_ok_evidence(evidence: dict[str, Any] | None) -> bool:
    return bool(evidence) and any(query.get("status") == "ok" for query in (evidence.get("queries") or []))


def is_conversational_ack(content: str) -> bool:
    normalized = str(content or "").strip().strip("，。！？!?、 ").casefold()
    return normalized in _CONVERSATIONAL_ACKS


def is_handoff_disposition(disposition: dict[str, Any] | None) -> bool:
    """可转人工的拒答判定：scope_refusal/off_topic（入口门确定性的跑题）不转人工。"""
    dtype = (disposition or {}).get("type")
    if dtype not in HANDOFF_REFUSAL_TYPES:
        return False
    if dtype == "scope_refusal" and (disposition or {}).get("reason") == "off_topic":
        return False
    return True


def turn_has_grounding_source(tool_names: set[str], evidence: dict[str, Any] | None) -> bool:
    """本轮是否有“回答依据来源”：query_kb(s) 返回 ok，或用了任何非纯检索工具。

    除 query_kb / query_kbs（是否 ok 由 evidence 反映）与 ask_user_question（澄清中断，
    不产出依据）外，任何工具调用（读文档/文件/图片/联网/行业方案/技能等）都视为
    模型从真实来源作答，豁免“零依据改写”。
    """
    if has_kb_ok_evidence(evidence):
        return True
    return bool(tool_names - QUERY_KB_TOOL_NAMES - {"ask_user_question"})


def should_revoke_no_evidence(
    content: str,
    evidence: dict[str, Any] | None,
    tool_names: set[str],
    *,
    continuation_with_evidence: bool = False,
) -> bool:
    """决策②：业务内问题的“正常作答”是否零依据，应改写为拒答。

    命中条件（全部满足）：
    - 正文是普通作答而非拒答模板（由调用方保证 type == answered）；
    - 非身份回复、非答后寒暄致谢（这些本就无需检索）；
    - 本轮无 ok 检索证据，也未用任何合法来源工具（纯文本硬答）；
    - 非“紧邻上一条带 ok 证据回答”的续答轮（续答可基于上文合法作答）。
    """
    text = str(content or "").strip()
    if not text or text == IDENTITY_REPLY or is_conversational_ack(text):
        return False
    if turn_has_grounding_source(tool_names, evidence):
        return False
    if continuation_with_evidence:
        return False
    return True


def no_evidence_disposition(
    msg_dict: dict[str, Any],
    *,
    evidence: dict[str, Any] | None,
    tool_names: set[str],
    continuation_with_evidence: bool = False,
) -> dict[str, Any] | None:
    """决策②：把「零依据硬答」的 answered 最终消息改写为知识缺口拒答；非改写对象返回 None。

    仅在已归类为 answered 的消息上生效；改写返回 knowledge_refusal/no_evidence_output
    判定（正文保留，由调用方置 knowledge_no_evidence 供前端横幅）。内容 list 先归一为正文文本，
    与 classify_knowledge_disposition 口径一致。
    """
    if (msg_dict.get("knowledge_disposition") or {}).get("type") != "answered":
        return None
    content = msg_dict.get("content")
    if isinstance(content, list):
        text = "\n".join(
            item.get("text", "") for item in content if isinstance(item, dict) and isinstance(item.get("text"), str)
        )
    else:
        text = str(content or "")
    if not should_revoke_no_evidence(text, evidence, tool_names, continuation_with_evidence=continuation_with_evidence):
        return None
    return _disposition("knowledge_refusal", NO_EVIDENCE_OUTPUT_REASON)


def is_final_assistant_message(message: dict[str, Any]) -> bool:
    content = message.get("content")
    if isinstance(content, list):
        content = "\n".join(
            item.get("text", "") for item in content if isinstance(item, dict) and isinstance(item.get("text"), str)
        )
    return (
        isinstance(content, str)
        and bool(content.strip())
        and not (message.get("tool_calls") or [])
        and not message.get("is_error")
        and not message.get("error_type")
    )


def classify_knowledge_disposition(content: str, evidence: dict[str, Any] | None) -> dict[str, Any]:
    """按最终回复文案与检索证据判定拒答归属。

    检索信号能解释的拒答直接归类（knowledge_refusal / system_error）；
    无检索证据的拒答打 judgment_required，交由 judge_refusal 进一步区分
    （知识缺口 / 跑题 / 跨域 / 策略拦截）。
    """
    normalized = content.strip()
    if normalized.startswith(SYSTEM_ERROR_REPLY):
        return _disposition("system_error", "retrieval_error")
    if not normalized.startswith(KNOWLEDGE_REFUSAL_REPLY):
        return _disposition("answered", None)
    if evidence is None:
        return _disposition("knowledge_refusal", "no_enabled_knowledge_base", judgment_required=True)

    queries = evidence["queries"]
    if queries and all(query["status"] == "error" for query in queries):
        return _disposition("system_error", "classification_mismatch")
    if any(query["status"] == "ok" for query in queries):
        return _disposition("knowledge_refusal", "insufficient_evidence")
    if any(query["reason"] == "empty_content" for query in queries):
        return _disposition("knowledge_refusal", "empty_content")
    return _disposition("knowledge_refusal", "no_results")


def apply_refusal_judgment(disposition: dict[str, Any], judgment: dict[str, Any] | None) -> dict[str, Any]:
    """合并 LLM judge 结果：仅当判定为 scope/policy 时改写类型与原因，知识缺口保持原检索原因。"""
    disposition.pop("judgment_required", None)
    if not judgment:
        return disposition
    jtype = judgment.get("type")
    reason = judgment.get("reason")
    if jtype == "policy_refusal":
        disposition["type"] = "policy_refusal"
        if reason in POLICY_REFUSAL_REASONS:
            disposition["reason"] = reason
    elif jtype == "scope_refusal":
        disposition["type"] = "scope_refusal"
        if reason in SCOPE_REFUSAL_REASONS:
            disposition["reason"] = reason
    # 域只认配置清单内的 code；模型给出游离值/空值时回退 unknown（保留兜底）。
    disposition["domain"] = sanitize_business_domain(judgment.get("domain"))
    return disposition


def _parse_judge_payload(text: str) -> dict[str, Any] | None:
    content = text.strip()
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        start, end = content.find("{"), content.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            payload = json.loads(content[start : end + 1])
        except json.JSONDecodeError:
            return None
    if not isinstance(payload, dict) or payload.get("type") not in {
        "knowledge_refusal",
        "scope_refusal",
        "policy_refusal",
    }:
        return None
    return payload


async def judge_refusal(question: str, *, caller=None) -> dict[str, Any] | None:
    """对无检索证据的拒答做一次 LLM 分类，返回 {type, reason, domain}。

    未配置 REFUSAL_JUDGE_MODEL 或调用/解析失败时返回 None（调用方沿用原判定）。
    caller 可注入以便测试：async (messages: list[dict]) -> str。
    """
    if not REFUSAL_JUDGE_MODEL and caller is None:
        return None
    messages = [
        {"role": "system", "content": build_judge_system_prompt()},
        {"role": "user", "content": f"用户问题：{question.strip() or '（空）'}\n\n只输出 JSON。"},
    ]
    try:
        if caller is not None:
            text = await caller(messages)
        else:
            adapter = select_model(REFUSAL_JUDGE_MODEL)
            # adapter.call 返回带 .content 的响应对象，直接 str() 只会得到对象 repr；取 .content 才是模型文本。
            text = (await adapter.call(messages)).content
        return _parse_judge_payload(str(text or ""))
    except Exception as exc:  # noqa: BLE001 — judge 失败不应影响消息保存
        logger.warning("拒答 judge 调用失败，沿用原判定: {}", exc)
        return None


def apply_knowledge_disposition(
    message: dict[str, Any],
    *,
    question: str,
    evidence: dict[str, Any] | None,
) -> dict[str, Any]:
    if not is_final_assistant_message(message):
        return message

    enriched = dict(message)
    content = enriched.get("content")
    if isinstance(content, list):
        content = "\n".join(
            item.get("text", "") for item in content if isinstance(item, dict) and isinstance(item.get("text"), str)
        )
    enriched["knowledge_question"] = question
    if evidence is not None:
        enriched["knowledge_evidence"] = evidence
    enriched["knowledge_disposition"] = classify_knowledge_disposition(str(content or ""), evidence)
    return enriched


__all__ = [
    "apply_knowledge_disposition",
    "apply_refusal_judgment",
    "build_knowledge_evidence",
    "classify_knowledge_disposition",
    "collect_turn_tool_names",
    "has_kb_ok_evidence",
    "is_conversational_ack",
    "is_final_assistant_message",
    "is_handoff_disposition",
    "judge_refusal",
    "no_evidence_disposition",
    "parse_query_kb_output",
    "should_revoke_no_evidence",
    "turn_has_grounding_source",
]
