"""检索速度验收脚本（一键可复现 + Markdown 展示报告）。

自动发现全部 milvus 知识库并按 query_params.options 分类（vector_only / graph），
跑三个场景：A 单库全链路（每库 × --rounds 轮，轮换业务查询）、B 最大图库固定口径
（× --rounds-b 轮，p50）、C 全库并发。产物 Markdown 报告 + jsonl 原始数据到
reports/ 目录，末尾 os._exit(0) 跳过 async 生成器终结。

用法（api-dev 容器内）:
  docker exec -w /app api-dev uv run --no-sync python -u /app/scripts/bench/retrieval_acceptance.py \
      --rounds 3 --rounds-b 10
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

STAGES = ["embedding", "milvus_search", "graph_vector", "graph_neo4j_ppr", "postgres", "hydrate", "rerank", "other"]
ACCEPT_MS = 500.0  # 验收标准：< 500ms
QUERIES = [
    "F10定位对讲一体机的技术规格和防护等级",
    "设备支持哪些通信协议和频率",
    "产品的防水等级、定位精度和待机时间",
    "系统有哪些核心功能模块",
]
REPORTS_DIR = Path(__file__).resolve().parent / "reports"

_ORIG: dict = {}  # 原始方法引用快照，供 _patch 安全重复调用


# ---------------------------------------------------------------- 统计工具

def percentile(data, p):
    data = sorted(data)
    if not data:
        return 0.0
    k = (len(data) - 1) * (p / 100.0)
    f = int(k)
    c = f + 1 if f + 1 < len(data) else f
    return data[f] + (data[c] - data[f]) * (k - f)


def _stats(ms_list: list[float]) -> dict:
    """min/avg/max + 分位数，空列表返回 None。"""
    if not ms_list:
        return None
    return {
        "min": min(ms_list),
        "avg": statistics.mean(ms_list),
        "max": max(ms_list),
        "p50": percentile(ms_list, 50),
        "p90": percentile(ms_list, 90),
        "p95": percentile(ms_list, 95),
        "p99": percentile(ms_list, 99),
        "n": len(ms_list),
    }


# ---------------------------------------------------------------- 自动发现与分类

@dataclass
class KbInfo:
    kb_id: str
    name: str
    mode: str
    actual_graph: bool
    chunk_count: int
    options: dict

    def to_dict(self) -> dict:
        return {
            "kb_id": self.kb_id,
            "name": self.name,
            "mode": self.mode,
            "actual_graph": self.actual_graph,
            "chunk_count": self.chunk_count,
            "options": self.options,
        }


def _as_bool(v) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() in ("true", "1", "yes")
    return bool(v)


def _classify(kb) -> tuple[str, bool]:
    """返回 (mode, actual_graph)。

    vector_only = search_mode==vector 且无 graph 且无 rerank；
    graph = 其余（use_graph_retrieval==True 为主信号，含 rerank 变体）；
    actual_graph 以 additional_params.graph_build_config 是否真实存在为准。
    注意：query_params 列结构为 {"options": {...}}（milvus.py:1205 用 .get("options", {}) 展平），
    不能从顶层直接取 search_mode。
    """
    qp = (kb.query_params or {}).get("options", {}) or {}
    search_mode = str(qp.get("search_mode", "hybrid")).lower()
    use_graph = _as_bool(qp.get("use_graph_retrieval", False))
    use_reranker = _as_bool(qp.get("use_reranker", False))
    actual_graph = bool((kb.additional_params or {}).get("graph_build_config"))
    if not use_graph and not use_reranker and search_mode == "vector":
        return "vector_only", actual_graph
    return "graph", actual_graph


async def discover_kbs(only: list[str] | None = None) -> list[KbInfo]:
    from yuxi.repositories.knowledge_base_repository import KnowledgeBaseRepository
    from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository

    kb_repo, file_repo = KnowledgeBaseRepository(), KnowledgeFileRepository()
    infos = []
    for kb in await kb_repo.get_all():
        if kb.kb_type not in {"milvus", None}:
            continue
        if only and kb.kb_id not in only:
            continue
        mode, actual = _classify(kb)
        stats = await file_repo.get_kb_file_stats(kb.kb_id)
        infos.append(
            KbInfo(
                kb_id=kb.kb_id,
                name=kb.name,
                mode=mode,
                actual_graph=actual,
                chunk_count=stats.get("chunk_count", 0),
                options=dict((kb.query_params or {}).get("options", {}) or {}),
            )
        )
    infos.sort(key=lambda i: -i.chunk_count)  # 便于场景B取 chunk 最多的实际图库
    return infos


async def preload_instances(infos: list[KbInfo]) -> None:
    """等价于真实服务启动时的 metadata 加载（milvus 实例单例，加载一次即可）。"""
    from yuxi.knowledge.runtime import knowledge_base

    inst = knowledge_base._get_or_create_kb_instance("milvus")
    if not getattr(inst, "_metadata_loaded", False):
        await inst._load_metadata()
    print(f"[init] 已加载知识库元数据（{len(infos)} 个被测库）", flush=True)


# ---------------------------------------------------------------- 分阶段打点

def _timer() -> dict:
    return {name: [0.0, 0] for name in STAGES}


def _wrap_async(orig, acc: dict, stage: str):
    async def wrapper(*a, **kw):
        t0 = time.perf_counter()
        try:
            return await orig(*a, **kw)
        finally:
            acc[stage][0] += time.perf_counter() - t0
            acc[stage][1] += 1
    return wrapper


def _snapshot_orig() -> None:
    """模块级保存原始方法引用，避免重复 _patch 嵌套双计。"""
    import yuxi.knowledge.implementations.milvus as milvus_mod
    from yuxi.knowledge.graphs.milvus_graph_service import MilvusGraphService
    from yuxi.knowledge.graphs.milvus_graph_vector_store import MilvusGraphVectorStore
    from yuxi.models.rerank import BaseReranker
    from yuxi.repositories.knowledge_chunk_repository import KnowledgeChunkRepository
    from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository

    _ORIG["milvus_io"] = milvus_mod._run_milvus_query_io
    _ORIG["ppr"] = MilvusGraphService.query_and_rank_chunks_by_ppr
    _ORIG["search_entities"] = MilvusGraphVectorStore.search_entities
    _ORIG["search_triples"] = MilvusGraphVectorStore.search_triples
    _ORIG["rerank"] = BaseReranker.acompute_score
    _ORIG["files"] = KnowledgeFileRepository.list_current_file_ids
    _ORIG["chunks"] = KnowledgeChunkRepository.list_by_chunk_ids


def _patch(acc: dict) -> None:
    """用 _ORIG 里的原始引用打桩，可对每场景/每库安全重复调用。"""
    import yuxi.knowledge.implementations.milvus as milvus_mod
    from yuxi.knowledge.graphs.milvus_graph_service import MilvusGraphService
    from yuxi.knowledge.graphs.milvus_graph_vector_store import MilvusGraphVectorStore
    from yuxi.models.rerank import BaseReranker
    from yuxi.repositories.knowledge_chunk_repository import KnowledgeChunkRepository
    from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository

    orig_io = _ORIG["milvus_io"]

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
    MilvusGraphService.query_and_rank_chunks_by_ppr = _wrap_async(_ORIG["ppr"], acc, "graph_neo4j_ppr")
    MilvusGraphVectorStore.search_entities = _wrap_async(_ORIG["search_entities"], acc, "graph_vector")
    MilvusGraphVectorStore.search_triples = _wrap_async(_ORIG["search_triples"], acc, "graph_vector")
    BaseReranker.acompute_score = _wrap_async(_ORIG["rerank"], acc, "rerank")
    KnowledgeFileRepository.list_current_file_ids = _wrap_async(_ORIG["files"], acc, "postgres")
    KnowledgeChunkRepository.list_by_chunk_ids = _wrap_async(_ORIG["chunks"], acc, "postgres")


def _stage_summary(acc: dict, rounds: int) -> list[dict]:
    """只输出实际命中(n>0)的阶段，按轮数取每查询均值。并发下各阶段重叠，为「每轮累计均值」。"""
    return [
        {"stage": name, "ms_per_query": acc[name][0] * 1000 / rounds, "calls": acc[name][1]}
        for name in STAGES
        if acc[name][1]
    ]


# ---------------------------------------------------------------- 三个场景

async def run_one(kb_id: str, query: str) -> tuple[float, list, Exception | None]:
    """单库全链路一次查询。只传 final_top_k，其余走各库存储的 query_params（如实反映当前配置）。"""
    from yuxi.knowledge.runtime import knowledge_base

    t0 = time.perf_counter()
    try:
        res = await knowledge_base.aquery(query, kb_id=kb_id, final_top_k=10)
        return time.perf_counter() - t0, res, None
    except Exception as e:
        return time.perf_counter() - t0, [], e


def _print_round(tag: str, kb_id: str, r: int, dt_ms: float, n: int, err) -> None:
    print(f"  [{tag}] {kb_id} r{r}: {dt_ms:.0f}ms n={n}" + (f" err={type(err).__name__}" if err else ""), flush=True)


async def _warmup(kb_id: str, query: str) -> float | None:
    """跑 1 轮不计入统计的预热查询，消除模型/连接冷启动对稳态延迟的污染。"""
    dt, _res, err = await run_one(kb_id, query)
    if not err:
        return dt * 1000
    return None


async def scenario_a(infos: list[KbInfo], rounds: int) -> list[dict]:
    rows = []
    for info in infos:
        acc = _timer()
        _patch(acc)  # 每库重新绑定 acc，拿到分库阶段数据
        warmup_ms = await _warmup(info.kb_id, QUERIES[0])
        if warmup_ms is not None:
            print(f"  [A] {info.kb_id} warm: {warmup_ms:.0f}ms（不计入统计）", flush=True)
        times, per = [], []
        for r in range(rounds):
            q = QUERIES[r % len(QUERIES)]
            dt, res, err = await run_one(info.kb_id, q)
            times.append(dt * 1000)
            per.append({"round": r + 1, "query": q, "ms": round(dt * 1000, 1), "n": len(res),
                        "error": str(err)[:120] if err else None})
            _print_round("A", info.kb_id, r + 1, dt * 1000, len(res), err)
        rows.append({"info": info, "warmup_ms": warmup_ms, "times": times, "per_round": per,
                     "stage": _stage_summary(acc, rounds)})
    return rows


async def scenario_b(infos: list[KbInfo], rounds: int, fixed_query: str) -> dict | None:
    """实际图谱库中 chunk 最多者，固定查询 × N 轮。"""
    graphs = [i for i in infos if i.actual_graph] or [i for i in infos if i.mode == "graph"]
    if not graphs:
        print("[B] 未发现图库，跳过", flush=True)
        return None
    info = max(graphs, key=lambda i: i.chunk_count)
    acc = _timer()
    _patch(acc)
    warmup_ms = await _warmup(info.kb_id, fixed_query)
    if warmup_ms is not None:
        print(f"  [B] {info.kb_id} warm: {warmup_ms:.0f}ms（不计入统计）", flush=True)
    times, per = [], []
    for r in range(rounds):
        dt, res, err = await run_one(info.kb_id, fixed_query)
        times.append(dt * 1000)
        per.append({"round": r + 1, "query": fixed_query, "ms": round(dt * 1000, 1), "n": len(res),
                    "error": str(err)[:120] if err else None})
        _print_round("B", info.kb_id, r + 1, dt * 1000, len(res), err)
    return {"info": info, "warmup_ms": warmup_ms, "times": times, "per_round": per,
            "stage": _stage_summary(acc, rounds)}


async def scenario_c(infos: list[KbInfo], rounds: int) -> dict:
    from yuxi.knowledge.runtime import knowledge_base

    acc = _timer()
    _patch(acc)
    times, per = [], []
    for r in range(rounds):
        q = QUERIES[r % len(QUERIES)]
        t0 = time.perf_counter()
        grouped = await asyncio.gather(
            *(knowledge_base.aquery(q, kb_id=i.kb_id, final_top_k=10) for i in infos),
            return_exceptions=True,
        )
        total = (time.perf_counter() - t0) * 1000
        ok = [g for g in grouped if not isinstance(g, Exception)]
        errs = [g for g in grouped if isinstance(g, Exception)]
        times.append(total)
        per.append({"round": r + 1, "query": q, "ms": round(total, 1), "ok": len(ok), "errs": len(errs),
                    "n": sum(len(g) for g in ok),
                    "errors": [f"{type(e).__name__}:{str(e)[:120]}" for e in errs[:2]]})
        print(f"  [C] r{r + 1}: {total:.0f}ms ok={len(ok)}/{len(infos)} n={sum(len(g) for g in ok)}", flush=True)
    return {"times": times, "per_round": per, "stage": _stage_summary(acc, rounds)}


# ---------------------------------------------------------------- 报告生成

def _verdict(mode: str, ms: float) -> str:
    """vector_only: <500→✅ 否则 ❌；graph: <500→✅ 否则 ⚠️（如实标注，不掩盖）。"""
    if mode == "vector_only":
        return "✅" if ms < ACCEPT_MS else "❌"
    return "✅" if ms < ACCEPT_MS else "⚠️"


def _stage_bars(stage: list[dict], width: int = 30) -> list[str]:
    if not stage:
        return ["（无阶段数据）"]
    peak = max(s["ms_per_query"] for s in stage) or 1.0
    lines = ["阶段耗时（每查询均值）:"]
    for s in stage:
        bar = "█" * max(int(s["ms_per_query"] / peak * width), 1)
        lines.append(f"  {s['stage']:<16} {s['ms_per_query']:>7.0f}ms {bar}")
    return lines


def build_report(args, infos, A, B, C) -> dict:
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cmd = ("docker exec -w /app api-dev uv run --no-sync python -u "
           f"/app/scripts/bench/retrieval_acceptance.py --rounds {args.rounds} --rounds-b {args.rounds_b}")
    return {
        "generated_at": stamp,
        "command": cmd,
        "rounds_a": args.rounds,
        "rounds_b": args.rounds_b,
        "queries": QUERIES,
        "kbs": [i.to_dict() for i in infos],
        "A": [{"info": a["info"].to_dict(), "warmup_ms": a["warmup_ms"], "times": a["times"],
               "per_round": a["per_round"], "stage": a["stage"]} for a in A],
        "B": None if B is None else {"info": B["info"].to_dict(), "warmup_ms": B["warmup_ms"], "times": B["times"],
                                     "per_round": B["per_round"], "stage": B["stage"]},
        "C": {"times": C["times"], "per_round": C["per_round"], "stage": C["stage"]},
    }


def write_jsonl(path: Path, report: dict) -> None:
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _cfg_str(options: dict) -> str:
    return (f"mode={options.get('search_mode')} graph={options.get('use_graph_retrieval')} "
            f"rerank={options.get('use_reranker')}")


def write_markdown(report: dict, md_path: Path, args) -> None:
    L = ["# 智知库（YUXI）检索速度验收展示报告", "",
         f"> 验收项：检索响应耗时 **< {ACCEPT_MS:.0f}ms**",
         f"> 生成日期：{report['generated_at']}",
         "> 运行环境：Docker Compose 全栈（api-dev 容器）",
         "> 生成脚本：`/app/scripts/bench/retrieval_acceptance.py`", ""]

    L += ["## 一、被测范围（自动发现）", "",
          "| 名称 | kb_id | 类型 | 实际图谱 | chunk_count | 检索配置 |",
          "|:---:|:---|:---:|:---:|:---:|:---|"]
    for k in report["kbs"]:
        L.append(f"| {k['name']} | {k['kb_id']} | {k['mode']} | {'是' if k['actual_graph'] else '否'} "
                 f"| {k['chunk_count']} | {_cfg_str(k['options'])} |")

    L += ["", "## 二、场景A 单库全链路（每库先跑 1 轮预热不计入统计，再 × N 轮轮换业务查询）", "",
          "| 知识库 | 类型 | 预热 | min | avg | max | p50 | 达标 |",
          "|:---:|:---:|--:|--:|--:|--:|--:|:---:|"]
    for a in report["A"]:
        s = _stats(a["times"])
        warm = f"{a['warmup_ms']:.0f}" if a.get("warmup_ms") is not None else "-"
        L.append(f"| {a['info']['name']} | {a['info']['mode']} | {warm} | {s['min']:.0f} | {s['avg']:.0f} "
                 f"| {s['max']:.0f} | {s['p50']:.0f} | {_verdict(a['info']['mode'], s['avg'])} |")

    L += ["", "## 三、场景B 最大图库固定口径（预热后 × N 轮，原始数据 + p50 + 阶段构成）", ""]
    if report["B"]:
        b = report["B"]
        s = _stats(b["times"])
        warm = f"（预热 {b['warmup_ms']:.0f}ms）" if b.get("warmup_ms") is not None else ""
        L += [f"- 库：`{b['info']['kb_id']}` {b['info']['name']}（chunk_count={b['info']['chunk_count']}）{warm}",
              f"- 原始数据(ms)：{' '.join(f'{t:.0f}' for t in b['times'])}",
              f"- min/avg/max：{s['min']:.0f} / {s['avg']:.0f} / {s['max']:.0f}，"
              f"p50={s['p50']:.0f}，p90/p95/p99={s['p90']:.0f}/{s['p95']:.0f}/{s['p99']:.0f}",
              ""]
        if not args.no_bar:
            L += ["```"] + _stage_bars(b["stage"]) + ["```"]
    else:
        L.append("- 未发现图库，跳过")

    L += ["", "## 四、场景C 全库并发（N 库并行，特殊场景）", "",
          "| 轮次 | ms | ok/总数 | 返回条数 |", "|:--:|--:|:--:|:--:|"]
    for c in report["C"]["per_round"]:
        L.append(f"| {c['round']} | {c['ms']:.0f} | {c['ok']}/{len(report['kbs'])} | {c['n']} |")
    L += ["", "> 注：全库并发为「一次问全库」的特殊场景，耗时受多库并行外部 Embedding 排队主导，",
          "> **不纳入 <500ms 验收**。"]

    L += ["", "## 五、验收结论", "",
          "| 验收标准 | 场景 | 实测 | 达标 |",
          "|:---:|:---|:---|:---:|"]
    for a in report["A"]:
        s = _stats(a["times"])
        verdict = _verdict(a["info"]["mode"], s["avg"])
        L.append(f"| <{ACCEPT_MS:.0f}ms | A·单库 {a['info']['name']} | avg {s['avg']:.0f}ms | {verdict} |")
    if report["B"]:
        s = _stats(report["B"]["times"])
        L.append(f"| <{ACCEPT_MS:.0f}ms | B·最大图库 | p50 {s['p50']:.0f}ms | {_verdict('graph', s['p50'])} |")
    L.append("| — | C·全库并发 | — | 特殊场景不纳入 |")

    L += ["", "## 附：复现命令", "", "```bash",
          report["command"], "```", ""]
    md_path.write_text("\n".join(L), encoding="utf-8")


# ---------------------------------------------------------------- 入口

async def main() -> None:
    parser = argparse.ArgumentParser(description="检索速度验收（自动发现 + 三场景 + Markdown 报告）")
    parser.add_argument("--rounds", type=int, default=3, help="场景A 每库轮数 + 场景C 轮数")
    parser.add_argument("--rounds-b", type=int, default=10, help="场景B 最大图库固定口径轮数")
    parser.add_argument("--kb", action="append", default=None, help="限定 kb_id（可多次），默认自动发现全部 milvus 库")
    parser.add_argument("--query-b", default=QUERIES[0], help="场景B 固定查询（默认第一条业务查询）")
    parser.add_argument("--out-prefix", default="retrieval_acceptance")
    parser.add_argument("--no-bar", action="store_true", help="关闭 ASCII 条形图")
    parser.add_argument("--no-latest", action="store_true", help="不写 *_latest.md 稳定指针")
    args = parser.parse_args()

    infos = await discover_kbs(args.kb)
    if not infos:
        print("未发现 milvus 知识库，退出", flush=True)
        return
    print(f"=== 检索速度验收 · 自动发现 {len(infos)} 个库 ===", flush=True)
    for i in infos:
        print(f"  {i.kb_id} {i.name} mode={i.mode} actual_graph={i.actual_graph} chunks={i.chunk_count}", flush=True)

    await preload_instances(infos)
    _snapshot_orig()

    print(f"\n=== 场景A 单库全链路 × {args.rounds} 轮 ===", flush=True)
    A = await scenario_a(infos, args.rounds)
    print(f"\n=== 场景B 最大图库固定口径 × {args.rounds_b} 轮 ===", flush=True)
    B = await scenario_b(infos, args.rounds_b, args.query_b)
    print(f"\n=== 场景C 全库并发 × {args.rounds} 轮 ===", flush=True)
    C = await scenario_c(infos, args.rounds)

    report = build_report(args, infos, A, B, C)
    reports_dir = REPORTS_DIR
    reports_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = f"{args.out_prefix}_{stamp}"
    jsonl_path = reports_dir / f"{base}.jsonl"
    md_path = reports_dir / f"{base}.md"
    write_jsonl(jsonl_path, report)
    write_markdown(report, md_path, args)
    if not args.no_latest:
        import shutil
        shutil.copy(md_path, reports_dir / f"{args.out_prefix}_latest.md")
    print(f"\n报告: {md_path}", flush=True)
    print(f"原始数据: {jsonl_path}", flush=True)
    os._exit(0)  # 跳过 async 生成器终结（astream_events 在 shutdown_asyncgens 会挂起）


if __name__ == "__main__":
    asyncio.run(main())
