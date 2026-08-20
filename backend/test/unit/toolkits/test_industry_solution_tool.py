from __future__ import annotations

import io
from pathlib import Path
from types import SimpleNamespace

import pytest
from docx import Document
from docx.oxml.ns import qn
from pydantic import ValidationError

import yuxi.agents.toolkits.industry_solution as industry_tools
from server.routers.agent_router import AgentRunCreate
from yuxi.services.industry_solution_service import (
    IndustrySolutionRequest,
    render_solution_docx,
    sanitize_docx_filename,
)
from yuxi.services.input_message_service import build_chat_input_message


def test_industry_solution_tools_register_runtime_as_injected_argument():
    assert "runtime" in industry_tools.research_industry_products._injected_args_keys
    assert "runtime" in industry_tools.export_industry_solution_docx._injected_args_keys


@pytest.mark.asyncio
async def test_research_industry_products_retrieves_each_product_and_keeps_sources(monkeypatch):
    queries: list[str] = []

    async def fake_retrieve(kb_ids, query_text, *, runtime, file_name=None):
        queries.append(query_text)
        product = "产品A" if "产品A" in query_text else "产品B"
        other_product = "产品B" if product == "产品A" else "产品A"
        return {
            "status": "ok",
            "results": [
                {
                    "id": f"chunk-{product}",
                    "kb_id": kb_ids[0],
                    "file_id": f"file-{product}",
                    "content": f"{product} 的真实能力证据",
                    "metadata": {"source": f"{product}白皮书.docx"},
                },
                {
                    "id": f"chunk-{other_product}",
                    "kb_id": kb_ids[0],
                    "file_id": f"file-{other_product}",
                    "content": f"{other_product} 的其他能力证据",
                    "metadata": {"source": f"{other_product}白皮书.docx"},
                },
            ],
        }

    monkeypatch.setattr(industry_tools, "retrieve_kbs", fake_retrieve)
    result = await industry_tools.research_industry_products.coroutine(
        industry="智慧园区",
        requirement="统一管理终端",
        products=["产品A", "产品B"],
        kb_ids=["kb-1"],
        runtime=SimpleNamespace(context=SimpleNamespace()),
    )

    assert len(queries) == 2
    assert any("产品：产品A" in query for query in queries)
    assert any("产品：产品B" in query for query in queries)
    assert [item["product"] for item in result["products"]] == ["产品A", "产品B"]
    assert result["products"][0]["evidence"][0]["source"]["product"] == "产品A"
    assert result["products"][1]["evidence"][0]["source"]["product"] == "产品B"
    assert result["products"][0]["evidence"][0]["source"] == {
        "reference": 1,
        "product": "产品A",
        "title": "产品A白皮书.docx",
        "kb_id": "kb-1",
        "file_id": "file-产品A",
        "chunk_id": "chunk-产品A",
    }
    assert "url" not in result["products"][0]["evidence"][0]["source"]
    assert [len(item["evidence"]) for item in result["products"]] == [1, 1]
    assert [source["reference"] for source in result["sources"]] == [1, 2]


@pytest.mark.parametrize(
    "products",
    [[], ["产品A"], ["产品A", " 产品A "], [f"产品-{index}" for index in range(6)]],
)
def test_industry_solution_request_rejects_invalid_products(products):
    with pytest.raises(ValidationError):
        IndustrySolutionRequest(industry="制造业", requirement="降本增效", products=products)


def test_industry_solution_request_normalizes_products():
    request = IndustrySolutionRequest(
        industry=" 制造业 ", requirement=" 降本增效 ", products=[" 产品A ", "产品B", "产品b"]
    )
    assert request.industry == "制造业"
    assert request.requirement == "降本增效"
    assert request.products == ["产品A", "产品B"]


