import asyncio
import os

import pytest

os.environ.setdefault("OPENAI_API_KEY", "test-key")

from yuxi.knowledge.eval.ragas_testset_gen import (
    LangchainEmbeddingsAdapter,
    _resolve_gold_chunk_ids,
    _sample_to_jsonl,
    cap_chunks,
    chunks_to_langchain_documents,
    write_testset_jsonl,
)


class FakeEmbedModel:
    """模拟系统 BaseEmbeddingModel 的 encode / aencode 接口。"""

    def encode(self, texts):
        return [[1.0] * 3 for _ in texts]

    async def aencode(self, texts):
        return [[1.0] * 3 for _ in texts]


@pytest.fixture
def adapter():
    return LangchainEmbeddingsAdapter(FakeEmbedModel())


def test_langchain_embeddings_adapter_sync(adapter):
    assert adapter.embed_query("问题") == [1.0, 1.0, 1.0]
    assert adapter.embed_documents(["a", "b"]) == [[1.0, 1.0, 1.0], [1.0, 1.0, 1.0]]


def test_langchain_embeddings_adapter_async(adapter):
    assert asyncio.run(adapter.aembed_query("问题")) == [1.0, 1.0, 1.0]
    assert asyncio.run(adapter.aembed_documents(["a", "b"])) == [[1.0, 1.0, 1.0], [1.0, 1.0, 1.0]]


def test_chunks_to_langchain_documents():
    chunks = [{"chunk_id": "c1", "content": "第一段"}, {"chunk_id": "c2", "content": "第二段"}]
    docs = chunks_to_langchain_documents(chunks)

    assert len(docs) == 2
    assert docs[0].page_content == "第一段"
    assert docs[0].metadata["chunk_id"] == "c1"
    assert docs[1].metadata["chunk_id"] == "c2"


def test_chunks_to_langchain_documents_skips_empty():
    docs = chunks_to_langchain_documents([{"chunk_id": "c1", "content": ""}])
    assert docs == []


def _fake_chunks(by_file: dict[str, int]) -> list[dict]:
    chunks = []
    for fid, n in by_file.items():
        for i in range(n):
            chunks.append({"chunk_id": f"{fid}_chunk_{i}", "file_id": fid, "content": f"text {fid} {i}"})
    return chunks


def test_cap_chunks_unchanged_when_within_cap():
    chunks = _fake_chunks({"f1": 3, "f2": 2})
    assert cap_chunks(chunks, 10) == chunks


def test_cap_chunks_bounds_total_and_covers_files():
    chunks = _fake_chunks({"f1": 80, "f2": 15, "f3": 5})
    out = cap_chunks(chunks, 10)

    assert len(out) <= len(chunks)
    assert set(c["file_id"] for c in out) == {"f1", "f2", "f3"}  # 每个文件至少 1 条
    assert len(set(c["chunk_id"] for c in out)) == len(out)  # 无重复


def test_cap_chunks_samples_evenly_within_file():
    chunks = _fake_chunks({"f1": 6})
    out = cap_chunks(chunks, 3)
    assert [c["chunk_id"] for c in out] == ["f1_chunk_0", "f1_chunk_2", "f1_chunk_4"]


def test_cap_chunks_strictly_within_cap_when_rounding_overshoots():
    # 每文件配额四舍五入可能使总配额 > cap（如 508 个单 chunk 文件、cap 500），须回剪
    chunks = _fake_chunks({f"f{i:03d}": 1 for i in range(508)})
    out = cap_chunks(chunks, 500)
    assert len(out) == 500
    assert len(set(c["file_id"] for c in out)) == len(out)  # 每文件仅 1 条，无重复


class _FakeSample:
    """模拟 ragas SingleTurnSample 的最小对象。"""

    def __init__(self, user_input, reference, reference_context_ids):
        self.user_input = user_input
        self.reference = reference
        self.reference_context_ids = reference_context_ids


def test_sample_to_jsonl_with_all_fields():
    row = _sample_to_jsonl(_FakeSample("蓝牙精度是多少？", "3-5米", ["c1", "c2"]))
    assert row == {"query": "蓝牙精度是多少？", "gold_answer": "3-5米", "gold_chunk_ids": ["c1", "c2"]}


def test_sample_to_jsonl_optional_fields():
    row = _sample_to_jsonl(_FakeSample("问题", None, None))
    assert row == {"query": "问题"}


def test_sample_to_jsonl_resolves_ids_from_reference_contexts():
    # ragas 合成器不填 reference_context_ids 时，用 reference_contexts 文本反查真实 chunk_id
    sample = _FakeSample("问题", "答案", None)
    sample.reference_contexts = ["第一段内容"]
    row = _sample_to_jsonl(sample, content_index={"第一段内容": "c1", "另一段": "c2"})
    assert row["gold_chunk_ids"] == ["c1"]


def test_resolve_gold_chunk_ids_handles_multi_hop_prefix():
    index = {"单段内容": "c1", "另一段": "c2"}
    # 多跳 context 带 "<N-hop>\n\n" 前缀
    assert _resolve_gold_chunk_ids(["<1-hop>\n\n单段内容", "<2-hop>\n\n另一段"], index) == ["c1", "c2"]
    # 无法匹配的文本忽略，不回填错误 id
    assert _resolve_gold_chunk_ids(["未知内容"], index) == []


def test_write_testset_jsonl(tmp_path):
    output = tmp_path / "nested" / "testset.jsonl"
    rows = [{"query": "Q1", "gold_answer": "A1"}, {"query": "Q2"}]
    path = write_testset_jsonl(rows, str(output))

    assert path == str(output)
    lines = output.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 2
    assert '"gold_answer": "A1"' in lines[0]
    assert "gold_answer" not in lines[1]
