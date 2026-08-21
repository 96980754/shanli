"""图谱检索查询级 embedding 缓存单测。

同一查询的实体 / 三元组 / 审核断言三路子检索应共享一次外部 embedding，
避免对同一句 query 重复调用外部 API（见 MilvusGraphVectorStore._embed_query_text）。
"""

from __future__ import annotations

import asyncio

from yuxi.knowledge.graphs.milvus_graph_vector_store import MilvusGraphVectorStore


class CountingEmbed:
    """记录调用次数的假 embedding 函数。"""

    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        return [[0.1] * 4 for _ in texts]


def _make_store(embed: CountingEmbed) -> MilvusGraphVectorStore:
    # 跳过 __init__（避免连接真实 Milvus），只构造缓存所需的最小状态。
    store = object.__new__(MilvusGraphVectorStore)
    store._query_embedding_tasks = {}
    store._get_embedding_function = lambda spec: embed
    return store


async def test_concurrent_searches_share_one_embedding() -> None:
    """同一 query 并发多路检索只调用一次 embedding，且结果一致。"""
    embed = CountingEmbed()
    store = _make_store(embed)

    results = await asyncio.gather(
        store._embed_query_text("同一句问题", "spec-a"),
        store._embed_query_text("同一句问题", "spec-a"),
        store._embed_query_text("同一句问题", "spec-a"),
    )

    assert embed.calls == 1
    assert len(results) == 3
    assert all(r == results[0] for r in results)


async def test_different_query_or_model_not_cached_together() -> None:
    """不同 query 或不同 embedding 模型不共用缓存。"""
    embed = CountingEmbed()
    store = _make_store(embed)

    await store._embed_query_text("问题A", "spec-a")
    await store._embed_query_text("问题A", "spec-b")
    await store._embed_query_text("问题B", "spec-a")

    assert embed.calls == 3


async def test_seeded_query_embedding_reused_without_embedding_call() -> None:
    """主检索注入的 query embedding 被直接复用，三路子检索不再调用外部 embedding。

    对应 milvus._retrieve_graph_chunks：主检索（vector/hybrid 分支）已算好
    query embedding 后，通过 seed_query_embedding 注入图谱缓存。
    """
    embed = CountingEmbed()
    store = _make_store(embed)
    seeded = [[0.9] * 4]

    store.seed_query_embedding("问题A", "spec-a", seeded)
    result = await store._embed_query_text("问题A", "spec-a")

    assert embed.calls == 0
    assert result == seeded

