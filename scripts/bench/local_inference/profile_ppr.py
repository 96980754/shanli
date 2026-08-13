"""拆解 graph_neo4j_ppr 的 465ms：Neo4j 子图查询 vs igraph PPR。
在 api-dev 容器内运行：python -u /tmp/profile_ppr.py
"""
import asyncio
import time

from yuxi.knowledge.graphs.milvus_graph_service import MilvusGraphService
from yuxi.knowledge.runtime import knowledge_base
from yuxi.repositories.knowledge_base_repository import KnowledgeBaseRepository


async def preload() -> None:
    rows = await KnowledgeBaseRepository().get_all()
    for row in rows:
        if row.kb_type not in {"milvus", None}:
            continue
        inst = knowledge_base._get_or_create_kb_instance(row.kb_type or "milvus")
        if not getattr(inst, "_metadata_loaded", False):
            await inst._load_metadata()


async def main() -> None:
    kb_id = "kb_3cm2gz6tyb"
    query = "F10定位对讲一体机的技术规格和防护等级"
    await preload()

    svc = MilvusGraphService()
    orig_query = MilvusGraphService.query_seed_subgraph
    orig_rank = MilvusGraphService.rank_chunks_by_ppr

    t_sub = {"ms": 0.0, "n": 0}
    t_ig = {"ms": 0.0, "n": 0}
    nodes_seen = []

    async def timed_query(self, *a, **kw):
        t0 = time.perf_counter()
        eids = kw.get("entity_ids") or []
        if "seed_counts" not in globals():
            seed_counts = []
            globals()["seed_counts"] = seed_counts
        globals()["seed_counts"].append(len(eids))
        r = await orig_query(self, *a, **kw)
        t_sub["ms"] += (time.perf_counter() - t0) * 1000
        t_sub["n"] += 1
        nodes_seen.append(len(r.get("nodes") or []))
        return r

    def timed_rank(*a, **kw):
        t0 = time.perf_counter()
        r = orig_rank(*a, **kw)
        t_ig["ms"] += (time.perf_counter() - t0) * 1000
        t_ig["n"] += 1
        return r

    MilvusGraphService.query_seed_subgraph = timed_query
    MilvusGraphService.rank_chunks_by_ppr = staticmethod(timed_rank)

    for _ in range(3):
        await knowledge_base.aquery(
            query,
            kb_id=kb_id,
            final_top_k=10,
            use_reranker=False,
            use_graph_retrieval=True,
            search_mode="hybrid",
        )

    svc.query_seed_subgraph = orig_query
    MilvusGraphService.rank_chunks_by_ppr = staticmethod(orig_rank)

    avg_sub = t_sub["ms"] / max(t_sub["n"], 1)
    avg_ig = t_ig["ms"] / max(t_ig["n"], 1)
    print(f"seed_counts={globals().get('seed_counts')}", flush=True)
    print(f"query_seed_subgraph(Neo4j): {t_sub['ms']:.0f}ms over {t_sub['n']} calls -> avg {avg_sub:.0f}ms, nodes={nodes_seen}")
    print(f"rank_chunks_by_ppr(igraph): {t_ig['ms']:.0f}ms over {t_ig['n']} calls -> avg {avg_ig:.0f}ms")


asyncio.run(main())
