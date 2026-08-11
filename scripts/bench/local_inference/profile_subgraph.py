"""拆解 query_seed_subgraph：Cypher 执行+数据传输 vs Python 反序列化。
在 api-dev 容器内运行：python -u /tmp/profile_subgraph.py
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


def main() -> None:
    kb_id = "kb_3cm2gz6tyb"
    label = "kb_3cm2gz6tyb"
    # 用真实查询会走到的 seed 实体：取图谱里与 chunk 相连的 5 个实体
    svc = MilvusGraphService()
    cypher = f"MATCH (:Chunk:MilvusKB:`{label}`)-[:MENTIONS]->(e:Entity:MilvusKB:`{label}`) RETURN e.entity_id AS id LIMIT 8"
    with svc.driver.session() as s:
        recs = s.run(cypher).data()
    eids = [r["id"] for r in recs]
    print(f"seed entity_ids: {eids}", flush=True)

    t_server = t_process = 0.0
    node_count = 0
    for _ in range(3):
        cypher_q = f"""
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
        t0 = time.perf_counter()
        with svc.driver.session() as s:
            record = s.run(
                cypher_q, entity_ids=eids, path_limit=max(5000, 1) * 4
            ).single()
        t1 = time.perf_counter()
        if not record:
            print("empty record", flush=True)
            continue
        processed = svc._process_subgraph_record(record, 5000, kb_id)
        t2 = time.perf_counter()
        t_server += (t1 - t0) * 1000
        t_process += (t2 - t1) * 1000
        node_count = len(processed.get("nodes") or [])
        print(f"  server(exec+transfer)={(t1-t0)*1000:.0f}ms  process={(t2-t1)*1000:.0f}ms nodes={node_count}", flush=True)

    print(f"avg: server={t_server/3:.0f}ms  process={t_process/3:.0f}ms  (nodes={node_count})", flush=True)


if __name__ == "__main__":
    asyncio.run(preload())
    main()
