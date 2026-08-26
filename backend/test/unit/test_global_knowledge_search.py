from types import SimpleNamespace

import pytest

from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository
from yuxi.services.global_knowledge_search_service import GlobalKnowledgeSearchService


def _always_allowed(permission_service):
    permission_service.has_permission = lambda context, kb_id, action: __import__("asyncio").sleep(0, True)


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


@pytest.mark.asyncio
async def test_global_search_filters_low_vector_score_and_sorts_by_score(monkeypatch):
    service = GlobalKnowledgeSearchService(permission_service=SimpleNamespace())
    _always_allowed(service.permission_service)

    async def databases(uid):
        return {"databases": [{"kb_id": "kb_a", "name": "A"}, {"kb_id": "kb_b", "name": "B"}]}

    async def query(query, kb_id, **kwargs):
        if kb_id == "kb_a":
            # vector 模式：score 是有界相似度，0.30 低于全局下限应被过滤
            return [
                {"content": "low", "metadata": {"file_id": "f1"}, "score": 0.30},
                {"content": "high", "metadata": {"file_id": "f2"}, "score": 0.72},
            ]
        # keyword 模式：bm25_score 无上界，退回互惠排名融合，不能被下限误杀
        return [{"content": "b25", "metadata": {"file_id": "f3"}, "score": 12.5, "bm25_score": 12.5}]

    monkeypatch.setattr("yuxi.services.global_knowledge_search_service.knowledge_base.get_databases_by_uid", databases)
    monkeypatch.setattr("yuxi.services.global_knowledge_search_service.knowledge_base.aquery", query)
    async def noop_enrich(items):
        return None

    monkeypatch.setattr(GlobalKnowledgeSearchService, "_enrich_file_paths", staticmethod(noop_enrich))

    result = await service.search(SimpleNamespace(uid="u1", role="user", department_id=None), "question", limit=10)

    # high(0.72) 直接按相似度排最前；b25 走 RRF(1/61≈0.016)；low(0.30) 被过滤
    assert [item["content"] for item in result] == ["high", "b25"]
    assert result[0]["global_score"] == 0.72


@pytest.mark.asyncio
async def test_enrich_file_paths_adds_file_name_and_dir(monkeypatch):
    items = [
        {"content": "x", "metadata": {"file_id": "f2"}},
        {"content": "y", "metadata": {"file_id": "missing"}},
        {"content": "z"},
    ]

    async def fake_list_by_file_ids(self, file_ids):
        return [SimpleNamespace(file_id="f2", kb_id="kb1")]

    async def fake_build_paths(self, records):
        return {"f2": "poc资料/方案.docx"}

    monkeypatch.setattr(KnowledgeFileRepository, "list_by_file_ids", fake_list_by_file_ids)
    monkeypatch.setattr(KnowledgeFileRepository, "build_document_display_paths", fake_build_paths)

    await GlobalKnowledgeSearchService._enrich_file_paths(items)

    assert items[0]["file_dir"] == "poc资料"
    assert items[0]["file_name"] == "方案.docx"
    # 无记录/无 metadata 的结果保持原样，不注入空字段
    assert "file_dir" not in items[1]
    assert "file_name" not in items[1]
    assert "file_dir" not in items[2]
