#!/usr/bin/env python3
"""为定制验证集生成 RAGAS 参考答案（gold_answer），写回原 JSONL。

方法：复用简化评估链路的检索 + 生成路径——每道题用指定知识库 aquery 检索 top chunks，
LLM 基于这些片段生成参考答案，写入原 JSONL 的 gold_answer 字段（保留原顺序与字段）。

局限（报告需注明）：参考答案来自被评估的同一知识库、且与评估走同一 aquery 检索路径，
因此 context_recall / context_precision 会系统性偏高，适合作为内部回归基线，不代表生产表现。

用法（api-dev 容器内）:
  docker exec -w /app/scripts/eval_datasets api-dev python generate_gold_answers.py \
      --kb kb_0368jjmecb --file loc.jsonl [--model deepseek:deepseek-v4-flash] [--limit 3]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "package"))

from yuxi import config as sys_config
from yuxi.knowledge.eval.evaluator import normalize_query_result
from yuxi.knowledge.runtime import knowledge_base
from yuxi.models import select_model
from yuxi.repositories.knowledge_base_repository import KnowledgeBaseRepository
from yuxi.storage.postgres.manager import pg_manager
from yuxi.utils import logger

# 外部 LLM 间歇性连接抖动（deepseek API 偶发 Connection error）时重试，避免产出空参考答案
MAX_LLM_RETRIES = 3

# 参考答案标注提示词：与评估的答案生成提示（build_answer_prompt）区分——
# 标注尽量写出可核查的要点与数值，片段不足时明确说明缺哪些信息，
# 而非一律回复"信息不足"，使 context_recall 在部分命中时仍可计算。
GOLD_PROMPT = (
    "你是企业知识库的答案标注员。请仅依据以下检索到的知识库片段，撰写一份准确、"
    "完整、可作为标准参考答案的文字（问题与片段来自同一知识库）。要求：\n"
    "1. 直接回答用户问题，列出具体功能、数值、规格、步骤；引用片段中出现的数字与术语。\n"
    "2. 片段中有相关信息就尽量完整呈现；片段不足以完整回答时，先答已确认的部分，"
    "再在末尾明确列出\"片段中未记载：...\"的缺失项，不要编造。\n"
    "3. 输出简洁的中文段落，不重复问题，不使用 Markdown 表格。\n\n"
    "{context}\n\n用户问题：{query}"
)


def build_gold_prompt(query: str, retrieved_chunks: list[dict], max_docs: int = 10) -> str:
    docs = [
        f"文档 {i}：{chunk.get('content', '')}"
        for i, chunk in enumerate(retrieved_chunks[:max_docs], 1)
        if chunk.get("content")
    ]
    return GOLD_PROMPT.format(query=query, context="\n\n".join(docs))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="为验证集生成 RAGAS 参考答案")
    parser.add_argument("--kb", required=True, help="知识库 ID")
    parser.add_argument("--file", required=True, help="JSONL 文件路径（相对 eval_datasets 目录或绝对路径）")
    parser.add_argument("--model", help="生成参考答案的模型 spec（默认系统 default_model）")
    parser.add_argument("--limit", type=int, help="只处理前 N 题（冒烟用）")
    parser.add_argument("--force", action="store_true", help="重新生成已有 gold_answer 的题目（默认跳过非空 gold）")
    return parser.parse_args()


async def call_with_retry(llm, prompt: str) -> str:
    """调用 LLM 生成参考答案，连接类错误重试，返回去空白后的文本。"""
    import asyncio

    last_err: Exception | None = None
    for attempt in range(1, MAX_LLM_RETRIES + 1):
        try:
            resp = await llm.call(prompt, stream=False)
            return (resp.content or "").strip() if resp else ""
        except Exception as e:
            last_err = e
            if attempt < MAX_LLM_RETRIES:
                await asyncio.sleep(2 * attempt)
    if last_err is not None:
        raise last_err
    return ""


async def resolve_retrieval_config(kb_id: str, kb_instance) -> dict:
    kb_repo = KnowledgeBaseRepository()
    kb_row = await kb_repo.get_by_kb_id(kb_id)
    query_params = (kb_row.query_params if kb_row else None) or {}
    retrieval_config = query_params.get("options", {}) if isinstance(query_params, dict) else {}
    if not retrieval_config and kb_instance is not None:
        retrieval_config = kb_instance._get_default_query_params(kb_id).get("options", {})
    return retrieval_config or {}


async def run(args: argparse.Namespace) -> int:
    pg_manager.initialize()
    path = Path(args.file)
    if not path.is_absolute():
        path = Path(__file__).resolve().parent / path
    if not path.exists():
        print(f"文件不存在: {path}", file=sys.stderr)
        return 1

    model_spec = args.model or getattr(sys_config, "default_model", None)
    if not model_spec:
        print("无法确定生成模型，请用 --model 指定", file=sys.stderr)
        return 1

    kb_instance = await knowledge_base.aget_kb(args.kb)
    if kb_instance is None:
        print(f"知识库不存在: {args.kb}", file=sys.stderr)
        return 1
    await kb_instance._load_metadata()
    retrieval_config = await resolve_retrieval_config(args.kb, kb_instance)
    # 注入可用 answer 模型：aquery 内部用 answer_llm 生成答案，
    # 否则会落到库配置里未注册的模型（如 MiniMax）而报错，与 ragas_eval 的注入保持一致。
    retrieval_config.setdefault("answer_llm", model_spec)
    llm = select_model(model_spec=model_spec)
    print(f"库 {args.kb} | 模型 {model_spec} | 检索配置 final_top_k={retrieval_config.get('final_top_k')}", flush=True)

    lines = path.read_text(encoding="utf-8").strip().splitlines()
    if args.limit:
        lines = lines[: args.limit]
    out_items: list[dict] = []
    for i, line in enumerate(lines, 1):
        if not line.strip():
            continue
        item = json.loads(line)
        query = item["query"]
        if not args.force and (item.get("gold_answer") or "").strip():
            out_items.append(item)
            print(f"[{i}/{len(lines)}] #{item.get('index')} 已有 gold，跳过", flush=True)
            continue
        gold = ""
        try:
            result = await kb_instance.aquery(query, args.kb, **retrieval_config)
            _, chunks = normalize_query_result(result)
            if chunks:
                gold = await call_with_retry(llm, build_gold_prompt(query, chunks))
        except Exception as e:
            logger.error(f"第 {item.get('index', i)} 题生成失败: {e}")
        item["gold_answer"] = gold
        item["gold_model"] = model_spec
        out_items.append(item)
        print(f"[{i}/{len(lines)}] #{item.get('index')} gold_len={len(gold)}", flush=True)

    if args.limit:
        print("冒烟模式，不写回文件。")
        return 0

    with open(path, "w", encoding="utf-8") as f:
        for item in out_items:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")
    print(f"已写回 {len(out_items)} 题 gold_answer -> {path}")
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
