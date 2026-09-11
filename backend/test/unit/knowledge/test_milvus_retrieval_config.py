from unittest.mock import AsyncMock

from yuxi.knowledge.graphs.milvus_graph_service import MilvusGraphService
from yuxi.knowledge.implementations.milvus import MilvusKB, _retrieval_config_options


def test_milvus_retrieval_config_exposes_graph_and_dependencies():
    options = _retrieval_config_options()
    by_key = {option["key"]: option for option in options}

    assert by_key["use_graph_retrieval"]["default"] is False
    assert by_key["graph_max_nodes"]["default"] == 10000
    assert by_key["graph_max_nodes"]["depend_on"] == ("use_graph_retrieval", True)
    assert by_key["graph_top_k"]["depend_on"] == ("use_graph_retrieval", True)
    assert by_key["reranker_model"]["depend_on"] == ("use_reranker", True)


def test_reranker_model_select_offers_follow_global_default(monkeypatch):
    from yuxi.knowledge.implementations import milvus as milvus_module
    from yuxi.models.providers.cache import ModelInfo

    def fake_rerank_models(_model_type):
        return [
            ModelInfo("siliconflow-cn", "global-rerank", "rerank", "全局重排", "", "", "openai"),
            ModelInfo("siliconflow-cn", "other-rerank", "rerank", "其它重排", "", "", "openai"),
        ]

    monkeypatch.setattr(milvus_module.model_cache, "get_all_specs", fake_rerank_models)
    monkeypatch.setattr(milvus_module, "resolve_reranker_model", lambda spec=None: "siliconflow-cn:global-rerank")

    reranker = next(o for o in milvus_module._retrieval_config_options() if o["key"] == "reranker_model")

    assert reranker["default"] == ""
    assert reranker["options"][0] == {"value": "", "label": "跟随全局默认（全局重排）"}
    assert [o["value"] for o in reranker["options"][1:]] == [
        "siliconflow-cn:global-rerank",
        "siliconflow-cn:other-rerank",
    ]


async def test_new_milvus_database_persists_enterprise_retrieval_strategy(tmp_path):
    kb = object.__new__(MilvusKB)
    kb.work_dir = str(tmp_path)
    kb.databases_meta = {}
    kb._persist_kb = AsyncMock()

    created = await kb.create_database("Enterprise KB", "Enterprise retrieval defaults")

    expected = kb._get_initial_query_params(created["kb_id"])
    assert created["query_params"] == expected
    kb._persist_kb.assert_awaited_once_with(created["kb_id"], record_fields=None)


def test_new_milvus_database_uses_enterprise_retrieval_strategy():
    kb = object.__new__(MilvusKB)

    defaults = kb._get_default_query_params("kb_existing")
    initial = kb._get_initial_query_params("kb_new")

    assert defaults["options"]["search_mode"] == "vector"
    assert defaults["options"]["use_graph_retrieval"] is False
    assert defaults["options"]["use_reranker"] is False
    assert initial["options"] == {
        **defaults["options"],
        "search_mode": "hybrid",
        "final_top_k": 10,
        "similarity_threshold": 0.2,
        "bm25_top_k": 50,
        "vector_weight": 0.7,
        "bm25_weight": 0.3,
        "bm25_drop_ratio_search": 0.1,
        "use_graph_retrieval": True,
        "graph_entity_top_k": 10,
        "graph_triple_top_k": 20,
        "graph_max_nodes": 5000,
        "graph_top_k": 20,
        "graph_weight": 0.5,
        "ppr_damping": 0.85,
        "use_reranker": True,
        "recall_top_k": 50,
    }
    assert initial["options"]["reranker_model"] == ""
    assert initial["options"]["include_distances"] is True


def test_graph_ppr_ranks_chunk_nodes_from_seed_entities():
    subgraph = {
        "nodes": [
            {"id": "e1", "type": "Entity", "properties": {"entity_id": "seed"}},
            {"id": "c1", "type": "Chunk", "properties": {"chunk_id": "chunk_a"}},
            {"id": "e2", "type": "Entity", "properties": {"entity_id": "other"}},
            {"id": "c2", "type": "Chunk", "properties": {"chunk_id": "chunk_b"}},
        ],
        "edges": [
            {"source_id": "e1", "target_id": "c1"},
            {"source_id": "e1", "target_id": "e2"},
            {"source_id": "e2", "target_id": "c2"},
        ],
    }

    ranked = MilvusGraphService.rank_chunks_by_ppr(subgraph, {"seed": 1.0}, top_k=2, damping=0.85)

    assert [chunk_id for chunk_id, _ in ranked] == ["chunk_a", "chunk_b"]


def test_rrf_fusion_merges_chunk_and_graph_rankings():
    kb = object.__new__(MilvusKB)
    base_chunks = [
        {"content": "base a", "metadata": {"chunk_id": "a"}, "score": 0.9},
        {"content": "base b", "metadata": {"chunk_id": "b"}, "score": 0.8},
    ]
    graph_chunks = [
        {"content": "graph b", "metadata": {"chunk_id": "b"}, "score": 0.7, "graph_score": 0.7},
        {"content": "graph c", "metadata": {"chunk_id": "c"}, "score": 0.6, "graph_score": 0.6},
    ]

    fused = kb._fuse_chunk_rankings(base_chunks, graph_chunks, graph_weight=1.0)

    assert [chunk["metadata"]["chunk_id"] for chunk in fused] == ["b", "a", "c"]
    assert fused[0]["graph_score"] == 0.7
    assert fused[0]["fusion_sources"] == ["chunk", "graph"]
    # 融合分只用于本次排序，不得回写 score：全库检索按 score（有界相似度/加权分）判断相关性，
    # 回写成 RRF 排名量级会让相关性下限把全部命中过滤掉。
    assert [chunk["score"] for chunk in fused] == [0.8, 0.9, 0.6]


def test_rrf_fusion_keeps_chunk_without_chunk_id():
    """缺 chunk_id 的片段退回「文件+片内序号」作为键，不能只在开启图检索时凭空消失；
    连 file_id/chunk_index 都没有的片段无从合并，只能丢弃。"""
    kb = object.__new__(MilvusKB)
    base_chunks = [
        {"content": "no id", "metadata": {"chunk_id": None, "file_id": "f1", "chunk_index": 3}, "score": 0.9},
        {"content": "no identity", "score": 0.7},
    ]

    fused = kb._fuse_chunk_rankings(base_chunks, [], graph_weight=0.5)

    assert [chunk["content"] for chunk in fused] == ["no id"]
    assert fused[0]["fusion_sources"] == ["chunk"]
