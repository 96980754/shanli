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
        # keyword 模式：bm25_score 无上界，按库内 min-max 归一到 [0,1] 后参与排序，不能被下限误杀
        return [{"content": "b25", "metadata": {"file_id": "f3"}, "score": 12.5, "bm25_score": 12.5}]

    monkeypatch.setattr("yuxi.services.global_knowledge_search_service.knowledge_base.get_databases_by_uid", databases)
    monkeypatch.setattr("yuxi.services.global_knowledge_search_service.knowledge_base.aquery", query)
    async def noop_enrich(items):
        return None

    monkeypatch.setattr(GlobalKnowledgeSearchService, "_enrich_file_paths", staticmethod(noop_enrich))

    result = await service.search(SimpleNamespace(uid="u1", role="user", department_id=None), "question", limit=10)

    # low(0.30) 低于全局下限被过滤；high(0.72) 沿用相似度；
    # b25 该库只有一条候选（无区分度），按该库最优候选归一为 1.0——这是"库内归一化"的必然结果：
    # 每个库的最优候选都会落在 1.0，跨库比较由"库内相对好坏"决定，不再由量纲决定。
    assert [item["content"] for item in result] == ["b25", "high"]
    assert [item["global_score"] for item in result] == [1.0, 0.72]


@pytest.mark.asyncio
async def test_global_search_normalizes_keyword_scores_within_each_kb(monkeypatch):
    """keyword 库的 bm25 分数无上界，库内归一化后必须保留库内次序、且不再恒沉底。"""
    service = GlobalKnowledgeSearchService(permission_service=SimpleNamespace())
    _always_allowed(service.permission_service)

    async def databases(uid):
        return {"databases": [{"kb_id": "kb_vec", "name": "V"}, {"kb_id": "kb_kw", "name": "K"}]}

    async def query(query, kb_id, **kwargs):
        if kb_id == "kb_vec":
            return [{"content": "vec", "metadata": {}, "score": 0.5}]
        return [
            {"content": "kw-1", "metadata": {}, "score": 12.5, "bm25_score": 12.5},
            {"content": "kw-2", "metadata": {}, "score": 8.0, "bm25_score": 8.0},
        ]

    monkeypatch.setattr("yuxi.services.global_knowledge_search_service.knowledge_base.get_databases_by_uid", databases)
    monkeypatch.setattr("yuxi.services.global_knowledge_search_service.knowledge_base.aquery", query)

    result = await service.search(SimpleNamespace(uid="u1", role="user", department_id=None), "question", limit=10)

    # kw-1(12.5→1.0) > vec(0.5) > kw-2(8.0→0.0)：库内相对次序保留，且不再因量级太小被截断
    assert [item["content"] for item in result] == ["kw-1", "vec", "kw-2"]
    assert [item["global_score"] for item in result] == [1.0, 0.5, 0.0]


@pytest.mark.asyncio
async def test_global_search_normalizes_scoreless_results_by_rank(monkeypatch):
    """没有 score 字段的结果（自定义后端）无从 min-max，按库内排名取相对位置，不能被下限误杀。"""
    service = GlobalKnowledgeSearchService(permission_service=SimpleNamespace())
    _always_allowed(service.permission_service)

    async def databases(uid):
        return {"databases": [{"kb_id": "kb_custom", "name": "C"}]}

    async def query(query, kb_id, **kwargs):
        return [{"content": name, "metadata": {}} for name in ["first", "second", "third"]]

    monkeypatch.setattr("yuxi.services.global_knowledge_search_service.knowledge_base.get_databases_by_uid", databases)
    monkeypatch.setattr("yuxi.services.global_knowledge_search_service.knowledge_base.aquery", query)

    result = await service.search(SimpleNamespace(uid="u1", role="user", department_id=None), "question", limit=10)

    assert [item["content"] for item in result] == ["first", "second", "third"]
    assert [round(item["global_score"], 4) for item in result] == [1.0, 0.6667, 0.3333]


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
