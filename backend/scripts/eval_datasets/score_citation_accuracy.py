#!/usr/bin/env python3
"""引用准确率（Hit@5）评分：前 5 位引用来源条目中是否有可支撑正确答案的有效引用。

对应合同附件 1 的验收指标「引用准确率 ≥ 95%（Hit@5）」。此前该指标在本仓库没有可计算的
实现：唯一的 citation_recall@1/@5（score_agent_results.py）走 gold_chunk_ids 对实检索 chunk
的口径，而现有题集全部没有 gold_chunk_ids，报告里一律 N/A。

口径（与业务方对齐）：
- 引用条目 = 检索片段 chunk 级（前端来源卡片内 #1/#2 行），不是文档卡片级。实测每题只引用
  1 个文档，文档级会让「前 5」退化成 Hit@1。
- 前 5 位来源条目 = 被引用文档按引用在回答中出现的先后排序，同一文档内按相关度（score）降序，
  取前 5。排序与相似度判定沿用前端口径。
- 命中判定 = LLM judge 逐条判「该片段是否可支撑甲方标准答案」；前 5 条中任一条判为可支撑，
  该题即计为引用正确（命中即短路，不再判后续条目）。
- 引用准确率 = 引用正确题数 ÷ 含引用标注的题数 × 100%。答案解析不出任何引用标注的题
  （含拒答）不计入分母。

与前端的两处有意差异（均为评测口径所需，非缺陷）：

1. **判定方向相反**：前端从回答切出候选文档名，再拿去匹配检索片段；本脚本反过来，拿本轮检索到的
   文件去比对回答的引用标注区。前端那样切是因为它只能拿到回答文本；本脚本手里有闭集（实测每题
   只检索到 1~2 个文件），反向比对既更稳也更简单。前端的切分在「文件名.docx（分组）——2.6 个人中心」
   这种形态下会把文件名切掉（它用 `——` 跳过「以上材料整理——」式引导语，而 `——` 同时被用作
   「文件名——章节号」的分隔），前端靠「匹配不到就回退展示全量」兜底；照搬该切分会把指标变成
   前端解析器的怪癖测试。文件名归一化与相似度阈值仍沿用前端；比对标注片段时保留括号内容，因为
   文件名常写在括号内（「来源：客服知识库（客服知识库.md）」）。
2. **不回退**：前端匹配不到任何片段时回退展示全量来源，那是为了防误删面板内容；本脚本不回退——
   宁可一条都不判，也不拿未被引用的检索结果冒充引用。这类题单独统计为 unresolved_citation。

另：本脚本消费的 run_agent_e2e.py 产物把相关度分数放在 metadata.score，故按「顶层 score 优先、
metadata.score 兜底」取值（与前端 groupKnowledgeChunksByDocument 的 scoreOf 同口径）。

输入：run_agent_e2e.py 产出的端到端结果 JSONL（query/gold_answer/section/agent_answer/retrieved_chunks）。
输出：JSON + Markdown 报告（口径定义、汇总、分域汇总、每题明细、未命中清单、说明与边界）。

用法（容器内，复用 yuxi 模型接线）：
  docker exec api-dev python /app/scripts/eval_datasets/score_citation_accuracy.py \
      --results /app/scripts/eval_datasets/synthetic/agent_e2e_citation50_20260928.jsonl \
      --output /app/scripts/eval_datasets/reports \
      --name citation50_20260928
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import unicodedata
from pathlib import Path

DEFAULT_JUDGE = "deepseek:deepseek-v4-flash"
DEFAULT_OUTPUT = "/app/scripts/eval_datasets/reports"
TOP_K = 5
# 喂给 judge 的片段上限。取值必须高于真实片段长度：表格类片段常在末尾才出现关键行，
# 上限压到 2000 时 50 题里有 35 条条目（25%，涉及 17 题）被截断，其中确有一条的关键行
# （「公司管理|公司列表|修改密码」）落在截断点之后，judge 因看不到而判为不支持——这是
# 判定侧的假阴性，不是被测系统的问题。实测条目最长 5174 字符，留出余量取 8000。
CONTENT_LIMIT = 8000
SIMILARITY_THRESHOLD = 0.8
# 表格行首格出现这些词才视为引用行（跳过表头与分隔行）；与前端 docKwRe 一致
_DOC_KEYWORD_RE = re.compile(
    r"规格书|datasheet|说明书|解决方案|白皮书|部署指南|指南|手册|工卡|一纸禅|协议|介绍|报告|规格|证书|认证|单页|资料|白皮",
    re.IGNORECASE,
)
_SOURCE_SECTION_RE = re.compile(r"来源说明|依据来源|参考来源|资料来源|引用来源|参考文献|来源\s*[*_]{0,2}\s*[：:]")
# 独立成行的来源标题（`## 来源`、`**来源**`、`来源：`），清单另起一行跟在后面。
# 这类回答里「来源」二字后面没有冒号与正文，上面的行内式匹配不到，整段引用会被漏掉。
_SOURCE_HEADING_RE = re.compile(
    r"^[ \t]*(?:#{1,6}[ \t]*)?(?:\*\*|__|\*|_)?[ \t]*来源[ \t]*(?:\*\*|__|\*|_)?[ \t]*[：:]?[ \t]*$",
    re.MULTILINE,
)
# 「来源」段落只取这么多字符，且到第一个句号为止：句号之后是补充说明/反问句，不属于引用清单
_SOURCE_SECTION_LIMIT = 800


def build_judge_prompt(question: str, gold: str, doc: str, content: str) -> str:
    """构造 judge 单轮 prompt：判定被引用来源证据能否支撑标准答案，输出严格 JSON。"""
    if len(content) > CONTENT_LIMIT:
        content = content[:CONTENT_LIMIT] + "\n…（片段过长已截断）"
    return f"""你是引用准确率的客观判定员。给定问题、标准答案，以及系统回答中标注的一条引用来源证据，
