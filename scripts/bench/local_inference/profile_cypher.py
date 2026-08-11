"""对照：旧 Cypher（全对象回传） vs 新 Cypher（投影标量）的 Neo4j 子图查询耗时。
在 api-dev 容器内运行：python -u /tmp/profile_cypher.py
"""
import asyncio
import time

from yuxi.knowledge.graphs.milvus_graph_service import MilvusGraphService
from yuxi.knowledge.runtime import knowledge_base
from yuxi.repositories.knowledge_base_repository import KnowledgeBaseRepository

CAPTURED: dict = {}


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
    label = "kb_3cm2gz6tyb"
    await preload()

    orig = MilvusGraphService.query_seed_subgraph

    async def capturer(self, kb_id_, **kw):
        CAPTURED["ids"] = kw.get("entity_ids") or []
        CAPTURED["max_nodes"] = kw.get("max_nodes") or 5000
        return await orig(self, kb_id_, **kw)

    MilvusGraphService.query_seed_subgraph = capturer
    await knowledge_base.aquery(
        query, kb_id=kb_id, final_top_k=10,
        use_reranker=False, use_graph_retrieval=True, search_mode="hybrid",
    )
    MilvusGraphService.query_seed_subgraph = orig

    eids = CAPTURED.get("ids") or []
    max_nodes = CAPTURED.get("max_nodes") or 5000
    print(f"real seed entities: n={len(eids)} {eids[:6]}", flush=True)
    if not eids:
        print("no seeds captured", flush=True)
        return

    old_cypher = f"""
    MATCH (seed:Entity:MilvusKB:`{label}`)
    WHERE seed.entity_id IN $entity_ids
    MATCH p = (seed)-[*1..2]-(n:MilvusKB:`{label}`)
    WITH p LIMIT $path_limit
    WITH collect(p) AS paths
    UNWIND paths AS node_path
    UNWIND nodes(node_path) AS node
    WITH paths, collect(DISTINCT node) AS graph_nodes
    UNWIND paths AS rel_path
    UNWIND relationships(rel_path) AS rel
    RETURN graph_nodes AS nodes, collect(DISTINCT rel) AS edges
    """
    new_cypher = f"""
    MATCH (seed:Entity:MilvusKB:`{label}`)
    WHERE seed.entity_id IN $entity_ids
    MATCH p = (seed)-[*1..2]-(n:MilvusKB:`{label}`)
    WITH p LIMIT $path_limit
    WITH collect(p) AS paths
    UNWIND paths AS node_path
    UNWIND nodes(node_path) AS node
    WITH paths, collect(DISTINCT node) AS graph_nodes
    UNWIND paths AS rel_path
    UNWIND relationships(rel_path) AS rel
    WITH collect(DISTINCT rel) AS graph_edges, graph_nodes
    RETURN
      [n IN graph_nodes | {{
        element_id: n.element_id, labels: labels(n),
        name: coalesce(n.name, n.content_preview, n.chunk_id, ''),
        chunk_id: n.chunk_id, entity_id: n.entity_id, label: n.label
      }}] AS nodes,
      [r IN graph_edges | {{
        id: r.element_id,
        source_id: startNode(r).element_id,
        target_id: endNode(r).element_id
      }}] AS edges
    """

    svc = MilvusGraphService()
    path_limit = max(max_nodes, 1) * 4
    for name, cypher in [("OLD", old_cypher), ("NEW", new_cypher)]:
        times, counts = [], []
        for i in range(3):
            t0 = time.perf_counter()
            with svc.driver.session() as s:
                rec = s.run(cypher, entity_ids=eids, path_limit=path_limit).single()
            dt = (time.perf_counter() - t0) * 1000
            n = len(rec.get("nodes")) if rec else 0
            e = len(rec.get("edges")) if rec else 0
            times.append(dt)
            counts.append((n, e))
        print(f"{name}: times={[int(t) for t in times]}ms  nodes/edges={counts[0]}", flush=True)


asyncio.run(main())
