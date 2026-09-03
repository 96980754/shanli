"""业务范围入口门：在进主模型前拦截明显业务外/闲聊问题（决策①）。

单智能体部署下，知识库即业务语料：与整个业务语料毫不相关的问题（linux epoll、
天气、闲聊等）没有必要进主模型，也绝不应给出“业务答案”或被转人工。

判定按 免费关键词 → embedding 亲和 → 小模型确认 的层级早退：
- 关键词命中（产品/业务域词）→ in_scope（绝大多数业务问题零开销返回）；
- query 对“业务语料锚”（KB 名/描述）的 embedding 亲和 ≥ 阈值 → in_scope；
- 二者都不满足才调用一次小模型确认，仅当模型确凿判定业务外才输出 off_topic。

所有不确定一律返回 in_scope（宁可放过、不可误拦）：放行后若模型无依据硬答，
由 save_messages 阶段的无证据兜底（should_revoke_no_evidence）再兜一层。

只在「thread 尚无已作答回答的首答轮」由调用方触发；续答轮不进门，直接放行主模型。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from yuxi.config.app import resolve_embedding_model
from yuxi.models import select_model
from yuxi.models.embed import select_embedding_model
from yuxi.services.knowledge_answer_disposition import REFUSAL_JUDGE_MODEL
from yuxi.utils.logging_config import logger

# 业务语料锚的 embedding 亲和阈值：query 与任一锚 >= 该值即视为业务内。
# 参考既有余弦阈值习惯（curated QA 0.70 / 全库检索下限 0.35），锚是“KB 名/描述”
# 这类短事实文本，业务问题的命中通常明显高于无关话题，取 0.5。
AFFINITY_IN_SCOPE_THRESHOLD = 0.5

# 内置业务种子词：观察自部署知识库（客服知识库.md 的 # 产品线标题、评估 JSONL 文件名的域词）。
# 只放“业务特有、几乎不会在闲聊/外部话题出现”的词，避免泛词（系统/终端等）误放行跑题。
# 部署方可随业务扩展。
BUILTIN_SCOPE_TERMS: frozenset[str] = frozenset(
    {
        "调度台",
        "运营平台",
        "安卓",
        "cat1",
        "cat1模组",
        "mdm",
        "miniserver",
        "pocstars",
        "mno",
        "客服",
        "白皮书",
        "f10",
    }
)

JUDGE_OFF_TOPIC_SYSTEM_PROMPT = """\
你是企业知识库客服的入口过滤器。仅判断一件事：用户问题是否属于该企业知识库的业务范围。

输入会给出“业务范围说明”（Agent 业务配置 + 该用户可访问的知识库名/描述）和用户问题。
- 只有能确凿判断问题属于业务外的闲聊、与业务无关的其它话题时，才输出 {"off_topic": true}；
- 问题含糊、涉及内部业务细节但不敢确定、或与说明中任一业务沾边，一律输出 {"off_topic": false}；
- 宁可放过，不可误拦（拦错意味着把业务内问题当闲聊拒掉且不给人工入口）。

