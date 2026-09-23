from types import SimpleNamespace

import pytest

from yuxi.knowledge.flowchart_analysis import (
    PAGE_PROMPT,
    FlowchartAnalysisError,
    FlowchartVisionAnalyzer,
    _parse_sections,
)
from yuxi.knowledge.flowchart_render import PageImage
from yuxi.models.providers.cache import ModelInfo

pytestmark = pytest.mark.asyncio

MODEL_SPEC = "domestic:vision"
PAGE_MARKDOWN = "# 调仓流程\n\n" + "\n\n".join(
    f"## {section}\n\n{body}"
    for section, body in (
        ("流程用途", "说明跨部门调仓"),
        ("参与角色", "- 销售经理"),
        ("主流程", "1. 发起调仓\n2. 审批"),
        ("条件分支", "### 条件：权限\n- 超权限：董事长审批"),
        ("退回与异常路径", "- 未通过退回发起人"),
        ("关键上下游关系", "- 发起 → 审批"),
        ("开始与结束", "申请开始；审批通过结束"),
        ("补充说明", "未识别到明确内容"),
    )
)


def make_image(number=1):
    return PageImage(number, "global", None, 100, 100, b"png", ())


def patch_model(monkeypatch, *, modalities=("text", "image"), response=PAGE_MARKDOWN, error=None):
    from yuxi.knowledge import flowchart_analysis

    info = ModelInfo(
        provider_id="domestic", model_id="vision", model_type="chat", display_name="vision",
        api_key="secret", base_url="https://example.test/v1", provider_type="openai",
        input_modalities=modalities,
    )
    monkeypatch.setattr(flowchart_analysis.model_cache, "get_model_info", lambda _spec: info)

    calls = []

    async def ainvoke(messages):
        calls.append(messages)
        if error:
            raise error
        return SimpleNamespace(content=response)

    monkeypatch.setattr(
        flowchart_analysis,
        "select_model",
        lambda **_kwargs: SimpleNamespace(model=SimpleNamespace(ainvoke=ainvoke)),
    )
    return calls


async def test_missing_vision_model_is_rejected(monkeypatch):
    from yuxi.knowledge import flowchart_analysis

    monkeypatch.setattr(flowchart_analysis.model_cache, "get_model_info", lambda _spec: None)
    with pytest.raises(FlowchartAnalysisError) as error:
        await FlowchartVisionAnalyzer().analyze_flowchart([make_image()], PAGE_PROMPT, "")
    assert error.value.code == "FLOWCHART_VISION_MODEL_NOT_CONFIGURED"


async def test_text_only_model_is_rejected_before_call(monkeypatch):
    patch_model(monkeypatch, modalities=("text",))
    with pytest.raises(FlowchartAnalysisError) as error:
        await FlowchartVisionAnalyzer().analyze_flowchart([make_image()], PAGE_PROMPT, MODEL_SPEC)
    assert error.value.code == "FLOWCHART_VISION_MODEL_UNSUPPORTED"


async def test_missing_capability_metadata_is_unknown_not_unsupported(monkeypatch):
    patch_model(monkeypatch, modalities=())
    with pytest.raises(FlowchartAnalysisError) as error:
        await FlowchartVisionAnalyzer().analyze_flowchart([make_image()], PAGE_PROMPT, MODEL_SPEC)
    assert error.value.code == "FLOWCHART_VISION_CAPABILITY_UNKNOWN"
    assert "缺少 input_modalities" in str(error.value)


async def test_mock_vision_result_preserves_page_provenance(monkeypatch):
    calls = patch_model(monkeypatch)
    result = await FlowchartVisionAnalyzer().analyze_flowchart(
        [make_image(1), make_image(2)], PAGE_PROMPT, MODEL_SPEC
    )
    assert [page.page_number for page in result.pages] == [1, 2]
    assert "<!-- page:1 -->" in result.semantic_markdown
    assert "<!-- page:2 -->" in result.semantic_markdown
    assert "跨页关系无法确认" in result.semantic_markdown
    assert "## 退回与异常路径" in result.semantic_markdown
    assert [section for section in result.semantic_markdown.splitlines() if section.startswith("## ")]
    assert len(calls) == 2
    assert calls[0][0].content[1]["image_url"]["url"].startswith("data:image/png;base64,")


async def test_unrecognized_page_still_has_provenance(monkeypatch):
    unknown = "# 无法从流程图确认\n\n" + "\n\n".join(
        f"## {name}\n\n未识别到明确内容" for name in (
            "流程用途", "参与角色", "主流程", "条件分支", "退回与异常路径",
            "关键上下游关系", "开始与结束", "补充说明",
        )
    )
    patch_model(monkeypatch, response=unknown)

    result = await FlowchartVisionAnalyzer().analyze_flowchart([make_image(3)], PAGE_PROMPT, MODEL_SPEC)

    assert "<!-- page:3 -->" in result.semantic_markdown


@pytest.mark.parametrize(
    ("response", "provider_error", "expected_code"),
    [
        ("", None, "FLOWCHART_EMPTY_RESULT"),
        ("bad", None, "FLOWCHART_INVALID_OUTPUT"),
        (PAGE_MARKDOWN, RuntimeError("secret provider detail"), "FLOWCHART_VISION_PROVIDER_ERROR"),
        (PAGE_MARKDOWN, TimeoutError(), "FLOWCHART_VISION_TIMEOUT"),
    ],
)
async def test_vision_failures_have_safe_codes(monkeypatch, response, provider_error, expected_code):
    patch_model(monkeypatch, response=response, error=provider_error)
    with pytest.raises(FlowchartAnalysisError) as error:
        await FlowchartVisionAnalyzer().analyze_flowchart([make_image()], PAGE_PROMPT, MODEL_SPEC)
    assert error.value.code == expected_code
    assert "secret" not in str(error.value)


async def test_valid_semantic_template_is_parsed():
    title, sections = _parse_sections(PAGE_MARKDOWN)
    assert title == "调仓流程"
    assert "董事长审批" in sections["条件分支"]
    assert list(sections)[-1] == "补充说明"


async def test_template_title_placeholder_is_not_accepted():
    with pytest.raises(FlowchartAnalysisError) as error:
        _parse_sections(PAGE_MARKDOWN.replace("# 调仓流程", "# 流程名称", 1))
    assert error.value.code == "FLOWCHART_INVALID_OUTPUT"
