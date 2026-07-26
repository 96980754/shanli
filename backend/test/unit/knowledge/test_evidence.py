from yuxi.knowledge.evidence import evaluate_search_output
from yuxi.knowledge.schemas import SearchOutputSchema, SearchResultSchema


def _result(
    *,
    content: str = "有效知识",
    score: float | None = None,
    rerank_score: float | None = None,
    extra_metadata: dict | None = None,
) -> SearchResultSchema:
    metadata = {
        "chunk_id": "chunk-1",
        "file_id": "file-1",
        "source": "test.docx",
    }

    if score is not None:
        metadata["score"] = score
    if rerank_score is not None:
        metadata["rerank_score"] = rerank_score
    if extra_metadata:
        metadata.update(extra_metadata)

    return SearchResultSchema(
        id="chunk-1",
        kb_id="kb-1",
        file_id="file-1",
        content=content,
        metadata=metadata,
    )


def test_evidence_marks_empty_results_as_insufficient():
    output = evaluate_search_output(
        SearchOutputSchema(kb_id="kb-1", results=[]),
        min_relevance_score=0.60,
    )

    assert output.status == "insufficient"
    assert output.reason == "no_result"
    assert output.results == []
    assert output.citations == []


def test_evidence_rejects_low_vector_score():
    output = evaluate_search_output(
        SearchOutputSchema(
            kb_id="kb-1",
            results=[_result(score=0.5656)],
        ),
        min_relevance_score=0.60,
    )

    assert output.status == "insufficient"
    assert output.reason == "low_relevance"
    assert output.top_score == 0.5656
    assert output.results == []


def test_evidence_keeps_high_vector_score_and_builds_citation():
    output = evaluate_search_output(
        SearchOutputSchema(
            kb_id="kb-1",
            results=[_result(score=0.6867)],
        ),
        min_relevance_score=0.60,
    )

    assert output.status == "sufficient"
    assert output.top_score == 0.6867
    assert len(output.results) == 1
    assert len(output.citations) == 1
    assert output.citations[0].file_name == "test.docx"
    assert output.citations[0].chunk_id == "chunk-1"


def test_evidence_prefers_rerank_score_for_gate():
    output = evaluate_search_output(
        SearchOutputSchema(
            kb_id="kb-1",
            results=[_result(score=0.90, rerank_score=0.40)],
        ),
        min_relevance_score=0.60,
    )

    assert output.status == "insufficient"
    assert output.reason == "low_relevance"
    assert output.top_score == 0.40
    assert output.score_type == "rerank_score"


def test_evidence_does_not_apply_normalized_threshold_to_bm25():
    output = evaluate_search_output(
        SearchOutputSchema(
            kb_id="kb-1",
            results=[
                _result(
                    score=0.20,
                    extra_metadata={"bm25_score": 0.20},
                )
            ],
        ),
        min_relevance_score=0.60,
    )

    assert output.status == "sufficient"
    assert len(output.results) == 1


def test_evidence_keeps_exact_attribute_match_below_vector_threshold():
    output = evaluate_search_output(
        SearchOutputSchema(
            kb_id="kb-1",
            results=[
                _result(
                    content="产品型号：TEST-C100\n最大并发用户数：137人。",
                    score=0.5460,
                )
            ],
        ),
        query_text="TEST-C100 最大并发用户数是多少？",
        min_relevance_score=0.60,
    )

    assert output.status == "sufficient"
    assert output.reason is None
    assert output.top_score == 0.5460
    assert len(output.results) == 1
    assert len(output.citations) == 1


def test_evidence_rejects_entity_only_match_in_score_gray_zone():
    output = evaluate_search_output(
        SearchOutputSchema(
            kb_id="kb-1",
            results=[
                _result(
                    content=(
                        "产品型号：TEST-C100\n"
                        "产品版本：V1.0\n"
                        "最大并发用户数：137人。\n"
                        "支持 SIP 和 WebSocket。"
                    ),
                    score=0.5578,
                )
            ],
        ),
        query_text="TEST-C100 的电池容量是多少？",
        min_relevance_score=0.60,
    )

    assert output.status == "insufficient"
    assert output.reason == "low_relevance"
    assert output.top_score == 0.5578
    assert output.results == []
    assert output.citations == []


def test_evidence_keeps_error_code_match_below_vector_threshold():
    output = evaluate_search_output(
        SearchOutputSchema(
            kb_id="kb-1",
            results=[
                _result(
                    content=(
                        "故障码 E701 表示账号鉴权失败。\n"
                        "处理方法：检查服务地址、账号和密码，修改配置后重新登录。"
                    ),
                    score=0.5400,
                )
            ],
        ),
        query_text="TEST-C100 出现 E701 应该如何处理？",
        min_relevance_score=0.60,
    )

    assert output.status == "sufficient"
    assert output.reason is None
    assert len(output.results) == 1


def test_evidence_allows_very_high_semantic_score_as_synonym_fallback():
    output = evaluate_search_output(
        SearchOutputSchema(
            kb_id="kb-1",
            results=[
                _result(
                    content="设备最多可同时服务一百三十七名在线人员。",
                    score=0.85,
                )
            ],
        ),
        query_text="TEST-C100 最大并发用户数是多少？",
        min_relevance_score=0.60,
        strong_relevance_score=0.80,
    )

    assert output.status == "sufficient"
    assert len(output.results) == 1
