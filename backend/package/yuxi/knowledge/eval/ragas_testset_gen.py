"""RAGAS 测试集生成（内部工具，不交付甲方）。

从知识库已入库的 chunks 用 RAGAS TestsetGenerator 自动生成 QA 测试集，
输出为平台评估可用的 JSONL（每行 {query, gold_answer, gold_chunk_ids?}）。

数据源完全来自系统入库数据：
- chunks：KnowledgeChunkRepository.list_by_kb_id
- 生成 LLM：复用评估链路 load_chat_model + LangchainLLMWrapper
- embedding：系统 select_embedding_model 经 LangchainEmbeddingsAdapter 包装

依赖隔离：与 ragas_eval.py 相同，所有 `import ragas` 均在函数内懒加载。
"""

from __future__ import annotations

from typing import Any

from yuxi.knowledge.eval._ragas_compat import _ensure_vertexai_stub
from yuxi.models.embed import select_embedding_model
from yuxi.repositories.knowledge_chunk_repository import KnowledgeChunkRepository
from yuxi.utils import logger


class LangchainEmbeddingsAdapter:
    """把系统 BaseEmbeddingModel 适配为 langchain Embeddings 接口。

    RAGAS TestsetGenerator 需要 LangchainEmbeddingsWrapper(Embeddings)，
    langchain Embeddings 接口做 duck-typing：embed_query / embed_documents
    及 async 变体。全部委托给系统模型的 encode / aencode。
    不子类化 langchain 基类，保持 ragas 懒加载。
    """

    def __init__(self, embed_model: Any) -> None:
        self._model = embed_model

    def embed_query(self, text: str) -> list[float]:
        return self._model.encode([text])[0]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._model.encode(texts)

    async def aembed_query(self, text: str) -> list[float]:
        return (await self._model.aencode([text]))[0]

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        return await self._model.aencode(texts)


def build_langchain_embeddings(model_spec: str) -> Any:
    """构建 ragas TestsetGenerator 需要的 langchain Embeddings 包装。"""
    from ragas.embeddings.base import LangchainEmbeddingsWrapper

    adapter = LangchainEmbeddingsAdapter(select_embedding_model(model_spec))
    return LangchainEmbeddingsWrapper(adapter)


async def load_kb_chunks(kb_id: str) -> list[dict[str, Any]]:
    """读取知识库已入库的 chunks，返回 [{"chunk_id", "file_id", "content"}]。"""
    chunks = await KnowledgeChunkRepository().list_by_kb_id(kb_id)
    return [
        {"chunk_id": c.chunk_id, "file_id": c.file_id, "content": c.content} for c in chunks if c.content
    ]


def cap_chunks(chunks: list[dict[str, Any]], max_chunks: int) -> list[dict[str, Any]]:
    """按文件均摊地把 chunks 采样到 max_chunks 以内，保证跨文件覆盖。

    RAGAS 的 NERExtractor/主题提取等 transform 会遍历全部 chunk，大库（数千 chunks）
    的 NER 成本随 chunk 数线性增长。生成测试集前按文件比例采样，把大库的
    transform 成本约束到可控范围，同时保留各文件的代表性内容。
    返回结果严格不超过 max_chunks；文件数超过 max_chunks 时保留 chunk 数最多的
    max_chunks 个文件。
    """
    if len(chunks) <= max_chunks:
        return chunks

    by_file: dict[str, list[dict[str, Any]]] = {}
    for c in chunks:
        by_file.setdefault(c.get("file_id") or "unknown", []).append(c)
    if len(by_file) > max_chunks:  # cap 小于文件数时，保留 chunk 数最多的 max_chunks 个文件
        by_file = dict(sorted(by_file.items(), key=lambda kv: -len(kv[1]))[:max_chunks])
    total = sum(len(flist) for flist in by_file.values())

    # 每文件配额按 chunk 数比例向下取整（总和天然 ≤ cap），剩余额度从最大文件补齐。
    # 不用 round：各文件配额四舍五入会溢出 cap，无法保证严格上界。
    remaining = max_chunks
    per_file = {}
    for fid, flist in by_file.items():
        quota = max(1, int(max_chunks * len(flist) / total))
        per_file[fid] = min(quota, len(flist))
        remaining -= per_file[fid]
    for fid in sorted(per_file, key=lambda f: -len(by_file[f])):
        if remaining <= 0:
            break
        headroom = len(by_file[fid]) - per_file[fid]
        take = min(headroom, remaining)
        per_file[fid] += take
        remaining -= take

    selected: list[dict[str, Any]] = []
    for fid, flist in by_file.items():
        quota = per_file[fid]
        # 均匀取 sample：从文件内 chunk 序列等间距抽样
        step = len(flist) / quota
        for i in range(quota):
            selected.append(flist[int(i * step)])
    return selected


def chunks_to_langchain_documents(chunks: list[dict[str, Any]]) -> list[Any]:
    """把 chunks 转为 langchain Document，metadata 带 chunk_id 供 reference 映射。"""
    from langchain_core.documents import Document

    return [
        Document(page_content=c["content"], metadata={"chunk_id": c["chunk_id"]}) for c in chunks if c.get("content")
    ]


def build_testset_generator(judge_llm: Any, embedding_model: Any) -> Any:
    """构造 RAGAS TestsetGenerator。

    直接传已包装好的 LangchainLLMWrapper / LangchainEmbeddingsWrapper。
    不能用 from_langchain()：它期望裸的 langchain 对象并自行包装，
    传入已包装对象会导致双重包装（内层 wrapper 缺 agenerate_prompt）。
    """
    _ensure_vertexai_stub()
    from ragas.testset import TestsetGenerator

    return TestsetGenerator(llm=judge_llm, embedding_model=embedding_model)


