"""预检索知识库快速路径：run 开始时并行检索已绑定知识库，把结果注入首轮上下文。

跳过 ReAct 的 skill 激活与检索决策轮（read SKILL.md → list_kbs → query_kb），
让答案轮成为首个 LLM 调用，显著降低首 token 延迟。

仅写 prompt（context._prefetch_knowledge_block），不改会话历史；单库检索失败
标 status=error 不拖垮整体，对齐 guardrails 对「知识不足」与「系统异常」的区分。
"""

import asyncio

from yuxi.agents.toolkits.kbs.tools import _build_query_output
from yuxi.knowledge.runtime import knowledge_base
from yuxi.utils.logging_config import logger

KNOWLEDGE_BASE_SKILL_SLUG = "knowledge-base"
PREFETCH_MAX_CHUNKS_PER_KB = 5


async def prefetch_knowledge_context(context) -> None:
    """预检索开启时，并行检索可见知识库并组装注入 prompt 的 markdown 块。"""
    if not getattr(context, "prefetch_knowledge", False):
        return
    query = getattr(context, "_latest_user_query", None)
    visible_kbs = _visible_knowledge_bases(context)
    if not isinstance(query, str) or not query.strip() or not visible_kbs:
        return

    try:
        kb_results = await _retrieve_all(visible_kbs, query.strip())
    except Exception as exc:
        logger.warning("知识库预检索整体失败，回退常规流程: %s", exc)
        return
    if not kb_results:
        return

    context._prefetch_knowledge_block = _build_prefetch_block(kb_results)
    prompt_skills = getattr(context, "_prompt_skills", None)
    if isinstance(prompt_skills, list):
        context._prompt_skills = [slug for slug in prompt_skills if slug != KNOWLEDGE_BASE_SKILL_SLUG]


def _visible_knowledge_bases(context) -> list[dict]:
    return getattr(context, "_visible_knowledge_bases", None) or []


async def _retrieve_all(visible_kbs: list[dict], query: str) -> list[dict]:
    retrievers = knowledge_base.get_retrievers()
    return list(
        await asyncio.gather(*(_retrieve_one(kb, retrievers.get(kb.get("kb_id")), query) for kb in visible_kbs))
    )


async def _retrieve_one(kb: dict, retriever_info: dict | None, query: str) -> dict:
    kb_id = kb.get("kb_id")
    name = kb.get("name") or kb_id
    if retriever_info is None:
        return {"kb_id": kb_id, "name": name, "status": "error", "reason": "retriever_unavailable", "results": []}
    try:
        output = await retriever_info["retriever"](query)
        return await _normalize_output(kb, output)
    except Exception as exc:
        logger.warning("知识库预检索失败 kb_id=%s: %s", kb_id, exc)
        return {"kb_id": kb_id, "name": name, "status": "error", "reason": "retrieval_error", "results": []}


async def _normalize_output(kb: dict, output) -> dict:
    """复用 query_kb 的语义：retriever 裸返回 {kb_id, results}，status/reason 由
    _build_query_output 统一推导，保证预检索与工具调用对「知识不足/系统异常」的判定一致。"""
    kb_id = kb.get("kb_id")
    name = kb.get("name") or kb_id
    schema = await _build_query_output(kb_id, output)
    if not isinstance(schema, dict):
        return {"kb_id": kb_id, "name": name, "status": "error", "reason": "invalid_result", "results": []}
    results = schema.get("results") or []
    return {**schema, "name": name, "results": results[:PREFETCH_MAX_CHUNKS_PER_KB]}


def _build_prefetch_block(kb_results: list[dict]) -> str:
    lines = [
        "<| 知识库预检索结果 |>",
        "本轮已自动检索以下知识库，请直接依据这些检索结果回答；结果不足或与问题无关时按统一拒答话术回复，需要补充检索时再调用知识库工具。",
        "",
        "已检索知识库：",
    ]
    for kb in kb_results:
        lines.append(f"- {kb['kb_id']}（{kb['name']}）")
    lines.append("")
    for kb in kb_results:
        lines.extend(_kb_section(kb))
    return "\n".join(lines)


def _kb_section(kb: dict) -> list[str]:
    lines = [f"【{kb['kb_id']} · {kb['name']}】status={kb['status']}"]
    if kb.get("reason"):
        lines.append(f"原因：{kb['reason']}")
    for index, item in enumerate(kb.get("results", []), 1):
        lines.append(f"- 候选片段 {index}（来源：{_format_source(item)}）")
        lines.append(f"  正文：{str(_item_value(item, 'content', '')).strip()}")
    return lines


def _format_source(item) -> str:
    metadata = _item_value(item, "metadata", {})
    metadata = metadata if isinstance(metadata, dict) else {}
    parts = []
    for key in ("file_id", "chunk_id"):
        value = metadata.get(key) or _item_value(item, key, "") or ""
        if value:
            parts.append(f"{key}={value}")
    score = metadata.get("score")
    if score is None:
        score = _item_value(item, "score", "")
    if score not in ("", None):
        parts.append(f"score={score}")
    return ", ".join(parts) or "无来源信息"


def _item_value(item, key: str, default):
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)
