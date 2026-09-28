import pytest

from yuxi.knowledge.chunking.ragflow_like.dispatcher import chunk_markdown
from yuxi.knowledge.flowchart_analysis import FlowchartAnalysisError


def semantic_markdown(*, long_step: str = "提交申请") -> str:
    return f"""# 认款流程

## 流程用途
<!-- page:1 -->
处理认款申请。

## 参与角色
<!-- page:1 -->
销售、财务。

## 主流程
<!-- page:1 -->
1. {long_step}
2. 财务核对。

## 条件分支
<!-- page:1 -->
审核通过后继续。

## 退回与异常路径
<!-- page:1 -->
审核不通过退回销售。

## 关键上下游关系
<!-- page:1 -->
提交申请 → 财务核对。

## 开始与结束
<!-- page:1 -->
从提交申请开始，财务核对完成后结束。

## 补充说明
<!-- page:1 -->
未识别到明确内容
"""


def test_flowchart_chunks_retain_title_section_and_page_provenance():
    chunks = chunk_markdown(semantic_markdown(), "file-1", "source.pdf", {"ingestion_type": "flowchart"})

    assert len(chunks) == 7
    assert all("流程：认款流程" in chunk["content"] for chunk in chunks)
    assert all("章节：" in chunk["content"] for chunk in chunks)
    assert all("page:1" in chunk["tags"] for chunk in chunks)
    assert all(chunk["file_id"] == "file-1" for chunk in chunks)
    assert {tag for chunk in chunks for tag in chunk["tags"] if tag.startswith("section:")} == {
        "section:流程用途", "section:参与角色", "section:主流程", "section:条件分支",
        "section:退回与异常路径", "section:关键上下游关系", "section:开始与结束",
    }


def test_long_flowchart_section_uses_general_splitter_with_context():
    chunks = chunk_markdown(
        semantic_markdown(long_step="审批步骤" * 120),
        "file-1",
        "source.pdf",
        {"ingestion_type": "flowchart", "chunk_parser_config": {"chunk_token_num": 48}},
    )

    main_chunks = [chunk for chunk in chunks if "section:主流程" in chunk["tags"]]
    assert len(main_chunks) > 1
    assert all("流程：认款流程\n章节：主流程\n页码：1" in chunk["content"] for chunk in main_chunks)


def test_multi_page_section_keeps_unmarked_editor_text_and_each_page():
    markdown = semantic_markdown().replace(
        "## 参与角色\n<!-- page:1 -->\n销售、财务。",
        "## 参与角色\n跨页共享角色。\n<!-- page:1 -->\n销售。\n<!-- page:2 -->\n财务。",
    )
    chunks = chunk_markdown(markdown, "file-1", "source.pdf", {"ingestion_type": "flowchart"})
    roles = [chunk for chunk in chunks if "section:参与角色" in chunk["tags"]]

    assert len(roles) == 3
    assert roles[0]["tags"] == ["flowchart", "section:参与角色"]
    assert roles[1]["tags"][-1] == "page:1"
    assert roles[2]["tags"][-1] == "page:2"


def test_flowchart_chunker_rejects_malformed_confirmed_markdown():
    with pytest.raises(FlowchartAnalysisError):
        chunk_markdown("# 认款流程\n\n## 主流程\n提交申请", "file-1", "source.pdf", {"ingestion_type": "flowchart"})


def test_ordinary_document_does_not_use_flowchart_chunker():
    chunks = chunk_markdown("普通文档\n第二行", "file-1", "source.pdf", {})
    assert chunks
    assert all("流程：" not in chunk["content"] for chunk in chunks)
    assert all("tags" not in chunk for chunk in chunks)
