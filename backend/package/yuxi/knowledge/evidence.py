"""知识库检索证据评估。

该模块只处理确定性的检索结果检查，不调用大模型。
"""

from __future__ import annotations

import os
import re
import unicodedata
from typing import Any

from yuxi.knowledge.schemas import (
    EvidenceCitationSchema,
    SearchOutputSchema,
    SearchResultSchema,
)

_DEFAULT_MIN_RELEVANCE_SCORE = 0.60
_DEFAULT_STRONG_RELEVANCE_SCORE = 0.80

# 从问题中去除这些问句骨架后，剩余词组用于检查片段是否真的覆盖所问属性。
_QUERY_STOP_PHRASES = tuple(
    sorted(
        {
            "请问",
            "麻烦",
            "帮我",
            "告诉我",
            "查询一下",
            "查一下",
            "查询",
            "相关信息",
            "相关资料",
            "详细信息",
            "是多少",
            "是什么",
            "有哪些",
            "有多少",
            "应该如何处理",
            "如何处理",
            "怎么处理",
            "怎么办",
            "为什么",
            "为何",
            "是否支持",
            "支持哪些",
            "是否",
            "能否",
            "可以吗",
            "支持吗",
            "出现",
            "发生",
            "请介绍",
            "介绍一下",
            "说明一下",
            "的",
            "吗",
            "呢",
        },
        key=len,
        reverse=True,
    )
)

# TEST-C100 这类产品型号在整篇资料中通常反复出现，不能单独作为答案证据。
_MODEL_IDENTIFIER_RE = re.compile(
    r"(?i)\b(?=[a-z0-9._/-]*[a-z])(?=[a-z0-9._/-]*\d)"
    r"[a-z0-9]+(?:[-_/][a-z0-9]+)+\b"
)
_CODE_IDENTIFIER_RE = re.compile(r"(?i)\b[a-z]{1,5}\d{2,8}\b")
_TOKEN_RE = re.compile(r"[\u4e00-\u9fff]+|[a-z0-9][a-z0-9._/-]*", re.IGNORECASE)

_SCORE_PRIORITY = (
    "rerank_score",
    "hybrid_score",
    "score",
    "bm25_score",
    "fusion_score",
    "graph_score",
)

# 这些分数没有统一的 0～1 语义，暂时不能使用统一阈值过滤。
_NON_NORMALIZED_SCORE_FIELDS = {
    "bm25_score",
    "fusion_score",
    "graph_score",
}


def get_evidence_min_score() -> float:
    """读取证据最低分，并限制在 0～1。"""

    raw_value = os.getenv(
        "YUXI_KB_EVIDENCE_MIN_SCORE",
        str(_DEFAULT_MIN_RELEVANCE_SCORE),
    )
    try:
        value = float(raw_value)
    except (TypeError, ValueError):
        value = _DEFAULT_MIN_RELEVANCE_SCORE

    return min(max(value, 0.0), 1.0)


def _normalize_for_matching(value: str) -> str:
    """统一全半角、大小写和标点，供确定性文本覆盖检查使用。"""

    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return re.sub(r"[^\u4e00-\u9fffa-z0-9]+", "", normalized)


def _extract_query_focus_terms(query_text: str | None) -> list[str]:
    """提取问题中真正描述所问属性的词组。

    产品型号会被移除，错误码等短代码会被保留。例如：
    - ``TEST-C100 最大并发用户数是多少`` -> ``最大并发用户数``
    - ``TEST-C100 的电池容量是多少`` -> ``电池容量``
    - ``TEST-C100 出现 E701 应该如何处理`` -> ``e701``
    """

    if not query_text:
        return []

    normalized = unicodedata.normalize("NFKC", str(query_text)).casefold()
    normalized = _MODEL_IDENTIFIER_RE.sub(" ", normalized)
    code_terms = [match.group(0) for match in _CODE_IDENTIFIER_RE.finditer(normalized)]

    for phrase in _QUERY_STOP_PHRASES:
        normalized = normalized.replace(phrase, " ")

    terms: list[str] = []
    for token in _TOKEN_RE.findall(normalized):
        compact = _normalize_for_matching(token)
        if not compact:
            continue

        is_code = bool(_CODE_IDENTIFIER_RE.fullmatch(compact))
        contains_chinese = bool(re.search(r"[\u4e00-\u9fff]", compact))
        if is_code or (contains_chinese and len(compact) >= 2) or len(compact) >= 3:
            if compact not in terms:
                terms.append(compact)

    for code in code_terms:
        compact = _normalize_for_matching(code)
        if compact and compact not in terms:
            terms.append(compact)

    return terms


def _query_focus_is_covered(query_text: str | None, content: str) -> bool | None:
    """判断片段是否明确包含问题的核心属性或错误码。

    返回 ``None`` 表示问题中没有可稳定提取的核心词，此时退回分数门控。
    """

    focus_terms = _extract_query_focus_terms(query_text)
    if not focus_terms:
        return None

    normalized_content = _normalize_for_matching(content)
    return any(term in normalized_content for term in focus_terms)


