from types import SimpleNamespace

import pytest

from yuxi.services.global_knowledge_search_service import GlobalKnowledgeSearchService


@pytest.mark.asyncio
async def test_global_search_only_queries_knowledge_bases_with_search_permission(monkeypatch):
    service = GlobalKnowledgeSearchService(permission_service=SimpleNamespace())
    service.permission_service.has_permission = lambda context, kb_id, action: __import__("asyncio").sleep(
        0, kb_id == "allowed"
    )

    async def databases(uid):
        return {"databases": [{"kb_id": "allowed", "name": "Allowed"}, {"kb_id": "denied", "name": "Denied"}]}

    queried = []

    async def query(query, kb_id, **kwargs):
        queried.append(kb_id)
        return [{"content": "answer", "file_name": "guide.md"}]

    monkeypatch.setattr("yuxi.services.global_knowledge_search_service.knowledge_base.get_databases_by_uid", databases)
    monkeypatch.setattr("yuxi.services.global_knowledge_search_service.knowledge_base.aquery", query)

    result = await service.search(SimpleNamespace(uid="u1", role="user", department_id=None), "question")

    assert queried == ["allowed"]
    assert result[0]["kb_name"] == "Allowed"


@pytest.mark.asyncio
async def test_global_search_returns_empty_for_blank_query():
    result = await GlobalKnowledgeSearchService().search(
        SimpleNamespace(uid="u1", role="user", department_id=None),
        "  ",
    )

    assert result == []
