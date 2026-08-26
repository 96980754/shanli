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
                ]
            },
            {
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
                ]
            },
        ]
    }
    chunks = e2e.extract_retrieved_chunks(history)
    ids = [c["id"] for c in chunks]
    assert ids == ["c1", "file_1:L10-20"]
    assert all(c.get("content") for c in chunks)