只输出 JSON，不要输出任何其它文字。
"""


@dataclass
class ScopeCorpus:
    """一次入口判定所需的业务语料：关键词集合 + 语义锚文本 + 供 LLM 阅读的范围说明。"""

    terms: frozenset[str] = field(default_factory=frozenset)
    anchors: list[str] = field(default_factory=list)
    description: str = ""

    _anchor_vectors: list[list[float]] | None = field(default=None, repr=False)

    def text_lines(self) -> list[str]:
        lines = [anchor.strip() for anchor in self.anchors if anchor and anchor.strip()]
        if self.terms:
            lines.append("业务关键词：" + "、".join(sorted(self.terms)))
        return lines


async def build_scope_corpus(
    *,
    uid: str | None,
    system_prompt: str | None = None,
    enabled_kb_ids: list[str] | None = None,
    list_databases: Callable[..., Any] | None = None,
) -> ScopeCorpus:
    """从 agent 业务配置 + 用户可访问知识库（名/描述）构建业务语料。

    list_databases 可注入以便测试，默认走 knowledge_base.get_databases_by_uid。
    """
    terms = set(BUILTIN_SCOPE_TERMS)
    anchors: list[str] = []
    lines: list[str] = []

    if system_prompt and str(system_prompt).strip():
        lines.append("Agent 业务配置：" + str(system_prompt).strip())

    loader = list_databases
    if loader is None:
        from yuxi.knowledge.runtime import knowledge_base

        loader = lambda u: knowledge_base.get_databases_by_uid(u)  # noqa: E731
    try:
        result = await loader(str(uid or "")) if uid else {}
        databases = (result or {}).get("databases") or []
        enabled_ids = {str(value).strip() for value in (enabled_kb_ids or []) if str(value).strip()}
        for db in databases:
            kb_id = str(db.get("kb_id") or "").strip()
            if enabled_ids and kb_id not in enabled_ids:
                continue
            name = str(db.get("name") or "").strip()
            description = str(db.get("description") or "").strip()
            if not name and not description:
                continue
            anchors.append(f"{name}：{description}" if description else name)
            lines.append(f"知识库[{name}]：{description}" if description else f"知识库：{name}")
    except Exception as exc:  # noqa: BLE001 — 语料构建失败不阻断入口（退化为仅关键词判定）
        logger.warning("构建业务语料失败，入口门退化为关键词判定: {}", exc)

    return ScopeCorpus(terms=frozenset(terms), anchors=anchors, description="\n".join(lines))


def _term_hit(question: str, corpus: ScopeCorpus) -> bool:
    lowered = question.casefold()
    return any(term.casefold() in lowered for term in corpus.terms)


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    va, vb = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    denominator = float(np.linalg.norm(va) * np.linalg.norm(vb))
    if denominator == 0.0:
        return 0.0
    return float(np.dot(va, vb) / denominator)


async def _max_anchor_affinity(
    question: str,
    corpus: ScopeCorpus,
    embedder: Any | None = None,
) -> float:
    model = embedder or select_embedding_model(resolve_embedding_model())
    if corpus._anchor_vectors is None:
        corpus._anchor_vectors = list(await model.abatch_encode(corpus.anchors))
    if not corpus._anchor_vectors:
        return 0.0
    query_vector = (await model.abatch_encode([question]))[0]
    return max(_cosine_similarity(query_vector, vector) for vector in corpus._anchor_vectors)


def _parse_off_topic_payload(text: str) -> bool | None:
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
    if not isinstance(payload, dict) or not isinstance(payload.get("off_topic"), bool):
        return None
    return payload["off_topic"]


async def judge_off_topic(question: str, scope_description: str, *, caller=None) -> bool | None:
    """一次小模型确认问题是否业务外。失败/未配置返回 None（调用方按业务内处理）。"""
    if not REFUSAL_JUDGE_MODEL and caller is None:
        return None
    user_prompt = (
        f"业务范围说明：\n{scope_description.strip() or '（无）'}"
        f"\n\n用户问题：{question.strip()}\n\n只输出 JSON。"
    )
    messages = [
        {"role": "system", "content": JUDGE_OFF_TOPIC_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]
    try:
        if caller is not None:
            text = await caller(messages)
        else:
            adapter = select_model(REFUSAL_JUDGE_MODEL)
            text = (await adapter.call(messages)).content
        return _parse_off_topic_payload(str(text or ""))
    except Exception as exc:  # noqa: BLE001 — 判定失败按业务内放行，不影响问答
        logger.warning("跑题入口确认模型调用失败，按业务内放行: {}", exc)
        return None


async def evaluate_scope(question: str, corpus: ScopeCorpus, *, caller=None, embedder=None) -> str:
    """返回 in_scope / off_topic。不确定一律 in_scope。"""
    q = str(question or "").strip()
    if not q:
        return "in_scope"

    if _term_hit(q, corpus):
        return "in_scope"

    if corpus.anchors:
        try:
            affinity = await _max_anchor_affinity(q, corpus, embedder=embedder)
            if affinity >= AFFINITY_IN_SCOPE_THRESHOLD:
                return "in_scope"
        except Exception as exc:  # noqa: BLE001 — embedding 失败退化走 LLM 确认
            logger.warning("入口门 embedding 亲和计算失败，改走 LLM 确认: {}", exc)

    if await judge_off_topic(q, corpus.description, caller=caller):
        return "off_topic"
    return "in_scope"


__all__ = [
    "AFFINITY_IN_SCOPE_THRESHOLD",
    "BUILTIN_SCOPE_TERMS",
    "JUDGE_OFF_TOPIC_SYSTEM_PROMPT",
    "ScopeCorpus",
    "build_scope_corpus",
    "evaluate_scope",
    "judge_off_topic",
]
