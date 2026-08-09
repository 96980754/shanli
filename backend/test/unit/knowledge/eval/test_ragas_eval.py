import asyncio
import os

import pytest

os.environ.setdefault("OPENAI_API_KEY", "test-key")

# 先导入 _ragas_compat（注入 vertexai stub），再导入被测模块
from yuxi.knowledge.eval.ragas_eval import (
    REPORT_DISCLAIMER,
    RagasEmbeddingAdapter,
    build_json_report,
    build_markdown_report,
    score_sample,
)


class FakeEmbedModel:
    """模拟系统 BaseEmbeddingModel 的 encode / aencode 接口。"""

    def encode(self, texts):
        return [[1.0] * 3 for _ in texts]

    async def aencode(self, texts):
        return [[1.0] * 3 for _ in texts]


@pytest.fixture
def adapter():
    return RagasEmbeddingAdapter(FakeEmbedModel())


def test_embedding_adapter_embed_query(adapter):
    assert adapter.embed_query("问题") == [1.0, 1.0, 1.0]


def test_embedding_adapter_embed_documents(adapter):
    assert adapter.embed_documents(["a", "b"]) == [[1.0, 1.0, 1.0], [1.0, 1.0, 1.0]]


def test_embedding_adapter_async_variants(adapter):
    assert asyncio.run(adapter.aembed_query("问题")) == [1.0, 1.0, 1.0]
    assert asyncio.run(adapter.aembed_documents(["a", "b"])) == [[1.0, 1.0, 1.0], [1.0, 1.0, 1.0]]
    assert asyncio.run(adapter.aembed_text("问题")) == [1.0, 1.0, 1.0]


def test_embedding_adapter_has_run_config(adapter):
    # ragas 的 SemanticSimilarity 会访问 run_config
    assert adapter.run_config is not None
    adapter.set_run_config(adapter.run_config)  # 幂等，不报错


class _FakeMetric:
    """模拟 RAGAS metric，返回预设分数。"""

    def __init__(self, name, value):
        self.name = name
        self._value = value

    async def single_turn_ascore(self, sample):
        return self._value


def test_score_sample_treats_nan_as_none():
    # 生成答案为空时 faithfulness 可能返回 nan（0 条可验证陈述），应视为缺失而非污染均值
    metrics = [_FakeMetric("faithfulness", float("nan")), _FakeMetric("cp", 0.5), _FakeMetric("cr", None)]
    scores = asyncio.run(score_sample(object(), metrics))
    assert scores["faithfulness"] is None
    assert scores["cp"] == 0.5
    assert scores["cr"] is None


def _sample_results():
    return {
        "metrics": {"faithfulness": 1.0, "context_precision": None},
        "items": [
            {
                "index": 0,
                "query": "电池容量是多少？",
                "ragas_metrics": {"faithfulness": 1.0, "context_precision": 0.5},
                "answer_scores": {"score": 1.0, "reasoning": "正确"},
            },
            {
                "index": 1,
                "query": "防水等级？",
                "ragas_metrics": {"faithfulness": 0.0, "context_precision": None},
                "answer_scores": {},
            },
        ],
    }


def test_json_report_structure():
    report = build_json_report(_sample_results())

    assert report["disclaimer"] == REPORT_DISCLAIMER
    assert set(report["metrics"]) == {"faithfulness", "context_precision"}
    assert report["metrics"]["faithfulness"] == 1.0
    assert report["metrics"]["context_precision"] is None
    assert len(report["items"]) == 2
    assert report["items"][0]["query"] == "电池容量是多少？"
    assert report["items"][0]["answer_score"] == 1.0
    assert report["items"][0]["answer_reasoning"] == "正确"


def test_markdown_report_contains_aggregate_and_rows():
    md = build_markdown_report(_sample_results(), run_name="test_run")

    assert "# RAGAS 评估报告：test_run" in md
    assert REPORT_DISCLAIMER in md
    # 均值表：faithfulness 有值，context_precision 为 None 显示 -
    assert "| faithfulness | 1.0000 |" in md
    assert "| context_precision | - |" in md
    # 每题明细表头与行
    assert "| 0 | 电池容量是多少？ |" in md
    assert "| 1 | 防水等级？ |" in md
    assert "0.5000" in md
