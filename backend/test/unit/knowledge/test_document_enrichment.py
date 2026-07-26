from __future__ import annotations

from types import SimpleNamespace

import pytest

from yuxi.knowledge import enrichment
from yuxi.knowledge.enrichment import (
    DocumentEnrichmentGenerator,
    EnrichmentProviderUnavailable,
    EnrichmentValidationError,
    mark_enrichment_data_outdated,
    normalize_keywords,
    normalize_tags,
    validate_summary,
)


def test_summary_rejects_numbers_not_present_in_formal_markdown():
    with pytest.raises(EnrichmentValidationError, match="原文中不存在"):
        validate_summary("产品支持版本 2.1。", "产品支持版本 3.0。", max_chars=500)


def test_summary_accepts_existing_product_version_and_number():
    summary = validate_summary(
        "Shanli 2.1 支持 16 个知识库。",
        "Shanli 2.1 支持 16 个知识库。",
        max_chars=500,
    )

    assert summary == "Shanli 2.1 支持 16 个知识库。"


def test_keywords_are_evidence_based_normalized_and_deduplicated():
    keywords = normalize_keywords(
        ["  Shanli  ", "shanli", "知识库", "不存在术语", "的"],
        "Shanli 是一个智能知识库平台。",
        limit=10,
    )

    assert keywords == [
        {"value": "Shanli", "normalized_value": "shanli"},
        {"value": "知识库", "normalized_value": "知识库"},
    ]


def test_tags_are_case_insensitive_and_do_not_semantically_merge():
    tags = normalize_tags([" RAG ", "rag", "知识检索", "知识搜索"], limit=10)

    assert tags == [
        {"name": "RAG", "normalized_name": "rag", "taxonomy_id": None},
        {"name": "知识检索", "normalized_name": "知识检索", "taxonomy_id": None},
        {"name": "知识搜索", "normalized_name": "知识搜索", "taxonomy_id": None},
    ]


def test_body_change_marks_existing_components_outdated_without_deleting_manual_values():
    data = mark_enrichment_data_outdated(
        {
            "summary": {"text": "人工摘要", "source": "manual", "status": "ready"},
            "keywords": [{"value": "知识库", "source": "manual", "status": "ready"}],
            "tags": [{"name": "RAG", "source": "manual", "status": "ready"}],
        }
    )

    assert data["summary"]["text"] == "人工摘要"
    assert data["summary"]["status"] == "possibly_outdated"
    assert data["keywords"][0]["status"] == "possibly_outdated"
    assert data["tags"][0]["status"] == "possibly_outdated"


@pytest.mark.asyncio
async def test_generator_without_provider_does_not_select_an_external_model(monkeypatch):
    def unexpected_select_model(**_kwargs):
        raise AssertionError("select_model must not be called without a configured model")

    monkeypatch.setattr(enrichment, "select_model", unexpected_select_model)

    with pytest.raises(EnrichmentProviderUnavailable):
        await DocumentEnrichmentGenerator().generate(
            "正式正文",
            components={"summary"},
            model_spec=None,
            temperature=0,
            timeout_seconds=1,
            chunk_chars=1000,
            attempts=2,
            summary_max_chars=500,
            keyword_limit=10,
            tag_limit=8,
        )


@pytest.mark.asyncio
async def test_generator_limits_invalid_json_repair_to_two_attempts(monkeypatch):
    calls = 0

    class InvalidModel:
        async def ainvoke(self, _messages):
            nonlocal calls
            calls += 1
            return SimpleNamespace(content="{invalid")

    monkeypatch.setattr(
        enrichment,
        "select_model",
        lambda **_kwargs: SimpleNamespace(model=InvalidModel(), model_name="test-model"),
    )

    with pytest.raises(EnrichmentValidationError, match="结构化结果"):
        await DocumentEnrichmentGenerator().generate(
            "正式正文",
            components={"summary"},
            model_spec="test:model",
            temperature=0,
            timeout_seconds=1,
            chunk_chars=1000,
            attempts=10,
            summary_max_chars=500,
            keyword_limit=10,
            tag_limit=8,
        )

    assert calls == 2
