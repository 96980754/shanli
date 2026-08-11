"""固定查询重复 N 轮，统计 p50/p95/p99 + 标准差（比轮换查询更严谨的延迟分布）。

用法:
  python latency_stats.py single-rerank 10
  python latency_stats.py all-local 10
"""
import argparse
import asyncio
import statistics
import time

DELIVERED = ["kb_3cm2gz6tyb", "kb_mvng8u1201", "kb_0368jjmecb", "kb_fhhcq7kf8a", "kb_2ncrgy5nr1", "kb_abeqi4880k"]
QUERY = "F10定位对讲一体机的技术规格和防护等级"
SINGLE_KB = "kb_2ncrgy5nr1"


def percentile(data, p):
    data = sorted(data)
    if not data:
        return 0.0
    k = (len(data) - 1) * (p / 100.0)
    f = int(k)
    c = f + 1 if f + 1 < len(data) else f
    return data[f] + (data[c] - data[f]) * (k - f)


def report(name, times):
    ms = [t * 1000 for t in times]
    print(f"=== {name} === rounds={len(ms)}", flush=True)
    print(f"  原始数据(ms): " + " ".join(f"{v:.0f}" for v in ms), flush=True)
    print(f"  min={min(ms):.0f}  avg={statistics.mean(ms):.0f}  max={max(ms):.0f}", flush=True)
    print(f"  p50={percentile(ms, 50):.0f}  p90={percentile(ms, 90):.0f}  "
          f"p95={percentile(ms, 95):.0f}  p99={percentile(ms, 99):.0f}", flush=True)
    print(f"  std={statistics.stdev(ms):.0f}ms", flush=True)


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("scenario", choices=["single-rerank", "single-local", "all-rerank", "all-local",
                                             "single-config", "all-config"])
    parser.add_argument("rounds", type=int)
    parser.add_argument("--kb", action="append", default=None, help="指定参与压测的 kb_id，默认 6 个交付库")
    parser.add_argument("--single", action="store_true", help="单库模式（配合 --kb 指定具体库）")
    args = parser.parse_args()

    from yuxi.knowledge.runtime import knowledge_base
    from yuxi.repositories.knowledge_base_repository import KnowledgeBaseRepository

    inst = knowledge_base._get_or_create_kb_instance("milvus")
    if not getattr(inst, "_metadata_loaded", False):
        await inst._load_metadata()

    kw = dict(final_top_k=10, use_reranker=True, use_graph_retrieval=True, search_mode="hybrid")
    if args.scenario.endswith("-local"):
        kw["use_reranker"] = False
    if args.scenario.endswith("-config"):
        # 按各库存储的 query_params 运行（不强制覆盖模式），如实反映当前配置
        kw = dict(final_top_k=10)
    kbs = (args.kb or DELIVERED) if args.scenario.startswith("all") else ([args.kb[0]] if args.kb else [SINGLE_KB])

    times = []
    for r in range(1, args.rounds + 1):
        t0 = time.perf_counter()
        if len(kbs) > 1:
            await asyncio.gather(*(knowledge_base.aquery(QUERY, kb_id=k, **kw) for k in kbs))
        else:
            await knowledge_base.aquery(QUERY, kb_id=kbs[0], **kw)
        dt = time.perf_counter() - t0
        times.append(dt)
        print(f"  round{r}: {dt*1000:.0f}ms", flush=True)
    scope = f"[{len(kbs)}库:{','.join(kbs)}]" if len(kbs) > 1 else f"[{kbs[0]}]"
    report(f"{args.scenario} {scope} [{QUERY}]", times)


asyncio.run(main())
