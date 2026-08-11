"""诊断：关闭 rerank 后纯 Milvus 检索耗时（证明瓶颈归属）。"""
import asyncio, time

QUERY = "F10定位对讲一体机的技术规格和防护等级"

async def probe(kb_id, mode):
    from yuxi.knowledge.runtime import knowledge_base
    from yuxi.repositories.knowledge_base_repository import KnowledgeBaseRepository
    rows = await KnowledgeBaseRepository().get_all()
    inst = knowledge_base._get_or_create_kb_instance("milvus")
    if not getattr(inst, "_metadata_loaded", False):
        await inst._load_metadata()
    t0 = time.perf_counter()
    res = await knowledge_base.aquery(
        QUERY, kb_id=kb_id, final_top_k=10,
        use_reranker=False, use_graph_retrieval=False,
        search_mode=mode,
    )
    dt = time.perf_counter() - t0
    print(f"  {kb_id} mode={mode:8s} {dt*1000:6.0f}ms results={len(res)}", flush=True)

async def main():
    print("=== 纯 Milvus 检索（无 rerank、无 graph、无外部 embedding 重算）===", flush=True)
    print("--- vector 模式（一次外部 embedding，仅 query 向量）---", flush=True)
    for kb in ["kb_2ncrgy5nr1", "kb_fhhcq7kf8a"]:
        await probe(kb, "vector")
    print("--- hybrid 模式（外部 embedding + Milvus 向量+BM25）---", flush=True)
    for kb in ["kb_2ncrgy5nr1"]:
        await probe(kb, "hybrid")

asyncio.run(main())