判断「该来源证据能否支撑标准答案」。

【问题】
{question}

【标准答案】（判定基准）
{gold}

【被引用的来源片段】
来源文档：{doc}
片段正文：
{content}

判定要求：
- 只看该片段本身是否包含标准答案所需的关键事实，语义等价即可，不要求逐字一致；
- 片段与标准答案矛盾、只沾边（关键词相同但说的不是同一件事）、或完全无关 → 不支持；
- 不要因为片段里出现了问题中的词就判支持，必须是能支撑标准答案核心结论的内容。

只输出如下 JSON（不要任何其他文字、不要 Markdown 代码块）：
{{"supported": true 或 false, "reason": "一句话说明判定依据"}}"""


def citation_spans(text: str) -> list[str]:
    """回答里所有引用标注片段，按在正文中出现的先后返回。

    四种标注形态（与前端 extractCitationNames 一致）：`《...》`、表格中含文档特征词的行首格、
    行内式「来源说明/依据来源：…」段落、独立成行的「来源」标题（清单在其后一行）。
    段落与标题都取正文中位置最靠后的一个，引用清单通常在回答末尾。
    这里只负责切出标注区，不再往下切成候选文档名——候选由检索到的文件反向比对（见 build_cited_entries）。
    """
    spans: list[tuple[int, str]] = [(m.start(), m.group(1)) for m in re.finditer(r"《([^》]+)》", text)]

    for row in re.finditer(r"^[ \t]*\|.*\|[ \t]*$", text, re.MULTILINE):
        cells = [c.strip() for c in row.group(0).strip().strip("|").split("|")]
        if len(cells) >= 2 and _DOC_KEYWORD_RE.search(cells[0]):
            spans.append((row.start(), cells[0]))

    sections = [*_SOURCE_SECTION_RE.finditer(text), *_SOURCE_HEADING_RE.finditer(text)]
    if sections:
        last = max(sections, key=lambda m: m.start())
        section = text[last.start() : last.start() + _SOURCE_SECTION_LIMIT]
        # 文档清单在句号处结束，句号之后是补充说明，不属于引用
        period = section.find("。")
        if period != -1:
            section = section[:period]
        # 清单后若空行再接散文/反问句，说明清单已结束，避免把反问句里的产品名当成被引用的文档
        para_break = re.search(r"\n\s*\n(?![ \t]*(?:-|\*|•|\d+[.、]))", section)
        if para_break:
            section = section[: para_break.start()]
        spans.append((last.start(), section))

    return [span for _, span in sorted(spans)]


def normalize_doc_name(raw: str) -> str:
    """文件名/引用名归一化（前端 normalizeDocName 的 Python 移植）。

    去括号注释 → 小写 → 去空白标点符号与下划线 → 去扩展名 → 去尾部版本/日期。
    例：'Miniserver M200规格书（更新日期 2025-11-25）.xlsx' → 'miniserverm200规格书'
    """
    if not raw:
        return ""
    s = re.sub(r"[（(][^（）()]*[）)]", "", str(raw)).lower()
    # JS 用 \p{P}\p{S} 匹配标点与符号类，Python re 不支持该属性转义，按 Unicode 类别过滤
    s = "".join(ch for ch in s if not ch.isspace() and ch != "_" and unicodedata.category(ch)[0] not in "PS")
    s = re.sub(r"(pdf|docx?|xlsx?|pptx?)$", "", s)
    return re.sub(r"(?:v\d+(?:\.\d+)*|\d{4,8})?$", "", s)


def levenshtein(a: str, b: str) -> int:
    """编辑距离（前端 levenshtein 的 Python 移植），用于近似匹配模型改写过的文档名。"""
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _flatten(text: str) -> str:
    """压平成可做子串比对的形态：小写、去空白与标点符号。与 normalize_doc_name 不同，保留括号内的内容——
    标注片段里的文件名常写在括号内（「来源：客服知识库（客服知识库.md）」），去掉括号内容会连文件名一起丢掉。
    """
    return "".join(ch for ch in text.lower() if not ch.isspace() and unicodedata.category(ch)[0] not in "PS")


def span_cites_doc(span: str, doc_core: str) -> bool:
    """标注片段里是否提到了这份文档：文件名出现在片段中，或与片段里的名字近似同名（阈值沿用前端）。

    过短的文件名不参与判定：名字越短，「出现在片段里」越容易偶然成立。
    """
    if len(doc_core) < 4:
        return False
    if doc_core in _flatten(span):
        return True
    cited = normalize_doc_name(span)
    if len(cited) >= 4 and (cited in doc_core or doc_core in cited):
        return True
    longest = max(len(doc_core), len(cited))
    return 1 - levenshtein(doc_core, cited) / longest >= SIMILARITY_THRESHOLD


def _chunk_score(chunk: dict) -> float:
    """片段相关度：顶层 score 优先，metadata.score 兜底（runner 产物把分数放在 metadata）。"""
    metadata = chunk.get("metadata") if isinstance(chunk.get("metadata"), dict) else {}
    for value in (chunk.get("score"), metadata.get("score")):
        if isinstance(value, (int, float)):
            return float(value)
    return float("-inf")


def _file_names(record: dict) -> dict[str, str]:
    """file_id → 文件名，取自本轮带 source 的片段。

    find_kb_document/open_kb_document 的窗口片段自身没有 source（后端 schema 里有该字段，
    但 runner 采集时只带了 file_id/kb_id），前端同样按 file_id 回查同轮检索结果来补名字
    （messageProcessor.js 的 fileInfoMap）。不补名字的话这些片段一律归不到任何被引用的文档，
    等于把模型真正读过的原文证据整段丢掉——实测 50 题里 340 条窗口片段有 260 条可由
    file_id 还原，分布在 29 题上。
    """
    names: dict[str, str] = {}
    for chunk in record.get("retrieved_chunks") or []:
        metadata = chunk.get("metadata") if isinstance(chunk.get("metadata"), dict) else {}
        source = str(metadata.get("source") or "")
        file_id = str(chunk.get("file_id") or metadata.get("file_id") or "")
        if source and file_id:
            names.setdefault(file_id, source)
    return names


def _doc_name(chunk: dict, names: dict[str, str]) -> str:
    """片段所属文件名（basename）：优先 metadata.source，缺失时用 file_id 回查本轮文件名。"""
    metadata = chunk.get("metadata") if isinstance(chunk.get("metadata"), dict) else {}
    source = str(metadata.get("source") or "")
    if not source:
        file_id = str(chunk.get("file_id") or metadata.get("file_id") or "")
        source = names.get(file_id, "")
    return re.split(r"[\\/]", source).pop() if source else ""


def _entry_of(chunk: dict, names: dict[str, str]) -> dict:
    """把检索片段转成报告里的一条引用条目。"""
    metadata = chunk.get("metadata") if isinstance(chunk.get("metadata"), dict) else {}
    return {
        "doc": _doc_name(chunk, names),
        "chunk_id": str(metadata.get("chunk_id") or chunk.get("id") or ""),
        "content": chunk.get("content") or "",
    }


def build_cited_entries(record: dict, limit: int = TOP_K) -> tuple[list[str], list[dict]]:
    """构造「前 N 位引用来源条目」，返回 (引用标注片段, 条目)。

    条目顺序 = 引用标注在回答中出现的先后，同一标注内的片段按相关度降序（前端口径）。归属用反向
    比对：拿本轮检索到的文件逐个问「它被引用了吗」，而不是从回答里切出文档名再回匹配——本轮实测
    只检索到 1~2 个文件，闭集小到可以这么问，且不受模型标注格式的影响。
    对不上任何标注的片段不进条目，对不上任何片段的标注题单独统计为 unresolved_citation。
    """
    answer = record.get("agent_answer") or ""
    spans = citation_spans(answer)
    if not spans:
        return [], []

    names = _file_names(record)
    cited: list[list[dict]] = [[] for _ in spans]
    for chunk in record.get("retrieved_chunks") or []:
        core = normalize_doc_name(_doc_name(chunk, names))
        if not core:
            continue
        for index, span in enumerate(spans):
            if span_cites_doc(span, core):
                cited[index].append(chunk)
                break

    entries: list[dict] = []
    for group in cited:
        # 窗口片段没有相关度分数（-inf），稳定排序后落在同文档检索片段之后，与前端一致
        for chunk in sorted(group, key=_chunk_score, reverse=True):
            entries.append(_entry_of(chunk, names))
            if len(entries) >= limit:
                return spans, entries
    return spans, entries


def build_cited_document_entries(record: dict, limit: int = TOP_K) -> tuple[list[str], list[dict]]:
    """按被引用文档聚合本轮已采集的片段，返回前 N 个文档证据。"""
    answer = record.get("agent_answer") or ""
    spans = citation_spans(answer)
    if not spans:
        return [], []

    names = _file_names(record)
    grouped: list[list[dict]] = [[] for _ in spans]
    for chunk in record.get("retrieved_chunks") or []:
        core = normalize_doc_name(_doc_name(chunk, names))
        if not core:
            continue
        for index, span in enumerate(spans):
            if span_cites_doc(span, core):
                grouped[index].append(chunk)
                break

    entries: list[dict] = []
    for chunks in grouped:
        if not chunks:
            continue
        doc = _doc_name(chunks[0], names)
        # 合并后的正文会超 judge 输入上限而被截断，故按相关度降序拼，保证最相关的证据落在限额内
        seen: set[str] = set()
        contents: list[str] = []
        chunk_ids: list[str] = []
        for chunk in sorted(chunks, key=_chunk_score, reverse=True):
            content = str(chunk.get("content") or "")
            chunk_id = str((chunk.get("metadata") or {}).get("chunk_id") or chunk.get("id") or "")
            key = f"{chunk_id}\0{content}"
            if key in seen:
                continue
            seen.add(key)
            chunk_ids.append(chunk_id)
            if content:
                contents.append(content)
        entries.append({"doc": doc, "chunk_ids": chunk_ids, "content": "\n\n".join(contents)})
        if len(entries) >= limit:
            break
    return spans, entries


def parse_judge(text: str) -> dict:
    """校验 judge 返回，规整成 {supported, reason}。"""
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*", "", t)
        t = re.sub(r"\s*```$", "", t)
    start, end = t.find("{"), t.rfind("}")
    if start == -1 or end <= start:
        raise ValueError(f"输出中没有 JSON 对象: {t[:120]}")
    raw = json.loads(t[start : end + 1])
    supported = raw.get("supported")
    if not isinstance(supported, bool):
        raise ValueError(f"supported 非布尔值: {supported!r}")
    return {"supported": supported, "reason": str(raw.get("reason") or "")}


async def judge_entry(llm, question: str, gold: str, entry: dict, semaphore: asyncio.Semaphore) -> dict:
    """判定单条引用片段能否支撑标准答案。"""
    prompt = build_judge_prompt(question, gold, entry["doc"], entry["content"])
    async with semaphore:
        last_err: Exception | None = None
        for attempt in range(2):
            try:
                # 显式超时：judge 调用偶发挂起，无超时会导致整轮评分卡死
                resp = await asyncio.wait_for(llm.ainvoke(prompt), timeout=120)
                parsed = parse_judge(str(resp.content))
                parsed["judge_error"] = None
                return parsed
            except Exception as e:
                last_err = e
                if attempt == 0:
                    prompt += "\n\n（上一次输出无法解析为指定 JSON，请严格只输出指定 JSON 对象。）"
        return {"supported": False, "reason": "", "judge_error": str(last_err)}


async def score_one(llm, record: dict, semaphore: asyncio.Semaphore, index: int, *, document_level: bool = False) -> dict:
    """对单题判定引用条目；文档级模式先合并同文档的本轮证据。"""
    spans, entries = (
        build_cited_document_entries(record) if document_level else build_cited_entries(record)
    )
    item = {
        "index": index,
        "section": record.get("section") or "未知",
        "query": record.get("query") or "",
        "gold_answer": record.get("gold_answer") or "",
        "agent_answer": record.get("agent_answer") or "",
        "cited_spans": spans,
        "entries": entries,
        "hit": False,
        "hit_position": None,
        "unresolved_citation": False,
        "judge_error": None,
    }
    if not entries:
        # 有引用标注却一条都对不上检索结果：不进分母，但要能被看见（见报告「说明与边界」）
        item["unresolved_citation"] = bool(spans)
        return item

    for position, entry in enumerate(entries, 1):
        judged = await judge_entry(llm, item["query"], item["gold_answer"], entry, semaphore)
        entry.update(judged)
        if judged["judge_error"] and item["judge_error"] is None:
            item["judge_error"] = judged["judge_error"]
        if judged["supported"]:
            item["hit"] = True
            item["hit_position"] = position
            break
    # 短路后未判定的条目补空，保证明细结构一致
    for entry in entries:
        entry.setdefault("supported", None)
        entry.setdefault("reason", "")
    return item


def _pct(value: float | None) -> str:
    return "-" if value is None else f"{value * 100:.1f}%"


def _fmt(text: str | None, limit: int = 60) -> str:
    t = " ".join((text or "").split())
    return t if len(t) <= limit else t[: limit - 1] + "…"


def summarize(items: list[dict], threshold: float) -> dict:
    """汇总口径：分母 = 含引用标注的题数；分子 = 前 N 条引用条目中命中至少一条的题数。"""
    with_citation = [it for it in items if it["entries"]]
    hits = [it for it in with_citation if it["hit"]]
    accuracy = len(hits) / len(with_citation) if with_citation else None
    return {
        "denominator": len(with_citation),
        "hit": len(hits),
        "accuracy": accuracy,
        "threshold": threshold,
        "passed": accuracy is not None and accuracy >= threshold,
        "no_citation": sum(1 for it in items if not it["cited_spans"]),
        "unresolved_citation": sum(1 for it in items if it["unresolved_citation"]),
        "entries_judged": sum(1 for it in items for e in it["entries"] if e.get("supported") is not None),
        "judge_error": sum(1 for it in items if it["judge_error"]),
    }


def build_report(
    items: list[dict],
    overall: dict,
    name: str,
    judge_llm: str,
    results_path: str,
    *,
    document_level: bool = False,
) -> tuple[str, str]:
    """生成 Markdown 报告与同源 JSON。"""
    granularity = "文档级" if document_level else "chunk 级"
    lines = [
        f"# 引用准确率（{granularity} Hit@{TOP_K}）评测报告 — {name}",
        "",
        "## 口径定义",
        "",
        f"- **引用准确率 = 引用正确的问题数 ÷ 含引用标注的问题总数 × 100%**，验收阈值 ≥ {_pct(overall['threshold'])}。",
        (
            f"- **引用正确**：系统输出引用所标注的前 {TOP_K} 个来源文档中，包含可支撑正确答案的有效来源。"
            if document_level
            else f"- **引用正确**：系统输出引用所标注的前 {TOP_K} 位来源条目中，包含可支撑正确答案的有效引用。"
        ),
        (
            "- **来源条目 = 文档级**：同一被引用文档的本轮检索片段先合并，再由 judge 判定；不读取本轮结果之外的全文。"
            if document_level
            else "- **来源条目 = 检索片段（chunk）级**，顺序按前端口径：被引用文档按引用在回答中首次出现的位置排序，"
            "同一文档内按相关度降序，与前端口径（web/src/utils/messageProcessor.js）逐条对齐。"
        ),
        (
            f"- **命中判定 = LLM judge 判定文档证据**，前 {TOP_K} 个文档中任一文档判为可支撑即命中。"
            if document_level
            else f"- **命中判定 = LLM judge 逐条判定**「该片段能否支撑甲方标准答案」，前 {TOP_K} 条中任一条"
            "判为可支撑即命中（命中即短路，不再判后续条目）。"
        ),
        "- **分母只含引用标注的题**：答案解析不出任何引用标注的题（含拒答/澄清）不计入。"
        "引用标注按前端口径切出（`《》`引用、表格引用行、行内「来源…：」段、独立成行的「来源」标题及其后清单），再与本次检索到的文件"
        "逐个比对，对不上的标注不产生条目、该类题单独统计。",
        f"- judge 模型：`{judge_llm}`；被测结果：`{results_path}`。",
        "",
        "## 汇总",
        "",
        "| 项 | 值 |",
        "|---|---|",
        f"| 引用准确率 | **{_pct(overall['accuracy'])}** |",
        f"| 验收阈值 | ≥ {_pct(overall['threshold'])} |",
        f"| 结论 | {'达标 ✅' if overall['passed'] else '未达标 ❌'} |",
        f"| 引用正确 / 含引用标注 | {overall['hit']} / {overall['denominator']} |",
        f"| 无引用标注（不计入分母） | {overall['no_citation']} |",
        f"| 有引用标注但一条都对不上检索片段（不计入分母） | {overall['unresolved_citation']} |",
        f"| 判定条目数 | {overall['entries_judged']} |",
        f"| judge 失败题数 | {overall['judge_error']} |",
        "",
    ]

    by_section: dict[str, list[dict]] = {}
    for it in items:
        by_section.setdefault(it["section"], []).append(it)
    lines += [
        "## 分域汇总",
        "",
        "| 域 | 引用正确 / 含引用标注 | 引用准确率 | 无引用标注 | 对不上片段 |",
        "|---|---|---|---|---|",
    ]
    for section, group in sorted(by_section.items()):
        with_citation = [it for it in group if it["entries"]]
        hits = sum(1 for it in with_citation if it["hit"])
        acc = hits / len(with_citation) if with_citation else None
        lines.append(
            f"| {section} | {hits} / {len(with_citation)} | {_pct(acc)} | "
            f"{sum(1 for it in group if not it['cited_spans'])} | "
            f"{sum(1 for it in group if it['unresolved_citation'])} |"
        )
    lines.append("")

    lines += ["## 每题明细", ""]
    for it in items:
        if not it["entries"]:
            lines.append(f"### {it['index']}. [{it['section']}] {_fmt(it['query'], 80)}")
            lines.append("")
            if it["unresolved_citation"]:
                quoted = "；".join(_fmt(span, 40) for span in it["cited_spans"])
                lines.append(f"引用标注一条都对不上检索片段 → 不计入分母；标注原文：{quoted}")
            else:
                lines.append("无引用标注 → 不计入分母")
            lines.append("")
            continue
        verdict = f"✅ 引用正确（命中第 {it['hit_position']} 条）" if it["hit"] else "❌ 引用不正确"
        quoted = "；".join(_fmt(span, 40) for span in it["cited_spans"])
        lines += [
            f"### {it['index']}. [{it['section']}] {_fmt(it['query'], 80)}",
            "",
            f"- 判定：{verdict}",
            f"- 引用标注：{quoted}",
            "",
            "| # | 来源文档 | chunk_id | 可支撑 | 理由 |",
            "|---|---|---|---|---|",
        ]
        for position, entry in enumerate(it["entries"], 1):
            supported = entry.get("supported")
            chunk_label = ", ".join(entry.get("chunk_ids", [])) or entry.get("chunk_id", "-")
            lines.append(
                f"| {position} | {_fmt(entry['doc'], 40)} | {_fmt(chunk_label, 40)} | "
                f"{'-' if supported is None else ('是' if supported else '否')} | {_fmt(entry['reason'], 80)} |"
            )
        lines.append("")

    missed = [it for it in items if it["entries"] and not it["hit"]]
    lines += ["## 未命中清单", ""]
    if missed:
        for it in missed:
            reasons = "；".join(_fmt(e.get("reason"), 80) for e in it["entries"] if e.get("reason"))
            lines.append(f"- **{it['index']}. [{it['section']}]** {_fmt(it['query'], 60)} — {reasons or '无判定理由'}")
    else:
        lines.append("无：含引用标注的题目全部命中。")
    lines += [
        "",
        "## 说明与边界",
        "",
        f"- **「前 {TOP_K}」在当前数据形态下接近「前 1」**：实测每题只引用 1 个文档，同文档内前 {TOP_K} 条"
        "多来自同一份材料。这是客服/终端题集的形态决定的，不是实现问题。",
        "- **系统没有页码字段**：chunk 元数据只有 source/chunk_id/file_id/chunk_index/score，提示词亦禁止编造页码，"
        "故「文档级 + 页码/章节关键词」里的位置线索只能通过被引用片段的正文体现，无独立页码可判。",
        "- **排序口径**：本报告按前端的过滤函数 `filterKnowledgeChunksByAnswer` 的顺序取前 "
        f"{TOP_K}（被引用文档按引用在回答中首次出现的位置排序，组内按相关度降序）。来源面板本身"
        "是「按文档分组 + 组间按组内最高分降序」渲染的，两种顺序会选出不同的前 5 条；本数据上"
        "两种口径相差 1 题，量级结论不变。",
        "- **judge 结果受 judge 模型影响，且有抖动**：同一条目两次独立判定的结论并非总是一致，"
        "同数据重跑会有小幅波动（本数据上条目级一致率约 97%，题级翻转约 7%，分母 30 时相当于 ±2 题）。"
        "跨模型数字不可直接横比；换 judge 模型或作正式结论前需整轮重跑并复核未命中清单。",
        f"- **有引用标注但一条都对不上检索片段的题**（本次 {overall['unresolved_citation']} 题）不计入分母。"
        "这类题多数是引用清单里只有知识库名/章节号、没有可归因的文件名——前端同样不把它们渲染成来源卡片；"
        "但若该数字偏高，说明答案的出处标注正在退化，应单独排查。",
        "- **本报告不替代合同验收结论**：合同口径为「引用正确率 ≥ 95%」，其判定方法与数据来源由合同约定，"
        "本报告给出的是同一指标在本项目题集与实现上的可复现度量。",
        "",
    ]
    return "\n".join(lines), json.dumps(
        {
            "run_name": name,
            "judge_llm": judge_llm,
            "results_path": results_path,
            "metric": f"citation_accuracy@hit{TOP_K}",
            "granularity": "document" if document_level else "chunk",
            "overall": overall,
            "items": items,
        },
        ensure_ascii=False,
        indent=2,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=f"引用准确率（Hit@{TOP_K}）评分")
    parser.add_argument("--results", required=True, help="run_agent_e2e.py 输出的 JSONL")
    parser.add_argument("--judge-llm", default=DEFAULT_JUDGE, help="judge 模型 spec")
    parser.add_argument("--max-tokens", type=int, default=8192, help="judge LLM 输出上限")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--threshold", type=float, default=0.95, help="验收阈值（默认 0.95）")
    parser.add_argument("--document-level", action="store_true", help="按文档聚合本轮检索片段后评分")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="报告输出目录")
    parser.add_argument("--name", required=True, help="报告文件名/运行名，如 citation50_20260928")
    args = parser.parse_args()

    records = [json.loads(line) for line in Path(args.results).read_text(encoding="utf-8").splitlines() if line.strip()]

    from yuxi.agents.models import load_chat_model

    llm = load_chat_model(args.judge_llm, max_tokens=args.max_tokens)
    semaphore = asyncio.Semaphore(max(1, args.concurrency))

    async def run() -> list[dict]:
        tasks = [score_one(llm, rec, semaphore, i, document_level=args.document_level) for i, rec in enumerate(records, 1)]
        items = []
        for done, task in enumerate(asyncio.as_completed(tasks), 1):
            items.append(await task)
            if done % 5 == 0 or done == len(tasks):
                print(f"[score] {done}/{len(tasks)} 题完成", flush=True)
        items.sort(key=lambda it: it["index"])
        return items

    items = asyncio.run(run())
    overall = summarize(items, args.threshold)

    markdown, payload = build_report(
        items,
        overall,
        args.name,
        args.judge_llm,
        args.results,
        document_level=args.document_level,
    )
    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / f"citation_accuracy_{args.name}.md"
    json_path = out_dir / f"citation_accuracy_{args.name}.json"
    md_path.write_text(markdown, encoding="utf-8")
    json_path.write_text(payload, encoding="utf-8")

    granularity = "文档级" if args.document_level else "chunk 级"
    print(f"judge 模型: {args.judge_llm} | 指标: 引用准确率（Hit@{TOP_K}，{granularity}来源条目）")
    print(
        f"{args.name}: 引用正确 {overall['hit']}/{overall['denominator']} 题"
        f"（无引用标注 {overall['no_citation']} 题不计入，judge 失败 {overall['judge_error']} 题）"
    )
    verdict = "达标" if overall["passed"] else "未达标"
    print(f"引用准确率: {_pct(overall['accuracy'])}（阈值 ≥ {_pct(args.threshold)}，{verdict}）")
    print(f"报告已写入:\n  {json_path}\n  {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
