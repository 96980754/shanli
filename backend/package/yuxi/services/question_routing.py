"""问题路由：简单问题用轻量模型，复杂问题用智能体模型。

启用条件：智能体配置了 model_simple（留空即关闭，不产生任何额外查询或模型调用，
行为与未启用时完全一致）。判定只看问题文本与输入形态（图片/附件），不看检索结果；
分档失败一律按复杂处理——省钱不能以答砸为代价，宁可多花一次完整模型的调用。

分档按 免费规则 → 小模型确认 的层级早退（形状仿 knowledge_scope_gate）：
- 规则命中（附件/长问题/复杂意图词/短问题无复杂信号）→ 零开销返回；
- 规则未决才调用一次小模型；未配置判定模型或调用失败 → complex。

续问轮复杂度向上粘滞：同 thread 上一 run 判过 complex，本轮直接 complex、不再分档。
误判方向只有“多花一次完整模型”，不会出现轻量模型接复杂上下文。
"""

from __future__ import annotations

import json
import os
from typing import Any

from fastapi import HTTPException

from yuxi.models import select_model
from yuxi.models.providers.cache import model_cache
from yuxi.repositories.agent_run_repository import AgentRunRepository
from yuxi.services.knowledge_answer_disposition import REFUSAL_JUDGE_MODEL
from yuxi.utils.logging_config import logger

# 分档判定模型：QUESTION_ROUTE_JUDGE_MODEL 优先，缺省退回拒答 judge 的快模型；
# 两者均未配置则只跑规则层（规则未决直接 complex）。判对率不达标时可置空该模型
# 先降级为纯规则路由，无需改代码。
QUESTION_ROUTE_JUDGE_MODEL = os.getenv("QUESTION_ROUTE_JUDGE_MODEL", "").strip() or REFUSAL_JUDGE_MODEL

# 复杂意图词：命中即视为需要完整模型。只放“任务重”的词，避免泛词误伤；
# 部署方可随业务扩展（口径仿 knowledge_scope_gate.BUILTIN_SCOPE_TERMS）。
COMPLEXITY_TERMS: frozenset[str] = frozenset(
    {
        "对比",
        "比较",
        "区别",
        "差异",
        "分析",
        "为什么",
        "怎么回事",
        "排查",
        "定位",
        "故障",
        "报错",
        "总结",
        "归纳",
        "提炼",
        "报告",
        "方案",
        "最佳实践",
        "写一份",
        "写个",
        "写一封",
        "帮我写",
        "生成",
        "起草",
        "列一个",
        "列一份",
        "步骤",
        "流程",
        "清单",
    }
)

# 短问题上限：不超过该长度且无复杂信号 → simple；超过 COMPLEX_MIN_CHARS → complex；
# 两者之间交由小模型分档。
SIMPLE_MAX_CHARS = 30
COMPLEX_MIN_CHARS = 120

JUDGE_COMPLEXITY_SYSTEM_PROMPT = """\
你是企业知识库客服的问题分档器。仅判断一件事：用户问题应该由轻量模型还是完整模型回答。

- 轻量（simple）：事实型单点问题——查一个参数/定义/价格、是否支持某功能、寒暄或转接诉求，一两句话能答完。
- 完整（complex）：多步推理、对比多个产品或方案、归因分析、故障排查、长文总结、方案或文档撰写，或问题模糊需要澄清。

只输出 JSON（不要输出任何其它文字）：
- {"complexity": "simple"}
- {"complexity": "complex"}
"""


async def route_question_model_spec(
    *,
    explicit_model_spec: str | None,
    base_spec: str,
    agent_item,
    agent_backend,
    question: str,
    has_image: bool,
    has_attachment: bool,
    thread_id: str,
    uid: str,
    db,
) -> tuple[str, dict[str, Any] | None]:
    """run 创建点的路由入口：返回 (最终 model_spec, route 记录或 None)。

    优先级：请求显式手选 > 未配置 model_simple（路由关闭）> 分档。前两种情况
    原样返回 base_spec 且不产生 route 记录，不触发任何额外查询或模型调用。
    model_simple 配置了但不可用时直接报错，不静默回退。
    """
    if isinstance(explicit_model_spec, str) and explicit_model_spec.strip():
        return base_spec, None

    model_simple = _configured_model_simple(agent_item, agent_backend)
    if not model_simple:
        return base_spec, None

    info = model_cache.get_model_info(model_simple)
    if not info or info.model_type != "chat":
        raise HTTPException(status_code=422, detail=f"问题路由配置的简单问题模型不可用: '{model_simple}'")

    if await _thread_last_route_complex(db, thread_id, uid):
        verdict = {"complexity": "complex", "tier": "thread-inertia", "reason": "thread 上一 run 为 complex"}
    else:
        verdict = await classify_complexity(question, has_image=has_image, has_attachment=has_attachment)

    final_spec = model_simple if verdict["complexity"] == "simple" else base_spec
    logger.info(
        "问题路由: complexity={} tier={} model={} reason={}",
        verdict["complexity"],
        verdict["tier"],
        final_spec,
        verdict["reason"],
    )
    return final_spec, verdict


