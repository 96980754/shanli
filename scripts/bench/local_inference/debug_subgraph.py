"""对比 raw Cypher vs 产品方法 query_seed_subgraph 的返回。
在 api-dev 容器内运行：python -u /tmp/debug_subgraph.py
"""
import asyncio

from yuxi.knowledge.graphs.milvus_graph_service import MilvusGraphService
from yuxi.knowledge.runtime import knowledge_base
from yuxi.repositories.knowledge_base_repository import KnowledgeBaseRepository

CAP = {}


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

    orig = MilvusGraphService.query_seed_subgraph

    async def capturer(self, kb_id_, **kw):
        CAP["ids"] = kw.get("entity_ids") or []
        return await orig(self, kb_id_, **kw)

    MilvusGraphService.query_seed_subgraph = capturer
    await knowledge_base.aquery(
        query, kb_id=kb_id, final_top_k=10,
        use_reranker=False, use_graph_retrieval=True, search_mode="hybrid",
    )
    MilvusGraphService.query_seed_subgraph = orig

    eids = CAP["ids"]
    print(f"seeds={len(eids)}", flush=True)

    import time

    svc = MilvusGraphService()

    # 1) 直接调产品方法（完整：thread + session.run + transfer + _process）
    t0 = time.perf_counter()
    sub = await svc.query_seed_subgraph(kb_id, entity_ids=eids, max_nodes=5000)
    t1 = time.perf_counter()
    print(f"product method query_seed_subgraph: {(t1-t0)*1000:.0f}ms nodes={len(sub.get('nodes') or [])} edges={len(sub.get('edges') or [])}", flush=True)

    # 2) raw 新 Cypher（打印实际字符串）
    label = "kb_3cm2gz6tyb"
    cypher = f"""
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
    print(f"cypher matches file? {cypher == _read_cypher()}", flush=True)
    t0 = time.perf_counter()
    with svc.driver.session() as s:
        rec = s.run(cypher, entity_ids=eids, path_limit=5000 * 4).single()
    t1 = time.perf_counter()
    n = len(rec.get("nodes")) if rec else 0
    print(f"raw cypher: {(t1-t0)*1000:.0f}ms nodes={n} edges={len(rec.get('edges')) if rec else 0}", flush=True)

    # 3.5) 单独计时 _process_subgraph_record
    t0 = time.perf_counter()
    proc = svc._process_subgraph_record(rec, 5000, kb_id)
    t1 = time.perf_counter()
    print(f"_process_subgraph_record only: {(t1-t0)*1000:.0f}ms nodes={len(proc.get('nodes') or [])}", flush=True)

    # 3) 复现 _process_subgraph_record：直接对 raw record 归一化
    sample = rec.get("nodes")[0]
    norm = svc._normalize_node(sample, kb_id)
    print(f"first projected node sample: {sample}", flush=True)
    print(f"_normalize_node -> id={norm.get('id')} type={norm.get('type')} nid-in-props={norm.get('properties',{}).get('chunk_id')}", flush=True)

    proc = svc._process_subgraph_record(rec, 5000, kb_id)
    print(f"_process_subgraph_record(raw record): nodes={len(proc.get('nodes') or [])} edges={len(proc.get('edges') or [])}", flush=True)


def _read_cypher():
    import inspect
    import re
    src = inspect.getsource(MilvusGraphService.query_seed_subgraph)
    m = re.search(r'cypher = f"""(.*?)"""', src, re.S)
    return m.group(1) if m else "<not found>"


asyncio.run(main())