def _as_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None

    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _result_metadata(result: SearchResultSchema) -> dict[str, Any]:
    return result.metadata if isinstance(result.metadata, dict) else {}


def _find_top_score(
    results: list[SearchResultSchema],
) -> tuple[float | None, str | None]:
    """按优先级查找本轮最高分。"""

    for score_field in _SCORE_PRIORITY:
        values = [
            score
            for result in results
            if (score := _as_float(_result_metadata(result).get(score_field))) is not None
        ]
        if values:
            return max(values), score_field

    return None, None


def _normalized_gate_score(result: SearchResultSchema) -> float | None:
    """获取可使用统一 0～1 阈值比较的分数。

    重排序分数优先。BM25、图检索和融合分数没有统一尺度，
    不能直接拿 0.60 做过滤。
    """

    metadata = _result_metadata(result)

    rerank_score = _as_float(metadata.get("rerank_score"))
    if rerank_score is not None:
        return rerank_score

    if any(metadata.get(field) is not None for field in _NON_NORMALIZED_SCORE_FIELDS):
        return None

    hybrid_score = _as_float(metadata.get("hybrid_score"))
    if hybrid_score is not None and 0.0 <= hybrid_score <= 1.0:
        return hybrid_score

    score = _as_float(metadata.get("score"))
    if score is not None and 0.0 <= score <= 1.0:
        return score

    return None


def _build_citation(
    result: SearchResultSchema,
    index: int,
) -> EvidenceCitationSchema:
    metadata = _result_metadata(result)

    chunk_id = str(metadata.get("chunk_id") or result.id)
    file_name = (
        metadata.get("source")
        or metadata.get("filename")
        or metadata.get("file_name")
    )

    return EvidenceCitationSchema(
        citation_id=f"c{index}",
        kb_id=result.kb_id,
        file_id=result.file_id,
        chunk_id=chunk_id,
        file_name=str(file_name) if file_name else None,
        quote=result.content,
        chunk_index=metadata.get("chunk_index"),
        updated_at=metadata.get("updated_at"),
        score=_as_float(metadata.get("score")),
        rerank_score=_as_float(metadata.get("rerank_score")),
    )


def evaluate_search_output(
    output: SearchOutputSchema,
    *,
    query_text: str | None = None,
    min_relevance_score: float | None = None,
    strong_relevance_score: float = _DEFAULT_STRONG_RELEVANCE_SCORE,
) -> SearchOutputSchema:
    """检查检索结果是否具备交给生成模型的基本条件。

    有查询文本时，先检查片段是否覆盖问题的核心属性。精确覆盖可以挽救
    分数偏低但正文明确命中的结果；仅命中产品型号的片段则不能通过。
    极高语义分数仍可作为同义改写的兜底。
    """

    if output.status == "error":
        output.results = []
        output.citations = []
        return output

    if not output.results:
        return SearchOutputSchema(
            kb_id=output.kb_id,
            status="insufficient",
            reason="no_result",
            message="未检索到可支持答案的知识片段。",
        )

    non_empty_results = [
        result
        for result in output.results
        if result.content and result.content.strip()
    ]

    if not non_empty_results:
        return SearchOutputSchema(
            kb_id=output.kb_id,
            status="insufficient",
            reason="empty_content",
            message="检索结果没有可用于回答的正文内容。",
        )

    top_score, score_type = _find_top_score(non_empty_results)
    threshold = (
        get_evidence_min_score()
        if min_relevance_score is None
        else min(max(float(min_relevance_score), 0.0), 1.0)
    )

    retained_results: list[SearchResultSchema] = []
    strong_threshold = min(max(float(strong_relevance_score), threshold), 1.0)

    for result in non_empty_results:
        gate_score = _normalized_gate_score(result)
        focus_covered = _query_focus_is_covered(query_text, result.content)

        # 正文明确覆盖所问属性或错误码时，即使向量分数处于灰区也保留。
        if focus_covered is True:
            retained_results.append(result)
            continue

        # 能提取核心属性但正文未覆盖时，只有极高语义分数才允许作为
        # 同义改写兜底；避免“只命中产品型号”被误当成答案依据。
        if focus_covered is False:
            if gate_score is not None and gate_score >= strong_threshold:
                retained_results.append(result)
            continue

        # 无法稳定提取核心属性时保持原有分数门控行为。
        # 没有统一尺度的分数暂不硬过滤，继续交给严格 Prompt 判断。
        if gate_score is None or gate_score >= threshold:
            retained_results.append(result)

    if not retained_results:
        return SearchOutputSchema(
            kb_id=output.kb_id,
            status="insufficient",
            reason="low_relevance",
            message="检索片段未覆盖问题核心属性，或相关度不足，不能据此生成业务答案。",
            top_score=top_score,
            score_type=score_type,
        )

    citations = [
        _build_citation(result, index)
        for index, result in enumerate(retained_results, start=1)
    ]

    retained_top_score, retained_score_type = _find_top_score(retained_results)

    return SearchOutputSchema(
        kb_id=output.kb_id,
        status="sufficient",
        reason=None,
        message=None,
        top_score=retained_top_score,
        score_type=retained_score_type,
        results=retained_results,
        citations=citations,
    )