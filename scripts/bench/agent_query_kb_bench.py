"""代理侧 query_kb 工具检索耗时压测：复现聊天智能体调用 query_kb 时服务端的真实耗时。

与线上链路一致性：
  query_kb 工具（agents/toolkits/kbs/tools.py）的实际执行 = knowledge_base.get_retrievers()
  → retriever(query_text) = aquery(...) + build_search_output(...)。本脚本直接走这条路径，
  并复用 retrieval_latency_bench 的 monkey-patch 对内部阶段打点（embedding / milvus /
  graph / rerank / postgres），因此测得的就是"前端 query_kb 工具卡上服务端那部分"。

用法（api-dev 容器内，uv run 依赖）:
  docker exec -w /app api-dev uv run --no-sync python -u /app/scripts/bench/agent_query_kb_bench.py --rounds 5
  # 只看某一库
  ... --kb kb_mvng8u1201 --rounds 5

输出：每库冷启动 / 温热均值 / min-avg-max，以及分阶段耗时占比。
"""
from __future__ import annotations

import argparse
import asyncio
import time

# default-chatbot 绑定的 3 个"资料"库（交付库）
BOUND_KBS = ["kb_mvng8u1201", "kb_3cm2gz6tyb", "kb_0368jjmecb"]
STAGES = ["embedding", "milvus_search", "graph_vector", "graph_neo4j_ppr", "postgres", "hydrate", "rerank"]

QUERY = "POCSTARS MDM 平台有哪些核心功能？请结合知识库说明。"


def _timer() -> dict[str, list[float]]:
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
    """对检索链路内部函数打点，复刻 retrieval_latency_bench._patch。"""
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
    MilvusGraphService.query_and_rank_chunks_by_ppr = _wrap_async(
        "graph_neo4j_ppr", acc, MilvusGraphService.query_and_rank_chunks_by_ppr
    )
    for m in ("search_entities", "search_triples"):
        setattr(MilvusGraphVectorStore, m, _wrap_async("graph_vector", acc, getattr(MilvusGraphVectorStore, m)))
    BaseReranker.acompute_score = _wrap_async("rerank", acc, BaseReranker.acompute_score)
    for repo, methods in (
        (KnowledgeFileRepository, ("list_current_file_ids",)),
        (KnowledgeChunkRepository, ("list_by_chunk_ids",)),
    ):
        for m in methods:
            setattr(repo, m, _wrap_async("postgres", acc, getattr(repo, m)))


async def preload_instances() -> None:
    """等价于真实服务启动时的 metadata 加载（worker 每次启动会走同一路径）。"""
    from yuxi.knowledge.runtime import knowledge_base
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


def _report(acc, times, kb_id):
    total = sum(times)
    print(f"  --- {kb_id}: rounds={len(times)} min={min(times)*1000:.0f}ms "
          f"avg={sum(times)/len(times)*1000:.0f}ms max={max(times)*1000:.0f}ms ---", flush=True)
    for name in STAGES + ["other"]:
        ms, n = acc[name][0] * 1000, acc[name][1]
        if n:
            print(f"    {name:<16} {ms/len(times):>7.0f}ms/query ({ms/(total*1000)*100:>5.1f}%) calls={n}", flush=True)


async def main() -> None:
    parser = argparse.ArgumentParser(description="代理侧 query_kb 检索耗时压测")
    parser.add_argument("--kb", action="append", default=None, help="要压测的 kb_id，可多次；默认 3 个绑定库")
    parser.add_argument("--rounds", type=int, default=5, help="每库轮数（首轮为冷启动，其余为温热）")
    parser.add_argument("--query", default=QUERY, help="压测问题")
    args = parser.parse_args()

    from yuxi.knowledge.runtime import knowledge_base

    kbs = args.kb or BOUND_KBS
    for kb_id in kbs:
        acc = _timer()
        _patch(acc)
        await preload_instances()
        retrievers = knowledge_base.get_retrievers()
        if kb_id not in retrievers:
            print(f"[skip] {kb_id} 无检索器（未加载？）", flush=True)
            continue
        retriever = retrievers[kb_id]["retriever"]
        print(f"=== query_kb 服务端耗时 · {kb_id}（{retrievers[kb_id]['name']}）× {args.rounds} 轮 ===", flush=True)
        times: list[float] = []
        for i in range(args.rounds):
            t0 = time.perf_counter()
            out = await retriever(args.query)
            dt = time.perf_counter() - t0
            times.append(dt)
            n = len(out.get("results", []))
            tag = "冷启动" if i == 0 else "温热"
            print(f"  r{i+1} [{tag}] {dt*1000:.0f}ms results={n}", flush=True)
        _report(acc, times, kb_id)


if __name__ == "__main__":
    asyncio.run(main())
