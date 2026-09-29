"""scripts/run_agent_e2e.py 的证据片段解析纯函数单测（无网络、不 import ragas）。"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "run_agent_e2e.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("run_agent_e2e", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def e2e():
    return _load_module()


def test_parse_query_kb_returns_results(e2e):
    content = json.dumps(
        {"status": "ok", "kb_id": "kb_a", "results": [{"id": "c1", "content": "正文1"}, {"id": "c2", "content": ""}]}
    )
    chunks = e2e._parse_tool_content("query_kb", content)
    assert len(chunks) == 1
    assert chunks[0]["id"] == "c1"
    assert chunks[0]["content"] == "正文1"


def test_parse_query_kbs_same_shape(e2e):
    content = json.dumps({"status": "ok", "kb_id": "kb_a", "results": [{"chunk_id": "c9", "content": "正文"}]})
    chunks = e2e._parse_tool_content("query_kbs", content)
    assert len(chunks) == 1
    assert chunks[0]["chunk_id"] == "c9"


def test_parse_find_kb_document_windows(e2e):
    content = json.dumps(
        {
            "kb_id": "kb_a",
            "file_id": "file_1",
            "match_mode": "keyword",
            "total_matches": 2,
            "windows": [
                {"start_line": 10, "end_line": 20, "matched_lines": [12], "content": "窗口正文A"},
                {"start_line": 30, "end_line": 40, "content": ""},
            ],
        }
    )
    chunks = e2e._parse_tool_content("find_kb_document", content)
    assert len(chunks) == 1
    assert chunks[0]["id"] == "file_1:L10-20"
    assert chunks[0]["content"] == "窗口正文A"
    assert chunks[0]["kb_id"] == "kb_a"
    assert chunks[0]["file_id"] == "file_1"
    assert chunks[0]["tool"] == "find_kb_document"


def test_parse_open_kb_document_content(e2e):
    content = json.dumps({"kb_id": "kb_a", "file_id": "file_2", "start_line": 1, "end_line": 5, "content": "整窗正文"})
    chunks = e2e._parse_tool_content("open_kb_document", content)
    assert len(chunks) == 1
    assert chunks[0]["id"] == "file_2:L1-5"
    assert chunks[0]["content"] == "整窗正文"
    assert chunks[0]["tool"] == "open_kb_document"


def test_parse_search_file_metadata_has_no_content(e2e):
    content = json.dumps({"files": [{"file_id": "f1", "filename": "手册.pdf"}], "total": 1, "has_more": False})
    assert e2e._parse_tool_content("search_file", content) == []


def test_collect_file_names_from_search_file_and_query_results(e2e):
    messages = [
        {
            "tool_calls": [
                {
                    "name": "search_file",
                    "status": "success",
                    "tool_call_result": {
                        "content": json.dumps({"files": [{"file_id": "f1", "filename": "手册.pdf"}, {"file_id": "f2"}]})
                    },
                },
                {
                    "name": "query_kbs",
                    "status": "success",
                    "tool_call_result": {
                        "content": json.dumps(
                            {"results": [{"file_id": "f3", "metadata": {"source": "资料/规格书.xlsx"}}]}
                        )
                    },
                },
            ]
        }
    ]
    # f2 没有文件名、f3 取 metadata.source；两者的键都带上，供窗口片段归因
    assert e2e._collect_file_names(messages) == {"f1": "手册.pdf", "f3": "资料/规格书.xlsx"}


def test_parse_find_kb_document_prefers_backend_source(e2e):
    content = json.dumps(
        {"kb_id": "kb_a", "file_id": "file_1", "source": "后端回填名.pdf", "windows": [{"content": "正文"}]}
    )
    chunks = e2e._parse_tool_content("find_kb_document", content, {"file_1": "映射里的名.pdf"})
    assert chunks[0]["metadata"]["source"] == "后端回填名.pdf"


def test_extract_resolves_window_source_from_search_file(e2e):
    """模型先用 search_file 按文件名找到文件、再 find 打开时，窗口片段必须带上文件名。

    向量检索没召回、改走文件检索兜底的题全靠这一步归因，否则来源面板与引用评测都看不到出处。
    """
    history = {
        "history": [
            {
                "run_id": "run_1",
                "tool_calls": [
                    {
                        "name": "search_file",
                        "status": "success",
                        "tool_call_result": {
                            "content": json.dumps({"files": [{"file_id": "file_1", "filename": "定位手册V1.1.pdf"}]})
                        },
                    }
                ],
            },
            {
                "run_id": "run_1",
                "tool_calls": [
                    {
                        "name": "find_kb_document",
                        "status": "success",
                        "tool_call_result": {
                            "content": json.dumps(
                                {
                                    "kb_id": "kb_a",
                                    "file_id": "file_1",
                                    "windows": [{"start_line": 1, "end_line": 8, "content": "正文"}],
                                }
                            )
                        },
                    },
                    {
                        "name": "open_kb_document",
                        "status": "success",
                        "tool_call_result": {
                            "content": json.dumps(
                                {"kb_id": "kb_a", "file_id": "file_9", "start_line": 1, "content": "别的文件"}
                            )
                        },
                    },
                ],
            },
        ]
    }
    chunks = e2e.extract_retrieved_chunks(history, "run_1")
    assert [(c["id"], c["metadata"]["source"]) for c in chunks] == [
        ("file_1:L1-8", "定位手册V1.1.pdf"),
        ("file_9:L1-?", ""),
    ]


def test_parse_non_json_or_non_dict_returns_empty(e2e):
    assert e2e._parse_tool_content("find_kb_document", "不是 JSON") == []
    assert e2e._parse_tool_content("find_kb_document", "[1, 2]") == []
    assert e2e._parse_tool_content("query_kb", '{"results": "not-a-list"}') == []


def test_extract_collects_content_tools_only_and_dedups(e2e):
    find_window = json.dumps(
        {"kb_id": "kb_a", "file_id": "file_1", "windows": [{"start_line": 10, "end_line": 20, "content": "窗口正文"}]}
    )
    history = {
        "history": [
            {
                "run_id": "run_1",
                "tool_calls": [
                    {
                        "name": "query_kbs",
                        "status": "success",
                        "tool_call_result": {
                            "content": json.dumps(
                                {"status": "ok", "kb_id": "kb_a", "results": [{"id": "c1", "content": "正文A"}]}
                            )
                        },
                    },
                    # search_file / read_file 无正文证据，不应采集
                    {
                        "name": "search_file",
                        "status": "success",
                        "tool_call_result": {"content": json.dumps({"files": [{"file_id": "f1"}]})},
                    },
                    {
                        "name": "read_file",
                        "status": "success",
                        "tool_call_result": {"content": "任意文件正文"},
                    },
                ],
            },
            {
                "run_id": "run_1",
                "tool_calls": [
                    {
                        "name": "find_kb_document",
                        "status": "success",
                        "tool_call_result": {"content": find_window},
                    },
                    # 同一窗口重复出现应去重
                    {
                        "name": "find_kb_document",
                        "status": "success",
                        "tool_call_result": {"content": find_window},
                    },
                    # 失败的工具调用不采集
                    {
                        "name": "find_kb_document",
                        "status": "error",
                        "tool_call_result": {"content": json.dumps({"windows": [{"start_line": 1, "content": "x"}]})},
                    },
                ],
            },
        ]
    }
    chunks = e2e.extract_retrieved_chunks(history, "run_1")
    ids = [c["id"] for c in chunks]
    assert ids == ["c1", "file_1:L10-20"]
    assert all(c.get("content") for c in chunks)


def test_extract_slices_by_run_id(e2e):
    """同线程复用多题：只有本题 run 的证据才算数。"""

    def msg(run_id: str, chunk_id: str) -> dict:
        content = json.dumps({"status": "ok", "results": [{"id": chunk_id, "content": "正文"}]})
        return {
            "run_id": run_id,
            "tool_calls": [{"name": "query_kb", "status": "success", "tool_call_result": {"content": content}}],
        }

    history = {"history": [msg("run_a", "c_a"), msg("run_b", "c_b")]}
    assert [c["id"] for c in e2e.extract_retrieved_chunks(history, "run_b")] == ["c_b"]


def test_extract_run_steps_counts_turns_and_tools(e2e):
    """一轮里并发多个工具仍算一个 tool_turn（super-step 按轮计）。"""
    history = {
        "history": [
            {"run_id": "run_1", "type": "human", "content": "问题"},
            {
                "run_id": "run_1",
                "type": "ai",
                "tool_calls": [{"name": "query_kb"}, {"name": "query_kb"}, {"name": "search_file"}],
            },
            {"run_id": "run_1", "type": "ai", "tool_calls": [{"name": "open_kb_document"}]},
            {"run_id": "run_1", "type": "ai", "content": "终答"},
            # 同线程另一题的 run 不计入
            {"run_id": "run_2", "type": "ai", "tool_calls": [{"name": "query_kb"}]},
        ]
    }
    steps = e2e.extract_run_steps(history, "run_1")
    assert steps["tool_calls"] == 4
    assert steps["tool_turns"] == 2
    assert steps["est_steps"] == 5
    assert steps["by_tool"] == {"query_kb": 2, "open_kb_document": 1, "search_file": 1}


def test_extract_run_steps_without_tool_calls(e2e):
    """直接作答（零工具）仍消耗 1 步（终答的模型节点）。"""
    history = {"history": [{"run_id": "run_1", "type": "ai", "content": "终答"}]}
    steps = e2e.extract_run_steps(history, "run_1")
    assert steps == {"tool_calls": 0, "by_tool": {}, "tool_turns": 0, "est_steps": 1}


def test_sum_run_steps_merges_clarification_rounds(e2e):
    """反问澄清拆两轮 run：首轮检索成本不能漏，两轮工具计数与步数相加。"""
    history = {
        "history": [
            {"run_id": "run_ask", "type": "ai", "tool_calls": [{"name": "query_kbs"}, {"name": "ask_user_question"}]},
            {"run_id": "run_done", "type": "ai", "tool_calls": [{"name": "query_kbs"}]},
            {"run_id": "run_done", "type": "ai", "content": "终答"},
        ]
    }
    steps = e2e.sum_run_steps(history, ["run_ask", "run_done"])
    # 两轮各 (2×1+1)=3 步、各占一份独立的 recursion_limit，故合计 6 而非「合起来算 2 轮」
    assert steps == {
        "tool_calls": 3,
        "by_tool": {"query_kbs": 2, "ask_user_question": 1},
        "tool_turns": 2,
        "est_steps": 6,
        "runs": 2,
    }


def test_build_first_option_answer_picks_first_option(e2e):
    """单选取第一个选项的 value；多选包成单元素列表（与前端 buildAnswer 同形状）。"""
    questions = [
        {
            "question_id": "q-1",
            "question": "哪个产品线？",
            "options": [{"label": "MCX", "value": "mcx"}, {"label": "POC", "value": "poc"}],
        },
        {
            "question_id": "q-2",
            "question": "涉及哪些终端？",
            "options": [{"label": "安卓", "value": "android"}],
            "multi_select": True,
        },
    ]
    answer, unanswered = e2e.build_first_option_answer(questions)
    assert answer == {"q-1": "mcx", "q-2": ["android"]}
    assert unanswered == []


def test_build_first_option_answer_defaults_question_id(e2e):
    """后端不保证下发 question_id，缺省规则与前端 normalizeQuestions 一致（q-1 起）。"""
    questions = [
        {"question": "A", "options": [{"value": "a1"}]},
        {"questionId": "custom", "options": [{"value": "b1"}]},
    ]
    answer, _ = e2e.build_first_option_answer(questions)
    assert answer == {"q-1": "a1", "custom": "b1"}


def test_build_first_option_answer_reports_questions_without_options(e2e):
    """追问没给选项的题如实记为未作答，不从题干里编造答案。"""
    questions = [
        {"question_id": "q-1", "question": "请描述具体场景", "options": []},
        {"question_id": "q-2", "question": "哪条产品线？", "options": [{"label": "MCX"}]},
    ]
    answer, unanswered = e2e.build_first_option_answer(questions)
    assert unanswered == ["q-1"]
    # label 兜底成 value：选项只有标签时也能作答
    assert answer == {"q-2": "MCX"}


def test_build_first_option_answer_ignores_option_without_value_or_label(e2e):
    """空选项等同没有可选项。"""
    answer, unanswered = e2e.build_first_option_answer(
        [{"question_id": "q-1", "options": [{"label": "  ", "value": ""}]}]
    )
    assert answer == {}
    assert unanswered == ["q-1"]


def test_build_clarification_answer_uses_question_ids(e2e):
    """固定回复策略：同一段文本套到每个问题 ID 上。"""
    questions = [{"question_id": "q-1"}, {"question": "缺 ID"}]
    assert e2e.build_clarification_answer(questions, "请按标准配置回答") == {
        "q-1": "请按标准配置回答",
        "q-2": "请按标准配置回答",
    }


def test_is_clarification_interrupt_only_for_ask_user_question(e2e):
    """只有「反问澄清」算澄清中断，超时取消等中断不能误判。"""
    assert e2e.is_clarification_interrupt({"status": "interrupted", "error": {"type": "ask_user_question_required"}})
    assert not e2e.is_clarification_interrupt({"status": "interrupted", "error": {"type": "cancelled"}})
    assert not e2e.is_clarification_interrupt({"status": "completed"})


@pytest.mark.asyncio
async def test_answer_clarifications_stops_at_round_limit(e2e, monkeypatch):
    """模型反复追问时按轮次上限收口：每轮都记明细，run 计数不漏不重。"""
    asked = {"n": 0}

    async def fake_fetch(client, headers, base_url, run_id):
        asked["n"] += 1
        return [{"question_id": "q-1", "question": f"第{asked['n']}次追问", "options": [{"value": f"opt{asked['n']}"}]}]

    async def fake_resume(
        client, headers, base_url, agent_slug, thread_id, run_id, answer, timeout, model_spec=None, sandbox_scope=None
    ):
        return f"run_{asked['n']}", {"status": "interrupted", "error": {"type": "ask_user_question_required"}}

    monkeypatch.setattr(e2e, "fetch_interrupt_questions", fake_fetch)
    monkeypatch.setattr(e2e, "resume_after_clarification", fake_resume)

    run_id, payload, rounds = await e2e.answer_clarifications(
        None,
        {},
        "",
        "bot",
        "thread",
        "run_0",
        {"status": "interrupted", "error": {"type": "ask_user_question_required"}},
        None,
        60.0,
        None,
        3,
    )
    assert len(rounds) == 3
    assert [item["answer"] for item in rounds] == [{"q-1": "opt1"}, {"q-1": "opt2"}, {"q-1": "opt3"}]
    assert [item["resumed_run_id"] for item in rounds] == ["run_1", "run_2", "run_3"]
    assert run_id == "run_3"


@pytest.mark.asyncio
async def test_answer_clarifications_gives_up_without_options(e2e, monkeypatch):
    """追问不给选项：不续跑、不留悬空 run，记一轮明细后收口。"""

    async def fake_fetch(client, headers, base_url, run_id):
        return [{"question_id": "q-1", "question": "请补充描述", "options": []}]

    async def fail_resume(*args, **kwargs):
        raise AssertionError("无可选项时不应发起续跑")

    monkeypatch.setattr(e2e, "fetch_interrupt_questions", fake_fetch)
    monkeypatch.setattr(e2e, "resume_after_clarification", fail_resume)

    run_id, _, rounds = await e2e.answer_clarifications(
        None,
        {},
        "",
        "bot",
        "thread",
        "run_0",
        {"status": "interrupted", "error": {"type": "ask_user_question_required"}},
        None,
        60.0,
        None,
        3,
    )
    assert run_id == "run_0"
    assert len(rounds) == 1
    assert rounds[0]["unanswered"] == ["q-1"]
    assert rounds[0]["resumed_run_id"] is None


@pytest.mark.asyncio
async def test_answer_clarifications_noop_when_run_completed(e2e, monkeypatch):
    """没被追问就直接返回，不产生任何明细。"""

    async def fail_fetch(*args, **kwargs):
        raise AssertionError("未中断时不应读取事件流")

    monkeypatch.setattr(e2e, "fetch_interrupt_questions", fail_fetch)
    run_id, payload, rounds = await e2e.answer_clarifications(
        None, {}, "", "bot", "thread", "run_0", {"status": "completed"}, None, 60.0, None, 3
    )
    assert (run_id, rounds) == ("run_0", [])
    assert payload == {"status": "completed"}
