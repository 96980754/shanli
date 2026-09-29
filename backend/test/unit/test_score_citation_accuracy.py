"""scripts/eval_datasets/score_citation_accuracy.py 的引用解析与判定纯函数单测（无网络、不调 LLM）。"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "eval_datasets" / "score_citation_accuracy.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("score_citation_accuracy", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def scorer():
    return _load_module()


def _chunk(source: str, score: float | None, content: str = "正文", chunk_id: str = "c1") -> dict:
    metadata = {"source": source, "chunk_id": chunk_id}
    if score is not None:
        metadata["score"] = score
    return {"id": chunk_id, "content": content, "metadata": metadata}


def _window(file_id: str, content: str = "窗口正文") -> dict:
    """find_kb_document / open_kb_document 的窗口片段：只有 file_id，没有 source 与分数。"""
    return {
        "id": f"{file_id}:L1-40",
        "file_id": file_id,
        "tool": "find_kb_document",
        "content": content,
        "metadata": {},
    }


class _StubLLM:
    """记录调用次数并按预设序列返回判定结果，用于验证短路与重试。"""

    def __init__(self, verdicts: list[str]):
        self.verdicts = list(verdicts)
        self.prompts: list[str] = []

    async def ainvoke(self, prompt: str):
        self.prompts.append(prompt)
        verdict = self.verdicts.pop(0) if self.verdicts else '{"supported": false, "reason": "兜底"}'

        class _Resp:
            content = verdict

        return _Resp()


# --- citation_spans ---------------------------------------------------------


def test_citation_spans_from_angle_brackets(scorer):
    assert scorer.citation_spans("依据《POCSTARS运营平台用户手册V3.0》中的记载，操作如下。") == [
        "POCSTARS运营平台用户手册V3.0"
    ]


def test_citation_spans_from_source_section(scorer):
    spans = scorer.citation_spans("操作步骤如下。\n\n**来源**：客服知识库（客服知识库.md）")
    assert spans == ["来源**：客服知识库（客服知识库.md）"]


def test_citation_spans_keeps_filename_and_section_number(scorer):
    answer = (
        "见下文。\n\n来源：POCSTARS运营平台用户手册V3.0-20260409.docx（poc资料/MNO）——2.6 个人中心、2.3.1.7 修改密码。"
    )
    span = scorer.citation_spans(answer)[0]
    # 文件名与章节号都必须留在片段里：反向比对靠片段内的文件名，切掉文件名就没有可归因的出处了
    assert "POCSTARS运营平台用户手册V3.0-20260409.docx（poc资料/MNO）" in span
    assert "2.6 个人中心、2.3.1.7 修改密码" in span


def test_citation_spans_cuts_at_period(scorer):
    answer = "结论。\n\n来源：客服知识库（客服知识库.md）。如需进一步了解，可继续提问。"
    span = scorer.citation_spans(answer)[0]
    assert "如需进一步了解" not in span


def test_citation_spans_uses_last_source_section(scorer):
    answer = "来源：旧来源.docx。\n\n正文继续。\n\n来源：最终来源.docx"
    assert scorer.citation_spans(answer) == ["来源：最终来源.docx"]


def test_citation_spans_from_standalone_heading(scorer):
    # 「来源」独立成行、清单在下一行：行内式正则匹配不到，漏掉就等于这题没有引用
    answer = "结论如下。\n\n**来源**\n\n- 知识库：客服知识库（客服知识库.md）"
    span = scorer.citation_spans(answer)[0]
    assert "客服知识库.md" in span


def test_citation_spans_from_hash_heading(scorer):
    answer = "结论如下。\n\n## 来源\n\n- 《POCSTARS运营平台用户手册V3.0.pdf》（知识库：定位资料）"
    span = scorer.citation_spans(answer)[0]
    assert "POCSTARS运营平台用户手册V3.0.pdf" in span


def test_citation_spans_prefers_heading_over_earlier_prose(scorer):
    # 正文里的「来源说明：…」是散文，末尾的独立标题才是引用清单；取位置靠后的那个
    answer = '结论。\n\n来源说明：该数据来自客服知识库中"某条目"，记载为 357KB。\n\n**来源**\n- 客服知识库'
    assert scorer.citation_spans(answer) == ["**来源**\n- 客服知识库"]


def test_citation_spans_heading_stops_at_following_paragraph(scorer):
    answer = "**来源**\n\n- 《手册V1.0.docx》\n\n如需进一步了解，可继续提问。"
    assert "如需进一步了解" not in scorer.citation_spans(answer)[0]


def test_citation_spans_from_table_row(scorer):
    answer = "| 参考手册 | 说明 |\n|---|---|\n| 内容 | 内容 |"
    assert scorer.citation_spans(answer) == ["参考手册"]


def test_citation_spans_returns_empty_without_citation(scorer):
    assert scorer.citation_spans("抱歉，在现有知识库中未找到相关依据。") == []


# --- normalize_doc_name / levenshtein ---------------------------------------


def test_normalize_doc_name_strips_notes_extension_and_version(scorer):
    assert scorer.normalize_doc_name("Miniserver M200规格书（更新日期 2025-11-25）.xlsx") == "miniserverm200规格书"


def test_normalize_doc_name_keeps_distinct_docs_apart(scorer):
    assert scorer.normalize_doc_name("C10单页.pdf") != scorer.normalize_doc_name("C20单页.pdf")


def test_levenshtein_basic(scorer):
    assert scorer.levenshtein("abc", "abc") == 0
    assert scorer.levenshtein("", "abc") == 3
    assert scorer.levenshtein("kitten", "sitting") == 3


# --- span_cites_doc ---------------------------------------------------------


def test_span_cites_doc_matches_filename_in_section(scorer):
    span = "来源：客服知识库（客服知识库.md）"
    assert scorer.span_cites_doc(span, scorer.normalize_doc_name("客服知识库.md")) is True


def test_span_cites_doc_keeps_parenthesised_filename(scorer):
    # 文件名只出现在括号内：压平片段时必须保留括号内容，否则等于没有可归因的出处
    span = "来源：poc 资料库（POCSTARS运营平台用户手册V3.0-20260409.docx）"
    assert scorer.span_cites_doc(span, scorer.normalize_doc_name("POCSTARS运营平台用户手册V3.0-20260409.docx")) is True


def test_span_cites_doc_matches_approximate_name(scorer):
    # 模型在文件名里多加了一个词：包含判定失败，交给相似度兜底（阈值沿用前端 0.8）
    core = scorer.normalize_doc_name("Miniserver M200规格书.xlsx")
    assert scorer.span_cites_doc("《MiniServer M200 产品规格书》", core) is True


def test_span_cites_doc_rejects_other_document(scorer):
    span = "来源：客服知识库（客服知识库.md）"
    assert scorer.span_cites_doc(span, scorer.normalize_doc_name("MDM管理员手册.docx")) is False


def test_span_cites_doc_ignores_too_short_name(scorer):
    # 过短的名字与片段偶然重合的概率高，与前端一致不参与判定
    assert scorer.span_cites_doc("来源：V90版本说明.md", scorer.normalize_doc_name("V90.pdf")) is False


# --- build_cited_entries ----------------------------------------------------


def test_build_cited_document_entries_merges_chunks(scorer):
    chunks = [
        _chunk("资料/pocstars操作手册.docx", 0.8, content="第一段", chunk_id="c1"),
        _chunk("资料/pocstars操作手册.docx", 0.7, content="第二段", chunk_id="c2"),
        _chunk("资料/完全无关的规格书.pdf", 0.9, content="无关", chunk_id="x1"),
    ]
    record = {"agent_answer": "来源：pocstars操作手册.docx", "retrieved_chunks": chunks}

    _, entries = scorer.build_cited_document_entries(record)

    assert len(entries) == 1
    assert entries[0]["doc"] == "pocstars操作手册.docx"
    assert entries[0]["chunk_ids"] == ["c1", "c2"]
    assert entries[0]["content"] == "第一段\n\n第二段"


def test_build_cited_document_entries_keeps_document_citation_order(scorer):
    chunks = [
        _chunk("资料/POCSTARS运营平台用户手册.docx", 0.99, chunk_id="b"),
        _chunk("资料/MDM管理员操作手册.docx", 0.1, chunk_id="a"),
    ]
    record = {
        "agent_answer": "先看《MDM管理员操作手册.docx》，再看《POCSTARS运营平台用户手册.docx》。",
        "retrieved_chunks": chunks,
    }

    _, entries = scorer.build_cited_document_entries(record)

    assert [entry["doc"] for entry in entries] == ["MDM管理员操作手册.docx", "POCSTARS运营平台用户手册.docx"]


def test_build_cited_entries_respects_limit(scorer):
    chunks = [_chunk("资料/pocstars操作手册.docx", 1.0 - i / 100, chunk_id=f"c{i}") for i in range(8)]
    record = {"agent_answer": "结论。\n\n来源：pocstars操作手册.docx", "retrieved_chunks": chunks}
    _, entries = scorer.build_cited_entries(record)
    assert len(entries) == scorer.TOP_K
    assert [e["chunk_id"] for e in entries] == ["c0", "c1", "c2", "c3", "c4"]


def test_build_cited_entries_orders_docs_by_citation_appearance(scorer):
    chunks = [
        _chunk("资料/POCSTARS运营平台用户手册.docx", 0.99, chunk_id="a"),
        _chunk("资料/MDM管理员操作手册.docx", 0.1, chunk_id="b"),
    ]
    record = {
        "agent_answer": "先看《MDM管理员操作手册.docx》，再看《POCSTARS运营平台用户手册.docx》。",
        "retrieved_chunks": chunks,
    }
    _, entries = scorer.build_cited_entries(record)
    # 引用先后优先于相关度：先被引用的文档排前面，文档内才按相关度降序
    assert [e["chunk_id"] for e in entries] == ["b", "a"]


def test_build_cited_entries_sorts_within_doc_by_score(scorer):
    # runner 产物把分数放在 metadata.score，顶层无 score 时不应退化为原始顺序
    chunks = [
        _chunk("资料/pocstars操作手册.docx", 0.2, chunk_id="low"),
        _chunk("资料/pocstars操作手册.docx", 0.8, chunk_id="high"),
    ]
    record = {"agent_answer": "来源：pocstars操作手册.docx", "retrieved_chunks": chunks}
    _, entries = scorer.build_cited_entries(record)
    assert [e["chunk_id"] for e in entries] == ["high", "low"]


def test_build_cited_entries_skips_uncited_chunks(scorer):
    # 本轮检索到但与引用标注对不上的片段不是引用，不进条目
    chunks = [
        _chunk("资料/pocstars操作手册.docx", 0.9, chunk_id="cited"),
        _chunk("资料/完全无关的规格书.pdf", 0.99, chunk_id="other"),
    ]
    record = {"agent_answer": "来源：pocstars操作手册.docx", "retrieved_chunks": chunks}
    _, entries = scorer.build_cited_entries(record)
    assert [e["chunk_id"] for e in entries] == ["cited"]


def test_build_cited_entries_resolves_window_chunk_by_file_id(scorer):
    # 窗口片段只有 file_id：不按 file_id 回查文件名就会把模型真正读过的原文证据整段丢掉
    chunks = [
        {
            "id": "c1",
            "file_id": "f1",
            "content": "检索正文",
            "metadata": {"source": "资料/pocstars操作手册.docx", "chunk_id": "c1", "score": 0.9},
        },
        _window("f1", "窗口里的原文"),
    ]
    record = {"agent_answer": "来源：pocstars操作手册.docx", "retrieved_chunks": chunks}
    _, entries = scorer.build_cited_entries(record)
    assert [e["chunk_id"] for e in entries] == ["c1", "f1:L1-40"]
    assert entries[1]["doc"] == "pocstars操作手册.docx"


def test_build_cited_entries_skips_window_chunk_with_unknown_file(scorer):
    # 本轮没有任何片段给出该 file_id 的名字：无从归因，不进条目
    chunks = [_window("f9", "无主的窗口正文")]
    record = {"agent_answer": "来源：pocstars操作手册.docx", "retrieved_chunks": chunks}
    _, entries = scorer.build_cited_entries(record)
    assert entries == []


def test_build_cited_entries_empty_when_no_citation(scorer):
    record = {"agent_answer": "抱歉，未找到相关依据。", "retrieved_chunks": [_chunk("资料/pocstars操作手册.docx", 0.9)]}
    spans, entries = scorer.build_cited_entries(record)
    assert spans == [] and entries == []


# --- build_judge_prompt -----------------------------------------------------


def test_build_judge_prompt_truncates_long_content(scorer):
    prompt = scorer.build_judge_prompt("问题", "标准答案", "手册.docx", "正" * (scorer.CONTENT_LIMIT + 500))
    assert "片段过长已截断" in prompt
    assert "正" * (scorer.CONTENT_LIMIT + 1) not in prompt


def test_build_judge_prompt_keeps_real_chunk_intact(scorer):
    # 上限必须高于真实片段长度：表格类片段的关键行常在末尾，截断会让 judge 判成「不支持」
    prompt = scorer.build_judge_prompt("问题", "标准答案", "手册.docx", "正" * 5174 + "结尾关键行")
    assert "片段过长已截断" not in prompt
    assert "结尾关键行" in prompt


# --- judge_entry / score_one ------------------------------------------------


def test_parse_judge_tolerates_code_fence(scorer):
    parsed = scorer.parse_judge('```json\n{"supported": true, "reason": "包含关键步骤"}\n```')
    assert parsed == {"supported": True, "reason": "包含关键步骤"}


def test_parse_judge_rejects_non_boolean(scorer):
    with pytest.raises(ValueError):
        scorer.parse_judge('{"supported": "yes", "reason": "x"}')


async def test_score_one_short_circuits_after_hit(scorer):
    chunks = [_chunk("资料/pocstars操作手册.docx", 0.9 - i / 100, chunk_id=f"c{i}") for i in range(4)]
    record = {
        "query": "问题",
        "gold_answer": "标准答案",
        "agent_answer": "来源：pocstars操作手册.docx",
        "retrieved_chunks": chunks,
    }
    llm = _StubLLM(['{"supported": false, "reason": "无关"}', '{"supported": true, "reason": "命中关键步骤"}'])

    item = await scorer.score_one(llm, record, scorer.asyncio.Semaphore(1), 1)

    assert item["hit"] is True
    assert item["hit_position"] == 2
    assert len(llm.prompts) == 2  # 命中后不再判后续条目
    assert item["entries"][2]["supported"] is None


async def test_score_one_marks_question_without_citation(scorer):
    record = {"query": "问题", "gold_answer": "答案", "agent_answer": "抱歉，未找到相关依据。", "retrieved_chunks": []}
    item = await scorer.score_one(_StubLLM([]), record, scorer.asyncio.Semaphore(1), 1)
    assert item["entries"] == [] and item["cited_spans"] == [] and item["unresolved_citation"] is False


async def test_score_one_flags_unresolved_citation(scorer):
    record = {
        "query": "问题",
        "gold_answer": "答案",
        "agent_answer": "结论。\n\n来源：客服知识库",
        "retrieved_chunks": [_chunk("资料/完全无关的规格书.pdf", 0.9)],
    }
    item = await scorer.score_one(_StubLLM([]), record, scorer.asyncio.Semaphore(1), 1)
    assert item["entries"] == [] and item["unresolved_citation"] is True


async def test_judge_entry_retries_once_on_unparsable_output(scorer):
    llm = _StubLLM(["这不是 JSON", '{"supported": true, "reason": "第二次成功"}'])
    entry = {"doc": "手册.docx", "content": "正文"}
    judged = await scorer.judge_entry(llm, "问题", "答案", entry, scorer.asyncio.Semaphore(1))
    assert judged == {"supported": True, "reason": "第二次成功", "judge_error": None}
    assert "请严格只输出指定 JSON" in llm.prompts[1]


# --- summarize --------------------------------------------------------------


def test_summarize_uses_cited_questions_as_denominator(scorer):
    items = [
        {
            "entries": [{"chunk_id": "c1"}],
            "hit": True,
            "cited_spans": ["来源：pocstars操作手册.docx"],
            "unresolved_citation": False,
            "judge_error": None,
        },
        {
            "entries": [{"chunk_id": "c2"}],
            "hit": False,
            "cited_spans": ["来源：pocstars操作手册.docx"],
            "unresolved_citation": False,
            "judge_error": None,
        },
        {"entries": [], "hit": False, "cited_spans": [], "unresolved_citation": False, "judge_error": None},
        {
            "entries": [],
            "hit": False,
            "cited_spans": ["来源：客服知识库"],
            "unresolved_citation": True,
            "judge_error": None,
        },
    ]
    overall = scorer.summarize(items, threshold=0.95)

    assert overall["denominator"] == 2  # 无引用标注、对不上片段的题都不进分母
    assert overall["hit"] == 1
    assert overall["accuracy"] == 0.5
    assert overall["passed"] is False
    assert overall["no_citation"] == 1
    assert overall["unresolved_citation"] == 1
