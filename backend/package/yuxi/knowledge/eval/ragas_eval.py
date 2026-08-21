"""RAGAS 内部评估插件（不交付甲方）。

复用现有评估链路 evaluate_question() 生成 (query, retrieved_chunks, generated_answer)，
再用 RAGAS 标准指标评分，输出 JSON + Markdown 报告。

依赖隔离：ragas 放在 pyproject [dependency-groups].eval，客户端镜像不安装；
本模块所有 `import ragas` 都在函数内懒加载，未安装 ragas 时不影响 `import yuxi`。

报告说明：本评估基于简化评估链路（复用 evaluate_question），不代表生产 Agent 最终表现。
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from yuxi.knowledge.eval._ragas_compat import _ensure_vertexai_stub
from yuxi.knowledge.eval.evaluator import evaluate_question
from yuxi.models import select_model as _select_model
from yuxi.models.embed import select_embedding_model
from yuxi.utils import logger

# 报告标注：明确区分简化评估链路与生产 Agent
REPORT_DISCLAIMER = "本报告基于简化评估链路（复用 evaluate_question），不代表生产 Agent 最终表现。"

# 仅需 LLM judge 的指标（默认开启）
LLM_ONLY_METRIC_NAMES = ("faithfulness", "context_precision", "context_recall")
# 需要 embeddings 的指标（--with-embedding-metrics 时开启）：
# answer_relevancy / answer_correctness


class RagasEmbeddingAdapter:
    """把系统自定义 BaseEmbeddingModel 适配为 ragas 需要的 embedding 接口。

    ragas 的经典指标对 embedding 只做 duck-typing：
    - answer_relevancy 调用 embed_query / embed_documents（同步）
    - answer_correctness 的 SemanticSimilarity 组件调用 aembed_text / embed_text
    全部委托给系统模型的 encode / aencode。不子类化 ragas 基类，
    使本类可在模块级定义且保持 ragas 懒加载。
    """

    def __init__(self, embed_model: Any) -> None:
        from ragas.run_config import RunConfig

        self._model = embed_model
        self.run_config = RunConfig()

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._model.encode(texts)

    def embed_query(self, text: str) -> list[float]:
        return self._model.encode([text])[0]

    async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
        return await self._model.aencode(texts)

    async def aembed_query(self, text: str) -> list[float]:
        return (await self._model.aencode([text]))[0]

    async def aembed_text(self, text: str) -> list[float]:
        return (await self._model.aencode([text]))[0]

    def embed_text(self, text: str) -> list[float]:
        return self._model.encode([text])[0]

    def set_run_config(self, run_config: Any) -> None:
        self.run_config = run_config


def build_judge_llm(
    model_spec: str,
    *,
    bypass_n: bool = True,
    run_config: Any = None,
    cache: Any = None,
) -> Any:
    """构建 ragas judge LLM：系统 load_chat_model 结果经 LangchainLLMWrapper 包装。

    bypass_n=True 规避部分 OpenAI 兼容提供商（如 deepseek）拒绝 n!=1 的问题。
    run_config / cache 透传给 LangchainLLMWrapper：cache 提供 DiskCacheBackend 时，
    相同 prompt 的 LLM 调用命中磁盘缓存，重跑测试集可跳过全部 LLM 调用。
    """
    from yuxi.agents.models import load_chat_model

    from ragas.llms import LangchainLLMWrapper

    return LangchainLLMWrapper(
        load_chat_model(model_spec),
        run_config=run_config,
        cache=cache,
        bypass_n=bypass_n,
    )


def build_embedding_adapter(model_spec: str) -> Any:
    """构建 ragas embedding 适配器。"""
    return RagasEmbeddingAdapter(select_embedding_model(model_spec))


def _build_llm_metrics(judge_llm: Any) -> list[Any]:
    import ragas.metrics as M

    metrics = []
    for name in LLM_ONLY_METRIC_NAMES:
        metric = getattr(M, name)
        metric.llm = judge_llm
        metrics.append(metric)
    return metrics


def _build_embedding_metrics(judge_llm: Any, embedding_adapter: Any) -> list[Any]:
    from ragas.metrics import answer_correctness, answer_relevancy
    from ragas.metrics._answer_similarity import AnswerSimilarity

    answer_relevancy.llm = judge_llm
    answer_relevancy.embeddings = embedding_adapter

    answer_correctness.llm = judge_llm
    answer_correctness.embeddings = embedding_adapter
    # answer_similarity 组件在构造时缓存 embeddings，需显式注入
    answer_correctness.answer_similarity = AnswerSimilarity(embeddings=embedding_adapter)

    return [answer_relevancy, answer_correctness]


def build_ragas_metrics(
    judge_llm: Any, embedding_adapter: Any | None = None, *, with_embedding_metrics: bool = False
) -> list[Any]:
    """构建 RAGAS 指标列表。embedding 指标默认关闭，需显式开启。"""
    _ensure_vertexai_stub()
    metrics = _build_llm_metrics(judge_llm)
    if with_embedding_metrics:
        if embedding_adapter is None:
            raise ValueError("启用 embedding 指标需要提供 embedding_adapter")
        metrics.extend(_build_embedding_metrics(judge_llm, embedding_adapter))
    return metrics


def _sample_from_question_data(
    question_data: dict[str, Any], retrieved_chunks: list[dict[str, Any]], generated_answer: str
) -> Any:
    """把 evaluate_question 产物组装为 ragas SingleTurnSample。"""
    from ragas import SingleTurnSample

    return SingleTurnSample(
        user_input=question_data["query"],
        retrieved_contexts=[c.get("content", "") for c in retrieved_chunks if c.get("content")],
        response=generated_answer,
        reference=question_data.get("gold_answer") or None,
    )


async def score_sample(sample: Any, metrics: list[Any]) -> dict[str, float]:
    """逐指标对单个 sample 评分，返回 {metric_name: value}。单指标失败不阻断其他指标。"""
    import math

    result: dict[str, float | None] = {}
    for metric in metrics:
        name = metric.name
        try:
            score = await metric.single_turn_ascore(sample)
            result[name] = None if score is None or math.isnan(float(score)) else float(score)
        except Exception as e:
            logger.error(f"RAGAS 指标 {name} 评分失败: {e}")
            result[name] = None
    return result


async def run_ragas_evaluation(
    kb_instance: Any,
    kb_id: str,
    questions: list[dict[str, Any]],
    retrieval_config: dict[str, Any],
    judge_llm: Any,
    embedding_adapter: Any | None = None,
    *,
    with_embedding_metrics: bool = False,
    concurrency: int = 1,
) -> dict[str, Any]:
    """对一批问题执行 RAGAS 评估。

    questions: [{"query", "gold_chunk_ids"?, "gold_answer"?}, ...]
    返回聚合指标 + 每题明细（含 RAGAS 分 + 系统自带的检索/答案分交叉校验）。

    concurrency: 同时评估的题目数。RAGAS 每题内部对 chunk 的 LLM 判断是串行的
    （single_turn 路径不使用 RunConfig.max_workers），并发题目是单轮提速的主要杠杆；
    共享 judge LLM 的并发调用由 LangChain/tenacity 保证安全。默认 1 保持串行。
    """
    metrics = build_ragas_metrics(judge_llm, embedding_adapter, with_embedding_metrics=with_embedding_metrics)

    has_gold_chunks = any(q.get("gold_chunk_ids") for q in questions)
    has_gold_answers = any(q.get("gold_answer") for q in questions)

    semaphore = asyncio.Semaphore(max(1, concurrency))

    async def _evaluate_one(index: int, q: dict[str, Any]) -> dict[str, Any]:
        async with semaphore:
            result = await evaluate_question(
                kb_instance=kb_instance,
                kb_id=kb_id,
                question_data=q,
                retrieval_config=retrieval_config,
                has_gold_chunks=has_gold_chunks,
                has_gold_answers=has_gold_answers,
                judge_llm=None,  # 答案生成走简化链路，RAGAS 用自己的 judge 评估
                select_model_fn=_select_model,
            )
            rag_scores = await score_sample(
                _sample_from_question_data(
                    q, result["detail"]["retrieved_chunks"], result["detail"]["generated_answer"]
                ),
                metrics,
            )
            return {
                "index": index,
                "query": q["query"],
                "ragas_metrics": rag_scores,
                "answer_scores": result["answer_scores"],
                "retrieval_scores": result["retrieval_scores"],
            }

    # gather 保序；任一题异常会随 gather 抛出（与串行版 fail-fast 语义一致）
    per_item = list(await asyncio.gather(*[_evaluate_one(i, q) for i, q in enumerate(questions)]))

    metric_names = [m.name for m in metrics]
    aggregate = {}
    for name in metric_names:
        values = [item["ragas_metrics"][name] for item in per_item if item["ragas_metrics"].get(name) is not None]
        aggregate[name] = sum(values) / len(values) if values else None

    return {"metrics": aggregate, "items": per_item}


def build_json_report(results: dict[str, Any], *, disclaimer: str = REPORT_DISCLAIMER) -> dict[str, Any]:
    """组装 JSON 报告结构。"""
    return {
        "disclaimer": disclaimer,
        "metrics": dict(results["metrics"]),
        "items": [
            {
                "index": item["index"],
                "query": item["query"],
                "ragas_metrics": item["ragas_metrics"],
                "answer_score": item["answer_scores"].get("score"),
                "answer_reasoning": item["answer_scores"].get("reasoning"),
            }
            for item in results["items"]
        ],
    }


def build_markdown_report(results: dict[str, Any], *, run_name: str, disclaimer: str = REPORT_DISCLAIMER) -> str:
    """渲染 Markdown 报告：标题 + 说明 + 均值表 + 每题明细。"""
    metric_names = list(results["metrics"].keys())
    lines = [
        f"# RAGAS 评估报告：{run_name}",
        "",
        f"> {disclaimer}",
        "",
        "## 聚合指标",
        "",
        "| 指标 | 均值 |",
        "| --- | --- |",
    ]
    lines.extend(f"| {name} | {_fmt(value)} |" for name, value in results["metrics"].items())
    lines += ["", "## 每题明细", ""]
    header = "| # | 问题 |" + "".join(f" {name} |" for name in metric_names)
    separator = "| --- | --- |" + "".join(" --- |" for _ in metric_names)
    lines += [header, separator]
    for item in results["items"]:
        cells = [str(item["index"]), item["query"]]
        cells.extend(_fmt(item["ragas_metrics"].get(name)) for name in metric_names)
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def _fmt(value: float | None) -> str:
    return "-" if value is None else f"{value:.4f}"


def write_reports(results: dict[str, Any], *, run_name: str, output_dir: str = ".") -> tuple[str, str]:
    """写 JSON + Markdown 报告到磁盘，返回 (json_path, md_path)。"""
    import os

    safe_name = re.sub(r"[^a-zA-Z0-9_-]+", "_", run_name)
    json_path = os.path.join(output_dir, f"ragas_eval_{safe_name}.json")
    md_path = os.path.join(output_dir, f"ragas_eval_{safe_name}.md")

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(build_json_report(results), f, ensure_ascii=False, indent=2)
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(build_markdown_report(results, run_name=run_name))

    return json_path, md_path