async def generate_testset_jsonl(
    *,
    kb_id: str,
    size: int,
    judge_llm: Any,
    embedding_model: Any,
    concurrency: int = 16,
    max_chunks: int = 500,
) -> list[dict[str, Any]]:
    """从知识库 chunks 生成 size 条 QA 测试集，返回 JSONL 行列表。

    concurrency 传给 RAGAS RunConfig.max_workers：transform 阶段（摘要/主题/实体提取）
    与 QA 合成阶段并发执行，显著缩短大批量生成耗时；默认 16 与 ragas RunConfig 默认一致。
    max_chunks 限制喂给生成器的 chunk 数（按文件均摊采样）：大库 NER transform 成本
    随 chunk 数线性增长，cap 后约束到可控范围。
    """
    from ragas.run_config import RunConfig

    chunks = await load_kb_chunks(kb_id)
    if not chunks:
        raise ValueError(f"知识库 {kb_id} 没有已入库的文本 chunks，无法生成测试集")
    chunks = cap_chunks(chunks, max_chunks=max(1, max_chunks))
    if size > len(chunks):
        logger.warning(f"size={size} 大于 chunk 采样数 {len(chunks)}，按 {len(chunks)} 生成")
        size = len(chunks)

    generator = build_testset_generator(judge_llm, embedding_model)
    documents = chunks_to_langchain_documents(chunks)
    run_config = RunConfig(max_workers=max(1, concurrency))
    testset = _generate_prechunked_testset(generator, documents, size, run_config=run_config)

    # chunk 内容 → 知识库 chunk_id，用于把样本的 reference_contexts 反查为真实 chunk_id
    content_index = {
        d.page_content: str(d.metadata["chunk_id"]) for d in documents if (d.metadata or {}).get("chunk_id")
    }
    rows = [_sample_to_jsonl(s.eval_sample, content_index=content_index) for s in testset.samples]
    if not rows:
        raise ValueError(
            f"RAGAS 未能从知识库 {kb_id} 的 {len(chunks)} 个 chunks 生成测试题，请换一个内容更丰富的知识库重试"
        )
    return rows


def _generate_prechunked_testset(generator: Any, documents: list[Any], size: int, *, run_config: Any) -> Any:
    """把已入库 chunks 视为预切分文档，走 RAGAS 生成链路。

    不能用 generate_with_langchain_docs：它对 DOCUMENT 节点套用 default_transforms，
    按 token 分箱后可能触发 HeadlineSplitter，而入库 chunk 长短不一，
    短 chunk 无 headlines 属性会直接崩溃（'headlines' property not found）；
    即使不崩溃，也可能因分箱选择偏差产出 0 场景。
    这里把 chunks 建成 CHUNK 节点并套用 default_transforms_for_prechunked，
    跳过标题切分与分箱，直接在每个 chunk 上提取摘要/主题/实体后生成 QA。
    run_config（RAGAS RunConfig）透传给 apply_transforms 与 generate，
    控制 transform/合成阶段的并发（max_workers）。
    """
    from ragas.testset.graph import KnowledgeGraph, Node, NodeType
    from ragas.testset.transforms import apply_transforms, default_transforms_for_prechunked

    nodes = [
        Node(
            type=NodeType.CHUNK,
            properties={"page_content": d.page_content, "document_metadata": d.metadata},
        )
        for d in documents
    ]
    kg = KnowledgeGraph(nodes=nodes)
    transforms = default_transforms_for_prechunked(llm=generator.llm, embedding_model=generator.embedding_model)
    apply_transforms(kg, transforms, run_config=run_config)
    generator.knowledge_graph = kg
    return generator.generate(testset_size=size, run_config=run_config, raise_exceptions=True)


def _sample_to_jsonl(sample: Any, *, content_index: dict[str, str] | None = None) -> dict[str, Any]:
    """把 ragas TestsetSample.eval_sample 转为平台评估 JSONL 行。

    gold_chunk_ids 优先用 ragas 提供的 reference_context_ids；各合成器通常不填，
    此时用 reference_contexts 文本反查知识库 chunk_id（content_index），
    保证下游 recall@k/f1@k 交叉校验能命中真实 chunk。
    """
    row: dict[str, Any] = {"query": sample.user_input}
    if sample.reference:
        row["gold_answer"] = sample.reference
    ids: list[str] = []
    if sample.reference_context_ids:
        ids = [str(cid) for cid in sample.reference_context_ids]
    elif content_index and sample.reference_contexts:
        ids = _resolve_gold_chunk_ids(sample.reference_contexts, content_index)
    if ids:
        row["gold_chunk_ids"] = ids
    return row


def _resolve_gold_chunk_ids(reference_contexts: list[str], content_index: dict[str, str]) -> list[str]:
    """把 reference_contexts 文本解析为知识库 chunk_id。

    单跳 context 与 chunk 内容完全一致；多跳 context 带 "<N-hop>\\n\\n" 前缀。
    """
    import re

    prefix = re.compile(r"^<\d+-hop>\n\n")
    ids: list[str] = []
    for ctx in reference_contexts:
        cid = content_index.get(ctx) or content_index.get(prefix.sub("", ctx))
        if cid:
            ids.append(cid)
    return ids


def write_testset_jsonl(rows: list[dict[str, Any]], output_path: str) -> str:
    """写 JSONL 文件，返回路径。"""
    import json

    from pathlib import Path

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return output_path