def test_structured_request_is_preserved_and_injected_into_model_context():
    request = {"industry": "智慧园区", "requirement": "统一管理", "products": ["产品A", "产品B"]}
    message = build_chat_input_message("生成行业方案", industry_solution=request)

    assert message.content == "生成行业方案"
    assert message.extra_metadata["industry_solution"] == request
    model_content = message.require_langchain_message().content
    assert "<industry_solution_request>" in model_content
    assert '"products": ["产品A", "产品B"]' in model_content


def test_agent_run_schema_validates_structured_products():
    payload = AgentRunCreate(
        query="生成行业方案",
        agent_slug="default",
        thread_id="thread-1",
        industry_solution={"industry": "能源", "requirement": "安全运营", "products": ["A", "B"]},
    )
    assert payload.industry_solution.products == ["A", "B"]


def test_render_solution_docx_contains_body_products_and_bilingual_references():
    document_bytes, chat_markdown = render_solution_docx(
        title="智慧园区行业解决方案",
        industry="智慧园区 / Smart Campus",
        products=["产品A", "Product B"],
        content="# 总体方案\n\n产品A 与 Product B 协同工作。[1][2]",
        sources=[
            {"reference": 1, "product": "产品A", "title": "产品A白皮书"},
            {"reference": 2, "product": "Product B", "title": "Product B Guide"},
        ],
    )
    document = Document(io.BytesIO(document_bytes))
    text = "\n".join(paragraph.text for paragraph in document.paragraphs)

    assert document_bytes.startswith(b"PK")
    assert "产品A 与 Product B 协同工作。[1][2]" in text
    assert "产品A, Product B" in text
    assert "[1] 产品A - 产品A白皮书" in text
    assert "[2] Product B - Product B Guide" in text
    assert chat_markdown.endswith("[2] Product B - `Product B Guide`")


def test_render_solution_docx_formats_inline_markdown_and_fonts_consistently():
    document_bytes, chat_markdown = render_solution_docx(
        title="智慧园区行业解决方案",
        industry="智慧园区",
        products=["Product A", "Product B", "Product C"],
        content=(
            "# 总体方案\n\n"
            "**安全管理**保障园区运行。[1]\n\n"
            "## 感知层\n\n"
            "- **运营管理**统一汇聚数据\n"
            "- **节能降耗**降低运营成本\n\n"
            "| 层级 | 能力 |\n| --- | --- |\n| 决策层 | **协同决策** |"
        ),
        sources=[
            {
                "reference": 1,
                "product": "Product A",
                "title": "product-a-smart-access.md",
                "kb_id": "kb-a",
                "file_id": "file-a",
                "chunk_id": "chunk-a",
            }
        ],
    )
    document = Document(io.BytesIO(document_bytes))
    paragraphs = list(document.paragraphs) + [
        paragraph
        for table in document.tables
        for row in table.rows
        for cell in row.cells
        for paragraph in cell.paragraphs
    ]
    text = "\n".join(paragraph.text for paragraph in paragraphs)
    runs = [run for paragraph in paragraphs for run in paragraph.runs]

    assert "**" not in text
    assert "安全管理" in text
    assert "感知层" in text
    assert any(run.text == "安全管理" and run.bold for run in runs)
    assert any(run.text == "运营管理" and run.bold for run in runs)
    assert any(run.text == "协同决策" and run.bold for run in runs)
    assert "[1] Product A - product-a-smart-access.md" in text
    assert "`product-a-smart-access.md`" in chat_markdown
    assert "http://product-a-smart-access.md" not in chat_markdown

    for style_name in ("Normal", "Heading 1", "Heading 2", "Heading 3", "List Bullet", "List Number"):
        style = document.styles[style_name]
        fonts = style._element.get_or_add_rPr().get_or_add_rFonts()
        assert style.font.name == "Microsoft YaHei"
        assert fonts.get(qn("w:ascii")) == "Microsoft YaHei"
        assert fonts.get(qn("w:hAnsi")) == "Microsoft YaHei"
        assert fonts.get(qn("w:eastAsia")) == "Microsoft YaHei"

    controlled_runs = [run for run in runs if run.text.strip()]
    assert controlled_runs
    for run in controlled_runs:
        fonts = run._element.get_or_add_rPr().get_or_add_rFonts()
        assert run.font.name == "Microsoft YaHei"
        assert fonts.get(qn("w:ascii")) == "Microsoft YaHei"
        assert fonts.get(qn("w:hAnsi")) == "Microsoft YaHei"
        assert fonts.get(qn("w:eastAsia")) == "Microsoft YaHei"


