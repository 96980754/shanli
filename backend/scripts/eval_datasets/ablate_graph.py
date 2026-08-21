#!/usr/bin/env python3
"""KG 消融实验：图检索开/关（use_graph_retrieval True/False）对比。

对选定的 20 道非零分题（三库按 section 确定性选取，非随机），同一检索配置下分别
以「图开」「图关」各跑一遍简化评估链路，对比：
  - 检索质量：RAGAS context_recall / context_precision
  - 生成质量：RAGAS faithfulness
  - 系统性能：每问 aquery 总耗时（两配置的答案生成相同，耗时差异归因于图检索）
  - 分场景：按 section / 按库分桶统计

选区规则：每库每个 section 取全局 index 最小的非 0 分题（0 分题指上次全量评估
faithfulness=0 的题，见 docs/vibe/2026-08-12-ragas-eval-summary.md「六、逐题明细」）。

用法（api-dev 容器内）:
  docker exec -w /app/scripts/eval_datasets api-dev python ablate_graph.py [--limit N]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "package"))

from yuxi.knowledge.eval.evaluator import generate_answer_if_needed, normalize_query_result
from yuxi.knowledge.eval.ragas_eval import (
    build_judge_llm,
    build_ragas_metrics,
    score_sample,
    _sample_from_question_data,
)
from yuxi.knowledge.runtime import knowledge_base
from yuxi.models import select_model
from yuxi.repositories.knowledge_base_repository import KnowledgeBaseRepository
from yuxi.storage.postgres.manager import pg_manager
from yuxi.utils import logger

# 消融选区：库别名 -> 全局 index（全部为非 0 分题；每 section 取最小全局 index）
ABLATION_SELECTION = {
    "loc": [8, 14, 29, 53, 61, 83],  # 产品使用x2 / 故障排查 / 应用场景 / 部署配置 / 方案整合
    "mcx": [41, 47, 60, 63, 76, 91],  # 产品规格x2 / 应用场景 / 部署配置 / 商务资质 / 方案整合
    "poc": [1, 20, 35, 62, 75, 90, 93, 98],  # 8 个 section 各 1
}
DATASET_FILES = {
    "loc": "loc.jsonl",
    "mcx": "mcx.jsonl",
    "poc": "poc.jsonl",
}
# 库别名 -> 数据库 kb_id（与基准评估一致）
KB_IDS = {
    "loc": "kb_0368jjmecb",
    "mcx": "kb_mvng8u1201",
    "poc": "kb_3cm2gz6tyb",
}
# judge/answer 固定与基准评估一致（见 docs/vibe/2026-08-12-ragas-eval-summary.md）
MODEL_SPEC = "deepseek:deepseek-v4-flash"
# 本轮消融：图开配置的图检索融合权重（覆盖 DB 中默认的 0.5）
ABLATION_GRAPH_WEIGHT = 0.2
REPORT_PREFIX = "ablation_graph_w0.2"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="KG 消融实验：图检索开/关对比")
    parser.add_argument("--limit", type=int, help="每库最多跑 N 题（冒烟用）")
    return parser.parse_args()


async def resolve_retrieval_config(kb_id: str, kb_instance) -> dict:
    kb_repo = KnowledgeBaseRepository()
    kb_row = await kb_repo.get_by_kb_id(kb_id)
    query_params = (kb_row.query_params if kb_row else None) or {}
    retrieval_config = query_params.get("options", {}) if isinstance(query_params, dict) else {}
    if not retrieval_config and kb_instance is not None:
        retrieval_config = kb_instance._get_default_query_params(kb_id).get("options", {})
    return retrieval_config or {}


async def eval_one(
    *,
    kb_instance,
    kb_id: str,
    question_data: dict,
    retrieval_config: dict,
    metrics: list,
) -> dict:
    """跑一遍简化评估链路，返回 aquery 耗时 + RAGAS 指标。"""
    query = question_data["query"]
    t0 = time.perf_counter()
    query_result = await kb_instance.aquery(query, kb_id, **retrieval_config)
    latency = time.perf_counter() - t0
    answer, chunks = normalize_query_result(query_result)
    answer = await generate_answer_if_needed(
        query=query,
        generated_answer=answer,
        retrieved_chunks=chunks,
        retrieval_config=retrieval_config,
        select_model_fn=select_model,
    )
    sample = _sample_from_question_data(question_data, chunks, answer)
    scores = await score_sample(sample, metrics)
    return {
        "latency": latency,
        "answer_len": len(answer),
        "n_chunks": len(chunks),
        "ragas": scores,
    }


def load_questions(kb: str) -> list[dict]:
    path = Path(__file__).resolve().parent / DATASET_FILES[kb]
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").strip().splitlines() if line.strip()]
    wanted = set(ABLATION_SELECTION[kb])
    picked = [r for r in rows if r["index"] in wanted]
    missing = wanted - {r["index"] for r in picked}
    if missing:
        logger.warning(f"{kb} 缺少选区 index: {sorted(missing)}")
    return picked


def mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def fmt(v: float | None, digits: int = 4) -> str:
    return "-" if v is None else f"{v:.{digits}f}"


def build_markdown(items: list[dict], judge_spec: str, model_spec: str) -> str:
    """渲染对比报告：整体 / 按库 / 按 section / 逐题。"""
    lines = [
        "# KG 消融实验：图检索开 vs 关",
        "",
        f"> 20 道非 0 分题（每库按 section 确定性选取）；judge={judge_spec}，answer={model_spec}",
        f"> 图开配置 graph_weight={ABLATION_GRAPH_WEIGHT}（本轮实验值，DB 默认 0.5）",
        "> 简化评估链路（复用 evaluate_question 检索+生成路径），不代表生产 Agent 最终表现。",
        "> 耗时 = aquery 总耗时（两配置答案生成相同，差异归因于图检索）。",
        "",
        "## 整体对比（20 题）",
        "",
        "| 指标 | 图开 | 图关 | 差值(开-关) |",
        "| --- | --- | --- | --- |",
    ]
    for key, label in (
        ("faithfulness", "faithfulness"),
        ("context_precision", "context_precision"),
        ("context_recall", "context_recall"),
        ("latency", "aquery 耗时(s)"),
    ):
        def field_value(entry: dict) -> float | None:
            return entry["latency"] if key == "latency" else entry["ragas"].get(key)

        on = mean([field_value(i["on"]) for i in items])
        off = mean([field_value(i["off"]) for i in items])
        delta = (on - off) if on is not None and off is not None else None
        lines.append(f"| {label} | {fmt(on)} | {fmt(off)} | {('+' if delta and delta > 0 else '') if delta is not None else ''}{fmt(delta) if delta is not None else '-'} |")
    lines += ["", "## 按知识库", "", "| 知识库 | 配置 | n | faithfulness | context_precision | context_recall | 耗时(s) |", "| --- | --- | --- | --- | --- | --- | --- |"]
    for kb in ("loc", "mcx", "poc"):
        subset = [i for i in items if i["kb"] == kb]
        for tag, field in (("图开", "on"), ("图关", "off")):
            vals = [i[field] for i in subset]
            n = len(vals)
            lines.append(
                "| {} | {} | {} | {} | {} | {} | {} |".format(
                    kb,
                    tag,
                    n,
                    fmt(mean([v["ragas"]["faithfulness"] for v in vals])),
                    fmt(mean([v["ragas"]["context_precision"] for v in vals])),
                    fmt(mean([v["ragas"]["context_recall"] for v in vals])),
                    fmt(mean([v["latency"] for v in vals]), 2),
                )
            )
    lines += ["", "## 按 section", "", "| section | 配置 | n | faithfulness | context_precision | context_recall | 耗时(s) |", "| --- | --- | --- | --- | --- | --- | --- |"]
    sections = sorted({i["section"] for i in items})
    for sec in sections:
        subset = [i for i in items if i["section"] == sec]
        for tag, field in (("图开", "on"), ("图关", "off")):
            vals = [i[field] for i in subset]
            lines.append(
                "| {} | {} | {} | {} | {} | {} | {} |".format(
                    sec,
                    tag,
                    len(vals),
                    fmt(mean([v["ragas"]["faithfulness"] for v in vals])),
                    fmt(mean([v["ragas"]["context_precision"] for v in vals])),
                    fmt(mean([v["ragas"]["context_recall"] for v in vals])),
                    fmt(mean([v["latency"] for v in vals]), 2),
                )
            )
    lines += ["", "## 逐题明细（20 题）", "", "| # | 库 | section | 问题 | 图开f | 图关f | 图开p | 图关p | 图开r | 图关r | 图开耗时 | 图关耗时 |", "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for i in items:
        o, c = i["on"], i["off"]
        lines.append(
            "| {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} |".format(
                i["idx"],
                i["kb"],
                i["section"],
                i["query"],
                fmt(o["ragas"]["faithfulness"]),
                fmt(c["ragas"]["faithfulness"]),
                fmt(o["ragas"]["context_precision"]),
                fmt(c["ragas"]["context_precision"]),
                fmt(o["ragas"]["context_recall"]),
                fmt(c["ragas"]["context_recall"]),
                fmt(o["latency"], 2),
                fmt(c["latency"], 2),
            )
        )
    return "\n".join(lines) + "\n"


async def run(args: argparse.Namespace) -> int:
    pg_manager.initialize()
    model_spec = MODEL_SPEC
    judge_spec = model_spec

    judge_llm = build_judge_llm(judge_spec)
    metrics = build_ragas_metrics(judge_llm)
    items: list[dict] = []

    for kb in ("loc", "mcx", "poc"):
        questions = load_questions(kb)
        if args.limit:
            questions = questions[: args.limit]
        if not questions:
            continue
        kb_id = KB_IDS[kb]
        kb_instance = await knowledge_base.aget_kb(kb_id)
        if kb_instance is None:
            print(f"知识库不存在: {kb} ({kb_id})", file=sys.stderr)
            return 1
        await kb_instance._load_metadata()
        base_cfg = await resolve_retrieval_config(kb_id, kb_instance)
        base_cfg.setdefault("answer_llm", model_spec)
        print(f"[{kb}] 选区 {len(questions)} 题 | final_top_k={base_cfg.get('final_top_k')} 图开默认={base_cfg.get('use_graph_retrieval')}", flush=True)

        for q in questions:
            row = {"idx": q["index"], "kb": kb, "section": q.get("section", ""), "query": q["query"]}
            for tag, graph in (("on", True), ("off", False)):
                cfg = dict(base_cfg)
                cfg["use_graph_retrieval"] = graph
                if graph:
                    cfg["graph_weight"] = ABLATION_GRAPH_WEIGHT
                try:
                    row[tag] = await eval_one(
                        kb_instance=kb_instance,
                        kb_id=kb_id,
                        question_data=q,
                        retrieval_config=cfg,
                        metrics=metrics,
                    )
                except Exception as e:
                    logger.error(f"#{q['index']} 图{'开' if graph else '关'}失败: {e}")
                    row[tag] = {
                        "latency": 0.0,
                        "answer_len": 0,
                        "n_chunks": 0,
                        "ragas": {m.name: None for m in metrics},
                    }
            items.append(row)
            print(f"  #{q['index']} [{q['section']}] on_f={row['on']['ragas'].get('faithfulness')} off_f={row['off']['ragas'].get('faithfulness')} | on耗时={row['on']['latency']:.1f}s off耗时={row['off']['latency']:.1f}s", flush=True)

    if not items:
        print("没有可评估的题目")
        return 1

    out_dir = Path(__file__).resolve().parent / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    md = build_markdown(items, judge_spec, model_spec)
    md_path = out_dir / f"{REPORT_PREFIX}.md"
    json_path = out_dir / f"{REPORT_PREFIX}.json"
    md_path.write_text(md, encoding="utf-8")
    json_path.write_text(json.dumps(items, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"消融报告已写入:\n  {json_path}\n  {md_path}")
    return 0


def main() -> int:
    args = parse_args()
    try:
        return asyncio.run(run(args))
    except Exception as e:
        print(f"失败: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
