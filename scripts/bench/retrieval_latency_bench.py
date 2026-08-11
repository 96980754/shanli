"""检索耗时压测：单库 + 全库并发，分阶段打点。

用法: python latency_bench.py <kb_id|all> [rounds]
"""
import argparse
import asyncio
import time

DELIVERED = ["kb_3cm2gz6tyb", "kb_mvng8u1201", "kb_0368jjmecb", "kb_fhhcq7kf8a", "kb_2ncrgy5nr1", "kb_abeqi4880k"]
STAGES = ["embedding", "milvus_search", "graph_vector", "graph_neo4j_ppr", "postgres", "hydrate", "rerank"]


def _timer():
    return {name: [0.0, 0] for name in STAGES + ["other"]}


def _wrap_async(stage, acc, fn):
    async def wrapper(*a, **kw):
        t0 = time.perf_counter()
        try:
            return await fn(*a, **kw)
        finally:
            acc[stage][0] += time.perf_counter() - t0
            acc[stage][1] += 1
    return wrapper


def _patch(acc):
    import yuxi.knowledge.implementations.milvus as milvus_mod
    from yuxi.knowledge.graphs.milvus_graph_service import MilvusGraphService
    from yuxi.knowledge.graphs.milvus_graph_vector_store import MilvusGraphVectorStore
    from yuxi.models.rerank import BaseReranker
    from yuxi.repositories.knowledge_chunk_repository import KnowledgeChunkRepository
    from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository

    orig_io = milvus_mod._run_milvus_query_io

    def _classify(func):
        inner = getattr(func, "func", func)
        name = getattr(inner, "__name__", "")
        return "embedding" if ("encode" in name or "embed" in name) else "milvus_search"

    async def timed_io(func, /, *args, **kwargs):
        t0 = time.perf_counter()
        stage = _classify(func)
        try:
            return await orig_io(func, *args, **kwargs)
        finally:
            acc[stage][0] += time.perf_counter() - t0
            acc[stage][1] += 1

    milvus_mod._run_milvus_query_io = timed_io
    MilvusGraphService.query_and_rank_chunks_by_ppr = _wrap_async("graph_neo4j_ppr", acc,
                                                                  MilvusGraphService.query_and_rank_chunks_by_ppr)
    for m in ("search_entities", "search_triples"):
        setattr(MilvusGraphVectorStore, m, _wrap_async("graph_vector", acc, getattr(MilvusGraphVectorStore, m)))
    BaseReranker.acompute_score = _wrap_async("rerank", acc, BaseReranker.acompute_score)
    for repo, methods in (
        (KnowledgeFileRepository, ("list_current_file_ids",)),
        (KnowledgeChunkRepository, ("list_by_chunk_ids",)),
    ):
        for m in methods:
            setattr(repo, m, _wrap_async("postgres", acc, getattr(repo, m)))


async def preload_instances():
    """等价于真实服务启动时的 metadata 加载。"""
    from yuxi.knowledge.runtime import knowledge_base

    def qkwargs(extra=None):
        kw = dict(final_top_k=10)
        if args.no_rerank:
            kw.update(use_reranker=False, use_graph_retrieval=True, search_mode="hybrid")
        if extra:
            kw.update(extra)
        return kw
    from yuxi.repositories.knowledge_base_repository import KnowledgeBaseRepository

    kb_repo = KnowledgeBaseRepository()
    rows = await kb_repo.get_all()
    for row in rows:
        if row.kb_type not in {"milvus", None}:
            continue
        inst = knowledge_base._get_or_create_kb_instance(row.kb_type or "milvus")
        if not getattr(inst, "_metadata_loaded", False):
            await inst._load_metadata()
    print(f"[init] 已加载 {len(rows)} 个库的元数据", flush=True)


def _report(acc, times, label):
    total_sum = sum(times)
    print(f"  --- {label}: rounds={len(times)} min={min(times)*1000:.0f}ms "
          f"avg={sum(times)/len(times)*1000:.0f}ms max={max(times)*1000:.0f}ms ---", flush=True)
    for name in STAGES + ["other"]:
        ms, n = acc[name][0] * 1000, acc[name][1]
        if n:
            print(f"  {name:<16} {ms/len(times):>7.0f}ms/query ({ms/(total_sum*1000)*100:>5.1f}%) calls={n}", flush=True)


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("target", choices=["all"] + DELIVERED)
    parser.add_argument("rounds", type=int, default=3)
    parser.add_argument("--kb", action="append", default=None)
    parser.add_argument("--no-rerank", action="store_true", help="use_reranker=False（本地融合排序，替代外部 Rerank API）")
    args = parser.parse_args()

    from yuxi.knowledge.runtime import knowledge_base

    def qkwargs(extra=None):
        kw = dict(final_top_k=10)
        if args.no_rerank:
            kw.update(use_reranker=False, use_graph_retrieval=True, search_mode="hybrid")
        if extra:
            kw.update(extra)
        return kw

    queries = [
        "F10定位对讲一体机的技术规格和防护等级",
        "设备支持哪些通信协议和频率",
        "产品的防水等级、定位精度和待机时间",
        "系统有哪些核心功能模块",
    ]

    if args.target == "all":
        kbs = args.kb or DELIVERED
        acc = _timer()
        _patch(acc)
        await preload_instances()
        print(f"=== 全库并发检索（{len(kbs)} 库）× {args.rounds} 轮{'  [本地融合排序]' if args.no_rerank else ''} ===", flush=True)
        times = []
        for r in range(args.rounds):
            t0 = time.perf_counter()
            grouped = await asyncio.gather(*(knowledge_base.aquery(queries[r % len(queries)], kb_id=k, **qkwargs())
                                             for k in kbs), return_exceptions=True)
            total = time.perf_counter() - t0
            ok = [g for g in grouped if not isinstance(g, Exception)]
            errs = [g for g in grouped if isinstance(g, Exception)]
            n = sum(len(g) for g in ok)
            times.append(total)
            print(f"  round{r+1}: {total*1000:.0f}ms results={n} ok={len(ok)}/{len(kbs)} errs={len(errs)}", flush=True)
            if errs:
                for e in errs[:2]:
                    print(f"    err: {type(e).__name__}: {str(e)[:120]}", flush=True)
        _report(acc, times, "并发总耗时")
        return

    kb_id = args.target
    acc = _timer()
    _patch(acc)
    await preload_instances()
    print(f"=== 单库 {kb_id} × {args.rounds} 轮{'  [本地融合排序]' if args.no_rerank else ''} ===", flush=True)
    times = []
    for r in range(args.rounds):
        q = queries[r % len(queries)]
        t0 = time.perf_counter()
        res = await knowledge_base.aquery(q, kb_id=kb_id, **qkwargs())
        total = time.perf_counter() - t0
        times.append(total)
        print(f"  q={q[:18]!r} r{r+1}: {total*1000:.0f}ms results={len(res)}", flush=True)
    _report(acc, times, "单库耗时")


asyncio.run(main())