def test_render_solution_docx_rejects_missing_reference_and_omits_unused_sources():
    with pytest.raises(ValueError, match=r"不存在的来源.*\[3]"):
        render_solution_docx(
            title="方案",
            industry="制造业",
            products=["A", "B"],
            content="正文结论。[3]",
            sources=[{"reference": 1, "product": "A", "title": "A手册"}],
        )

    _, chat_markdown = render_solution_docx(
        title="方案",
        industry="制造业",
        products=["A", "B"],
        content="正文结论。[1]",
        sources=[
            {"reference": 1, "product": "A", "title": "A手册"},
            {"reference": 2, "product": "B", "title": "B手册"},
        ],
    )
    assert "[1] A - `A手册`" in chat_markdown
    assert "[2] B - B手册" not in chat_markdown


def test_render_solution_docx_replaces_model_generated_reference_list():
    _, chat_markdown = render_solution_docx(
        title="方案",
        industry="智慧园区",
        products=["A", "B"],
        content="正文结论。[1]\n\n## 来源 / References\n\n[1] 模型自行生成的来源",
        sources=[{"reference": 1, "product": "A", "title": "A手册"}],
    )

    assert chat_markdown.count("# 来源 / References") == 1
    assert "模型自行生成的来源" not in chat_markdown
    assert chat_markdown.endswith("[1] A - `A手册`")


def test_docx_filename_is_sanitized():
    filename = sanitize_docx_filename("../../智慧园区:方案\\报告")
    assert filename == "智慧园区-方案-报告.docx"
    assert ".." not in filename
    assert "/" not in filename
    assert "\\" not in filename


def test_export_source_rejects_internal_url_and_strips_path():
    source = industry_tools.SolutionSource(
        reference=1,
        product="A",
        title=r"C:\internal\A手册.docx",
        url="https://example.test/a",
    )
    assert source.title == "A手册.docx"

    with pytest.raises(ValidationError, match="HTTP/HTTPS"):
        industry_tools.SolutionSource(
            reference=1,
            product="A",
            title="A手册.docx",
            url="file:///internal/A手册.docx",
        )


def test_export_tool_writes_docx_inside_thread_outputs(tmp_path: Path, monkeypatch):
    outputs = tmp_path / "outputs"

    monkeypatch.setattr(industry_tools, "ensure_thread_dirs", lambda thread_id, uid: outputs.mkdir())
    monkeypatch.setattr(industry_tools, "sandbox_outputs_dir", lambda thread_id: outputs)
    monkeypatch.setattr(
        industry_tools,
        "virtual_path_for_thread_file",
        lambda thread_id, path, uid: f"/home/gem/user-data/outputs/{Path(path).name}",
    )

    result = industry_tools.export_industry_solution_docx.func(
        title="../智慧园区方案",
        industry="智慧园区",
        products=["产品A", "Product B"],
        content="# 总体方案\n\n协同能力。[1]",
        sources=[
            {
                "reference": 1,
                "product": "产品A",
                "title": "产品A手册",
                "kb_id": "kb-1",
                "file_id": "file-1",
                "chunk_id": "chunk-1",
            }
        ],
        runtime=SimpleNamespace(context=SimpleNamespace(thread_id="thread-1", uid="user-1")),
    )

    generated = outputs / Path(result["file_path"]).name
    assert generated.exists()
    assert generated.read_bytes().startswith(b"PK")
    assert ".." not in generated.name
    assert result["chat_markdown"].endswith("[1] 产品A - `产品A手册`")
