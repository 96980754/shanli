"""Provider-neutral flowchart vision analysis and page-preserving synthesis."""

from __future__ import annotations

import asyncio
import base64
import re
from dataclasses import dataclass

from langchain_core.messages import convert_to_messages

from yuxi import config
from yuxi.knowledge.flowchart_render import PageImage
from yuxi.models.chat import select_model
from yuxi.models.providers.cache import model_cache

PROMPT_VERSION = "flow-semantic-v2"
SECTION_NAMES = (
    "流程用途",
    "参与角色",
    "主流程",
    "条件分支",
    "退回与异常路径",
    "关键上下游关系",
    "开始与结束",
    "补充说明",
)
PAGE_PROMPT = """你是企业流程图语义解析器。请把当前这一页转换为面向检索的流程语义 Markdown，不要只做 OCR 或泛泛总结。
只根据图片中可见的文字、泳道、箭头、连线、判断框及附注填写，不依据常识补充业务事实。
对无法确认的信息，只能引用图中已观察到的角色、节点、条件或步骤，说明这些实体之间的关系无法确认；不得为了说明不确定性而举出图外角色、节点、审批、事件或业务规则。
如果未标明开始或结束，只写“未识别到明确开始节点”或“未识别到明确结束节点”；不要添加括号中的假设性示例。未标明条件细节时只写“无法从流程图确认判定细节”，不要列举图外标准。
识别流程名称、用途、角色/部门/泳道、主流程步骤及顺序、条件分支、是/否路径、审批通过/不通过、退回和异常路径、关键上下游关系、开始条件、结束状态和图中业务规则。
连线或小字看不清时写“无法从流程图确认”；没有明确内容的章节写“未识别到明确内容”。跨页关系无法由当前页证明时写“跨页关系无法确认”。
必须严格使用以下一级标题与二级章节，不要增删或改名：
# 流程名称
## 流程用途
## 参与角色
## 主流程
## 条件分支
## 退回与异常路径
## 关键上下游关系
## 开始与结束
## 补充说明
一级标题中的“流程名称”是占位符，必须替换为图中实际流程名称；如果看不清，写“无法从流程图确认”，不要照抄占位符。
主流程用编号步骤；条件分支可用“### 条件：...”及条件路径；上下游关系用“A → B（条件：...）”。只输出 Markdown。"""


class FlowchartAnalysisError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class PageFlowchartAnalysis:
    page_number: int
    markdown: str
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class FlowchartAnalysisResult:
    pages: tuple[PageFlowchartAnalysis, ...]
    semantic_markdown: str
    warnings: tuple[str, ...]
    model_spec: str
    prompt_version: str = PROMPT_VERSION


