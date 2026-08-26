from types import SimpleNamespace

import pytest

from yuxi.services.curated_qa_semantic_matcher import (
    CURATED_QA_SEMANTIC_THRESHOLD,
    CuratedQASemanticMatcher,
    _cosine_similarity,
)


def test_cosine_similarity_basic():
    assert _cosine_similarity([1, 0], [1, 0]) == pytest.approx(1.0)
    assert _cosine_similarity([1, 0], [0, 1]) == pytest.approx(0.0)
    assert _cosine_similarity([], [1, 0]) == 0.0
    assert _cosine_similarity([0, 0], [1, 0]) == 0.0
    assert CURATED_QA_SEMANTIC_THRESHOLD == pytest.approx(0.70)


class _FakeEmbedModel:
    def __init__(self, vectors_by_question):
        self.vectors_by_question = vectors_by_question

    async def abatch_encode(self, messages):
        return [self.vectors_by_question[msg] for msg in messages]


def _patch_embed_model(monkeypatch, vectors_by_question):
    monkeypatch.setattr(
        "yuxi.services.curated_qa_semantic_matcher.select_embedding_model",
        lambda spec: _FakeEmbedModel(vectors_by_question),
    )
    monkeypatch.setattr(
        "yuxi.services.curated_qa_semantic_matcher.resolve_embedding_model",
        lambda spec=None: "spec",
    )


@pytest.mark.asyncio
async def test_find_match_returns_best_pair_above_threshold(monkeypatch):
    pairs = [
        SimpleNamespace(id=1, question="如何重置密码？", question_embedding=[1, 0, 0], answer="A"),
        SimpleNamespace(id=2, question="怎么改邮箱？", question_embedding=[0, 1, 0], answer="B"),
    ]

    async def list_enabled_for_agent(agent_slug):
        return pairs

    repo = SimpleNamespace(list_enabled_for_agent=list_enabled_for_agent, session=SimpleNamespace())
    # 查询向量固定为 [0.6, 0.8, 0]：pair1 余弦 0.6（低于阈值）、pair2 余弦 0.8（命中）
    _patch_embed_model(monkeypatch, {"改邮箱的方法": [0.6, 0.8, 0.0]})

    result = await CuratedQASemanticMatcher(repo).find_match(agent_slug="agent-1", question="改邮箱的方法")

    assert result is pairs[1]


@pytest.mark.asyncio
async def test_find_match_returns_none_without_enough_similarity(monkeypatch):
    pairs = [SimpleNamespace(id=1, question="如何重置密码？", question_embedding=[1, 0, 0], answer="A")]

    async def list_enabled_for_agent(agent_slug):
        return pairs

    repo = SimpleNamespace(list_enabled_for_agent=list_enabled_for_agent, session=SimpleNamespace())
    _patch_embed_model(monkeypatch, {"完全无关问题": [0, 0, 1.0]})

    result = await CuratedQASemanticMatcher(repo).find_match(agent_slug="agent-1", question="完全无关问题")

    assert result is None


@pytest.mark.asyncio
async def test_find_match_skips_agent_without_pairs(monkeypatch):
    async def list_enabled_for_agent(agent_slug):
        return []

    repo = SimpleNamespace(list_enabled_for_agent=list_enabled_for_agent, session=SimpleNamespace())

    result = await CuratedQASemanticMatcher(repo).find_match(agent_slug="agent-1", question="任意问题")

    assert result is None


@pytest.mark.asyncio
async def test_find_match_backfills_missing_embeddings(monkeypatch):
    pairs = [
        SimpleNamespace(id=1, question="重置密码", question_embedding=None, answer="A"),
        SimpleNamespace(id=2, question="改邮箱", question_embedding=None, answer="B"),
    ]
    calls = {"flush": 0, "commit": 0}

    class _FakeSession:
        async def flush(self):
            calls["flush"] += 1

        async def commit(self):
            calls["commit"] += 1

    async def list_enabled_for_agent(agent_slug):
        return pairs

    repo = SimpleNamespace(list_enabled_for_agent=list_enabled_for_agent, session=_FakeSession())
    _patch_embed_model(monkeypatch, {"重置密码": [1, 0, 0], "改邮箱": [0, 1, 0]})

    result = await CuratedQASemanticMatcher(repo).find_match(agent_slug="agent-1", question="重置密码")

    assert pairs[0].question_embedding == [1, 0, 0]
    assert pairs[1].question_embedding == [0, 1, 0]
    assert calls["flush"] == 1
    assert calls["commit"] == 1
    assert result is pairs[0]
