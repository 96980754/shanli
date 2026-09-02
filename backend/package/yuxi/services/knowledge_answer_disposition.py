from __future__ import annotations

import json
import os
from typing import Any

from yuxi.agents.buildin.chatbot.prompt import KNOWLEDGE_REFUSAL_REPLY, SYSTEM_ERROR_REPLY
from yuxi.models import select_model
from yuxi.utils.logging_config import logger

QUERY_TOOL_NAME = "query_kb"

# 拒答判定 schema 版本：v2 起新增 scope_refusal / policy_refusal 类型与 domain 字段。
DISPOSITION_SCHEMA_VERSION = 2

# 可转人工的拒答类型；policy_refusal 属于「该拒绝」，不转普通客服。
HANDOFF_REFUSAL_TYPES = {"knowledge_refusal", "scope_refusal"}

SCOPE_REFUSAL_REASONS = {"off_topic", "other_domain", "ambiguous"}
POLICY_REFUSAL_REASONS = {"policy_violation", "privacy", "jailbreak", "sensitive"}

# 拒答 judge：仅当检索无法解释拒答（无 query_kb 证据）时调用一次 LLM 分类。
# 用 REFUSAL_JUDGE_MODEL 配置判定模型；未配置时跳过（保持既有转人工行为）。
REFUSAL_JUDGE_MODEL = os.getenv("REFUSAL_JUDGE_MODEL", "").strip()

JUDGE_SYSTEM_PROMPT = """\
你是企业知识库客服的拒答原因分类器。仅当系统无法回答用户问题时调用。把问题归类并只输出 JSON（不要输出任何其它文字）。

可选输出（type / reason / domain）：
- {"type": "knowledge_refusal", "reason": "no_enabled_knowledge_base", "domain": "<业务域或 unknown>"}
    用户在业务范围内提问，知识库应能覆盖但当前缺料（正常转人工补答）。
- {"type": "scope_refusal", "reason": "off_topic", "domain": "unknown"}
    问题与业务完全无关（闲聊/其它话题），不需要转人工。
- {"type": "scope_refusal", "reason": "other_domain", "domain": "<目标业务域>"}
    问题属于其它业务线，应转给对应线。
- {"type": "scope_refusal", "reason": "ambiguous", "domain": "<业务域或 unknown>"}
    问题含糊/多义/需要人工判断，应优先转人工。
- {"type": "policy_refusal", "reason": "policy_violation|privacy|jailbreak|sensitive", "domain": "unknown"}
    问题本身违规/涉隐私/诱导越狱/高争议，系统拒绝回答是正确的，不转普通客服，仅复核。

业务域取值：diaodutai（调度台）、terminal（终端）、ops（运营平台）、kefu（通用客服）、unknown。
"""


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
        if message_type != "tool" or data.get("name") != QUERY_TOOL_NAME:
            continue
        query = parse_query_kb_output(data.get("content"))
        if query is not None:
            queries.append(query)

    if not queries:
        return current_question, None

    kb_scope = sorted({query["kb_id"] for query in queries if query["kb_id"]})
    return current_question, {"schema_version": 1, "kb_scope": kb_scope, "queries": queries}


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
    domain = judgment.get("domain")
    if domain:
        disposition["domain"] = domain
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
        {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
        {"role": "user", "content": f"用户问题：{question.strip() or '（空）'}\n\n只输出 JSON。"},
    ]
    try:
        if caller is not None:
            text = await caller(messages)
        else:
            adapter = select_model(REFUSAL_JUDGE_MODEL)
            text = await adapter.call(messages)
        return _parse_judge_payload(str(text))
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
    "is_final_assistant_message",
    "judge_refusal",
    "parse_query_kb_output",
]
