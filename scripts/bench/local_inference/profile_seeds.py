"""分析 _build_graph_seed_weights 返回的 seed 权重分布。
在 api-dev 容器内运行：python -u /tmp/profile_seeds.py
"""
import asyncio

from yuxi.knowledge.runtime import knowledge_base
from yuxi.repositories.knowledge_base_repository import KnowledgeBaseRepository
import yuxi.knowledge.implementations.milvus as milvus_mod


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

    captured: list[dict] = []
    orig = milvus_mod.MilvusKB._build_graph_seed_weights

    async def wrapper(self, *a, **kw):
        sw = await orig(self, *a, **kw)
        captured.append(sw)
        return sw

    milvus_mod.MilvusKB._build_graph_seed_weights = wrapper
    await knowledge_base.aquery(
        query, kb_id=kb_id, final_top_k=10,
        use_reranker=False, use_graph_retrieval=True, search_mode="hybrid",
    )
    milvus_mod.MilvusKB._build_graph_seed_weights = orig

    if not captured:
        print("no seeds captured", flush=True)
        return
    sw = captured[0]
    items = sorted(sw.items(), key=lambda kv: kv[1], reverse=True)
    n = len(items)
    total = sum(v for _, v in items)
    print(f"seed count={n} total_weight={total:.4f}", flush=True)
    cum = 0.0
    for i, (eid, w) in enumerate(items):
        cum += w
        if i < 10 or i in (19, 29, 49, 99, 199, n - 1):
            pct = cum / total * 100
            print(f"  top{i+1}: w={w:.5f} cum={cum:.5f} ({pct:.1f}%)", flush=True)
    # top-N 覆盖
    for top in (5, 10, 20, 30, 50, 100):
        cum_top = sum(w for _, w in items[:top])
        print(f"top {top}: {cum_top/total*100:.1f}% of weight", flush=True)


asyncio.run(main())
