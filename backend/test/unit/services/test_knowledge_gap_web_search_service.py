from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from yuxi.services.knowledge_gap_web_search_service import KnowledgeGapWebSearchService


class _FakeSearchClient:
    def __init__(self, response):
        self.response = response
        self.calls = []

    async def search(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


def _gap(**overrides):
    data = {
        "id": 7,
        "question": "项目最新发布时间是什么？",
        "agent_slug": "chatbot",
        "assistant_message_id": 99,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


@pytest.mark.asyncio
async def test_search_returns_draft_answer_and_safe_sources(monkeypatch):
    gap = _gap()
    gap_repo = SimpleNamespace(get=AsyncMock(return_value=gap))
    client = _FakeSearchClient(
        {
            "answer": "候选联网答案",
            "results": [
                {"title": "官方资料", "url": "https://example.com/a", "content": "正文 A", "score": 0.9},
                {"title": "危险链接", "url": "javascript:alert(1)", "content": "不要保留"},
            ],
        }
    )

    monkeypatch.setattr(
        "yuxi.services.knowledge_gap_web_search_service.KnowledgeGapRepository",
        lambda session: gap_repo,
    )
    monkeypatch.setattr(KnowledgeGapWebSearchService, "_build_client", staticmethod(lambda: client))

    result = await KnowledgeGapWebSearchService().search(object(), gap.id)

    assert result["draft_answer"] == "候选联网答案"
    assert result["question"] == gap.question
    assert result["sources"] == [
        {"title": "官方资料", "url": "https://example.com/a", "content": "正文 A", "score": 0.9}
    ]
    assert client.calls[0]["include_answer"] is True
    assert client.calls[0]["search_depth"] == "advanced"


def test_build_client_requires_tavily_api_key(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)

    with pytest.raises(ValueError, match="TAVILY_API_KEY"):
        KnowledgeGapWebSearchService._build_client()


@pytest.mark.parametrize(
    "bad_key",
    [
        "# 获取搜索服务的 api key 请访问 https://tavily.com",
        "tvly-中文占位",
        "tvly-abc def",
    ],
)
def test_build_client_rejects_placeholder_or_non_ascii_key(monkeypatch, bad_key):
    monkeypatch.setenv("TAVILY_API_KEY", bad_key)

    with pytest.raises(ValueError, match="配置无效"):
        KnowledgeGapWebSearchService._build_client()


def test_build_client_accepts_real_ascii_key(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "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")

    client = KnowledgeGapWebSearchService._build_client()

    assert client is not None


@pytest.mark.asyncio
async def test_save_answer_creates_curated_qa_and_resolves_gap(monkeypatch):
    gap = _gap()
    updated_gap = SimpleNamespace(to_dict=lambda: {"id": gap.id, "status": "resolved"})
    gap_repo = SimpleNamespace(
        get=AsyncMock(return_value=gap),
        update_status=AsyncMock(return_value=updated_gap),
    )
    qa_pair = SimpleNamespace(id=13, to_dict=lambda: {"id": 13, "answer": "人工确认答案"})
    qa_repo = SimpleNamespace(upsert=AsyncMock(return_value=qa_pair))
    session = SimpleNamespace(commit=AsyncMock(), refresh=AsyncMock())

    monkeypatch.setattr(
        "yuxi.services.knowledge_gap_web_search_service.KnowledgeGapRepository",
        lambda current_session: gap_repo,
    )
    monkeypatch.setattr(
        "yuxi.services.knowledge_gap_web_search_service.CuratedQARepository",
        lambda current_session: qa_repo,
    )

    result = await KnowledgeGapWebSearchService().save_answer(
        session,
        gap_id=gap.id,
        answer="  人工确认答案  ",
        operator_uid="admin-1",
        sources=[{"title": "来源", "url": "https://example.com/source", "content": "内容"}],
    )

    qa_repo.upsert.assert_awaited_once_with(
        agent_slug="chatbot",
        question=gap.question,
        answer="人工确认答案",
        operator_uid="admin-1",
        source_type="knowledge_gap",
        source_message_id=99,
    )
    update_kwargs = gap_repo.update_status.await_args.kwargs
    assert update_kwargs["status"] == "resolved"
    assert "https://example.com/source" in update_kwargs["resolution_note"]
    assert result["qa_pair"]["id"] == 13
    assert result["gap"]["status"] == "resolved"
    assert result["gap"]["has_answer"] is True
    session.commit.assert_awaited_once()