async def classify_complexity(
    question: str,
    *,
    has_image: bool = False,
    has_attachment: bool = False,
    caller=None,
) -> dict[str, Any]:
    """分档单点：返回 {"complexity": simple|complex, "tier": rule|llm|fallback, "reason": str}。

    caller 可注入以便测试：async (messages: list[dict]) -> str。
    """
    q = str(question or "").strip()
    if has_image or has_attachment:
        return {"complexity": "complex", "tier": "rule", "reason": "带图片或附件"}
    if not q:
        return {"complexity": "complex", "tier": "rule", "reason": "空问题"}
    if len(q) > COMPLEX_MIN_CHARS:
        return {"complexity": "complex", "tier": "rule", "reason": f"问题长度超过{COMPLEX_MIN_CHARS}字"}
    hit = next((term for term in COMPLEXITY_TERMS if term in q), None)
    if hit:
        return {"complexity": "complex", "tier": "rule", "reason": f"命中复杂意图词「{hit}」"}
    if len(q) <= SIMPLE_MAX_CHARS:
        return {"complexity": "simple", "tier": "rule", "reason": "短问题且无复杂信号"}

    complexity = await _judge_complexity(q, caller=caller)
    if complexity is None:
        return {"complexity": "complex", "tier": "fallback", "reason": "分档判定不可用，按复杂处理"}
    return {"complexity": complexity, "tier": "llm", "reason": "小模型分档"}


def _configured_model_simple(agent_item, agent_backend) -> str:
    """读智能体配置里的 model_simple，解析方式与 resolve_agent_run_model_spec 一致。"""
    config_json = getattr(agent_item, "config_json", None) or {}
    config_context = config_json.get("context") if isinstance(config_json, dict) else {}
    context = agent_backend.context_schema()
    if isinstance(config_context, dict):
        context.update_from_dict(config_context)
    return (getattr(context, "model_simple", "") or "").strip()


async def _thread_last_route_complex(db, thread_id: str, uid) -> bool:
    """同 thread 上一 run 是否判过 complex（复杂度向上粘滞）。"""
    last_run = await AgentRunRepository(db).get_latest_run_by_thread_for_user(thread_id, str(uid))
    last_route = last_run.input_payload.get("route") if last_run and isinstance(last_run.input_payload, dict) else None
    return isinstance(last_route, dict) and last_route.get("complexity") == "complex"


async def _judge_complexity(question: str, *, caller=None) -> str | None:
    """一次小模型确认问题复杂度。失败/未配置返回 None（调用方按 complex 处理）。"""
    if not QUESTION_ROUTE_JUDGE_MODEL and caller is None:
        return None
    messages = [
        {"role": "system", "content": JUDGE_COMPLEXITY_SYSTEM_PROMPT},
        {"role": "user", "content": f"用户问题：{question.strip() or '（空）'}\n\n只输出 JSON。"},
    ]
    try:
        if caller is not None:
            text = await caller(messages)
        else:
            adapter = select_model(QUESTION_ROUTE_JUDGE_MODEL)
            text = (await adapter.call(messages)).content
        return _parse_complexity_payload(str(text or ""))
    except Exception as exc:  # noqa: BLE001 — 分档失败不应影响问答创建
        logger.warning("问题分档判定模型调用失败，按复杂处理: {}", exc)
        return None


def _parse_complexity_payload(text: str) -> str | None:
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
    if not isinstance(payload, dict) or payload.get("complexity") not in {"simple", "complex"}:
        return None
    return payload["complexity"]
