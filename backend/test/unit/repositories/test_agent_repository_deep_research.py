from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from yuxi.repositories.agent_repository import (
    AgentRepository,
    DEEP_RESEARCH_AGENT_SLUG,
    DEFAULT_AGENT_BACKEND_ID,
    FACT_VERIFIER_AGENT_SLUG,
    RESEARCH_EXPLORER_AGENT_SLUG,
    SUB_AGENT_BACKEND_ID,
)


class CollectingDb:
    def __init__(self):
        self.added: list = []
        self.commit = AsyncMock()
        self.refresh = AsyncMock()

    def add(self, item):
        self.added.append(item)


@pytest.mark.asyncio
async def test_ensure_deep_research_agents_creates_orchestrator_and_subagents(monkeypatch):
    db = CollectingDb()
    repo = AgentRepository(db)

    async def get_by_slug(_slug):
        return None

    monkeypatch.setattr(repo, "get_by_slug", get_by_slug)

    await repo.ensure_deep_research_agents()

    created = {agent.slug: agent for agent in db.added}
    assert set(created) == {
        DEEP_RESEARCH_AGENT_SLUG,
        RESEARCH_EXPLORER_AGENT_SLUG,
        FACT_VERIFIER_AGENT_SLUG,
    }

    explorer = created[RESEARCH_EXPLORER_AGENT_SLUG]
    verifier = created[FACT_VERIFIER_AGENT_SLUG]
    assert explorer.backend_id == SUB_AGENT_BACKEND_ID and explorer.is_subagent is True
    assert verifier.backend_id == SUB_AGENT_BACKEND_ID and verifier.is_subagent is True

    orchestrator = created[DEEP_RESEARCH_AGENT_SLUG]
    assert orchestrator.backend_id == DEFAULT_AGENT_BACKEND_ID
    assert orchestrator.is_subagent is False
    assert orchestrator.is_default is False
    context = orchestrator.config_json["context"]
    assert context["subagents"] == [RESEARCH_EXPLORER_AGENT_SLUG, FACT_VERIFIER_AGENT_SLUG]
    assert "skills" not in context
    assert "读取 `deep-research` 技能" not in context["system_prompt"]


@pytest.mark.asyncio
async def test_ensure_deep_research_agents_is_idempotent(monkeypatch):
    db = CollectingDb()
    repo = AgentRepository(db)

    async def get_by_slug(slug):
        return SimpleNamespace(slug=slug, config_json={"context": {}})

    monkeypatch.setattr(repo, "get_by_slug", get_by_slug)

    await repo.ensure_deep_research_agents()


@pytest.mark.asyncio
async def test_ensure_deep_research_agents_migrates_legacy_skill_without_overwriting_context(monkeypatch):
    db = CollectingDb()
    repo = AgentRepository(db)
    legacy_prompt = (
        "保留的自定义前言\n"
        "1. 接到研究任务后，先读取 `deep-research` 技能（read_file 其 SKILL.md）获取完整方法论，并严格据此执行。\n"
        "保留的自定义结尾"
    )
    deep_research = SimpleNamespace(
        slug=DEEP_RESEARCH_AGENT_SLUG,
        config_json={
            "context": {
                "skills": [DEEP_RESEARCH_AGENT_SLUG, "knowledge-base"],
                "system_prompt": legacy_prompt,
                "model": "provider:model",
            },
            "custom": True,
        },
    )

    async def get_by_slug(slug):
        if slug == DEEP_RESEARCH_AGENT_SLUG:
            return deep_research
        return SimpleNamespace(slug=slug, config_json={"context": {}})

    updated = {}

    async def update(agent, **kwargs):
        updated.update(kwargs)
        agent.config_json = kwargs["config_json"]
        return agent

    monkeypatch.setattr(repo, "get_by_slug", get_by_slug)
    monkeypatch.setattr(repo, "update", update)

    await repo.ensure_deep_research_agents(created_by="system")

    assert updated["updated_by"] == "system"
    assert updated["config_json"]["custom"] is True
    context = updated["config_json"]["context"]
    assert context["skills"] == ["knowledge-base"]
    assert context["model"] == "provider:model"
    assert "保留的自定义前言" in context["system_prompt"]
    assert "读取 `deep-research` 技能" not in context["system_prompt"]