class FlowchartVisionAnalyzer:
    """Calls the configured image-capable chat model once per page."""

    async def analyze_flowchart(self, images: list[PageImage], prompt: str, model_spec: str) -> FlowchartAnalysisResult:
        if not model_spec:
            raise FlowchartAnalysisError("FLOWCHART_VISION_MODEL_NOT_CONFIGURED", "请先配置流程图视觉模型")
        info = model_cache.get_model_info(model_spec)
        if info is None:
            raise FlowchartAnalysisError("FLOWCHART_VISION_MODEL_NOT_CONFIGURED", "流程图视觉模型未注册或未启用")
        modalities = {str(item).lower() for item in info.input_modalities}
        if not modalities:
            raise FlowchartAnalysisError(
                "FLOWCHART_VISION_CAPABILITY_UNKNOWN",
                "所选模型缺少 input_modalities 能力元数据，暂不能确认是否支持图片输入",
            )
        if info.model_type != "chat" or not ({"image", "images", "vision"} & modalities):
            raise FlowchartAnalysisError(
                "FLOWCHART_VISION_MODEL_UNSUPPORTED", "所选模型明确声明的 input_modalities 不支持图片输入"
            )

        try:
            model = select_model(model_spec=model_spec, temperature=0)
        except Exception as exc:
            raise FlowchartAnalysisError("FLOWCHART_VISION_PROVIDER_ERROR", "视觉模型初始化失败") from exc

        pages = []
        for image in images:
            data_url = "data:image/png;base64," + base64.b64encode(image.png_bytes).decode("ascii")
            messages = [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": f"{prompt}\n当前图像为 PDF 第 {image.page_number} 页。"},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                }
            ]
            try:
                response = await asyncio.wait_for(
                    model.model.ainvoke(convert_to_messages(messages)), timeout=config.flowchart_vision_timeout_seconds
                )
            except TimeoutError as exc:
                raise FlowchartAnalysisError(
                    "FLOWCHART_VISION_TIMEOUT", f"第 {image.page_number} 页视觉解析超时"
                ) from exc
            except Exception as exc:
                raise FlowchartAnalysisError(
                    "FLOWCHART_VISION_PROVIDER_ERROR", f"第 {image.page_number} 页视觉服务调用失败"
                ) from exc
            text = response.content.strip() if isinstance(response.content, str) else ""
            if not text:
                raise FlowchartAnalysisError("FLOWCHART_EMPTY_RESULT", f"第 {image.page_number} 页视觉模型返回空内容")
            if len(text) > config.flowchart_max_response_chars:
                raise FlowchartAnalysisError("FLOWCHART_INVALID_OUTPUT", f"第 {image.page_number} 页模型输出超过上限")
            _parse_sections(text)
            pages.append(PageFlowchartAnalysis(image.page_number, text, image.warnings))

        return FlowchartAnalysisResult(
            pages=tuple(pages),
            semantic_markdown=_synthesize_pages(pages),
            warnings=tuple(dict.fromkeys(warning for page in pages for warning in page.warnings)),
            model_spec=model_spec,
        )


def _parse_sections(markdown: str) -> tuple[str, dict[str, str]]:
    text = markdown.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:markdown|md)?\s*\n|\n```\s*$", "", text, flags=re.IGNORECASE)
    headings = list(re.finditer(r"(?m)^## (.+?)\s*$", text))
    title_match = re.match(r"^# (.+?)\s*(?:\n|$)", text)
    if (
        not title_match
        or not title_match.group(1).strip()
        or title_match.group(1).strip() == "流程名称"
        or len(headings) != len(SECTION_NAMES)
    ):
        raise FlowchartAnalysisError("FLOWCHART_INVALID_OUTPUT", "视觉模型未按流程语义模板输出")
    names = [heading.group(1).strip() for heading in headings]
    if names != list(SECTION_NAMES):
        raise FlowchartAnalysisError("FLOWCHART_INVALID_OUTPUT", "视觉模型的流程语义章节不完整或顺序错误")
    sections = {}
    for index, heading in enumerate(headings):
        end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
        body = text[heading.end() : end].strip()
        if not body:
            raise FlowchartAnalysisError("FLOWCHART_INVALID_OUTPUT", f"章节“{names[index]}”为空")
        sections[names[index]] = body
    return title_match.group(1).strip(), sections


def validate_semantic_markdown(markdown: str) -> None:
    """Reject model output that does not satisfy the fixed flowchart section contract."""
    if not markdown.lstrip().startswith("# "):
        raise FlowchartAnalysisError("FLOWCHART_INVALID_OUTPUT", "流程图语义 Markdown 必须以流程名称标题开始")
    _parse_sections(markdown)


def _synthesize_pages(pages: list[PageFlowchartAnalysis]) -> str:
    """Merge only page evidence; page markers are generated by code, never by the model."""
    parsed = [_parse_sections(page.markdown) for page in pages]
    title = next((name for name, _ in parsed if name != "无法从流程图确认"), parsed[0][0])
    sections = [f"# {title}"]
    for name in SECTION_NAMES:
        sections.append(f"## {name}")
        for page, (_, content) in zip(pages, parsed, strict=True):
            body = content[name]
            sections.append(f"<!-- page:{page.page_number} -->\n{body}")
        if name == "关键上下游关系" and len(pages) > 1:
            sections.append("跨页关系无法确认；仅保留各页可直接证明的关系。")
    return "\n\n".join(sections).strip() + "\n"
