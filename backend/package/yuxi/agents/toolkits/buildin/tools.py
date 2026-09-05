import os
import re
from pathlib import Path
from typing import Annotated

from langchain.tools import InjectedToolCallId
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolRuntime
from langgraph.types import Command, interrupt
from pydantic import BaseModel, Field

from yuxi.agents.toolkits.registry import ToolExtraMetadata, _all_tool_instances, _extra_registry, tool
from yuxi.utils import logger
from yuxi.utils.paths import (
    CONVERSATION_HISTORY_DIR_NAME,
    LARGE_TOOL_RESULTS_DIR_NAME,
    OUTPUTS_DIR_NAME,
    UPLOADS_DIR_NAME,
    VIRTUAL_PATH_OUTPUTS,
    WORKSPACE_DIR_NAME,
)
from yuxi.utils.question_utils import normalize_questions

# Lazy initialization for TavilySearch (only when API key is available)
_tavily_search_instance = None

_PRESENT_ARTIFACTS_INTERNAL_DIR_NAMES = frozenset(
    {CONVERSATION_HISTORY_DIR_NAME, LARGE_TOOL_RESULTS_DIR_NAME, "large_tool_history"}
)
_OCR_PARSE_ALLOWED_DIRS = frozenset({WORKSPACE_DIR_NAME, UPLOADS_DIR_NAME, OUTPUTS_DIR_NAME})
_OCR_OUTPUT_DIR_NAME = "ocr"
_OCR_PREVIEW_LIMIT = 1200
_SAFE_OUTPUT_STEM_RE = re.compile(r"[^A-Za-z0-9._\-\u4e00-\u9fff]+")


def _create_tavily_search():
    """Create and register TavilySearch tool with metadata."""
    global _tavily_search_instance
    if _tavily_search_instance is None:
        from langchain_tavily import TavilySearch

        _tavily_search_instance = TavilySearch()

    return _tavily_search_instance


# 注册 TavilySearch 工具（延迟初始化）
def _register_tavily_tool():
    """Register TavilySearch tool with extra metadata."""
    tavily_instance = _create_tavily_search()
    # 手动注册到全局注册表
    _extra_registry["tavily_search"] = ToolExtraMetadata(
        category="buildin",
        tags=["搜索"],
        display_name="Tavily 网页搜索",
    )
    # 添加到工具实例列表
    _all_tool_instances.append(tavily_instance)


# 模块加载时注册
if os.getenv("TAVILY_API_KEY"):
    try:
        _register_tavily_tool()
    except Exception as e:
        logger.warning(f"Failed to register TavilySearch tool: {e}")


class PresentArtifactsInput(BaseModel):
    """Expose artifact files to the frontend after the agent finishes."""

    filepaths: list[str] = Field(
        description=f"需要展示给用户的文件绝对路径列表，只允许位于 {VIRTUAL_PATH_OUTPUTS} 下，且不能是内部运行文件"
    )


def _normalize_presented_artifact_path(filepath: str, runtime: ToolRuntime) -> str:
    from yuxi.agents.backends.sandbox.paths import (
        VIRTUAL_PATH_PREFIX,
        ensure_thread_dirs,
        resolve_virtual_path,
        sandbox_outputs_dir,
    )

    outputs_virtual_prefix = f"{VIRTUAL_PATH_PREFIX}/outputs"
    runtime_context = runtime.context
    thread_id = getattr(runtime_context, "file_thread_id", None) or getattr(runtime_context, "thread_id", None)
    if not thread_id:
        raise ValueError("当前运行时缺少 thread_id")
    uid = getattr(runtime_context, "uid", None)
    if not uid:
        raise ValueError("当前运行时缺少 uid")

    ensure_thread_dirs(thread_id, str(uid))
    outputs_dir = sandbox_outputs_dir(thread_id).resolve()
    normalized_input = str(filepath or "").strip()
    if not normalized_input:
        raise ValueError("文件路径不能为空")

    stripped = normalized_input.lstrip("/")
    virtual_prefix = VIRTUAL_PATH_PREFIX.lstrip("/")
    if stripped == virtual_prefix or stripped.startswith(f"{virtual_prefix}/"):
        actual_path = resolve_virtual_path(thread_id, normalized_input, uid=str(uid))
    else:
        actual_path = Path(normalized_input).expanduser().resolve()

    if not actual_path.exists() or not actual_path.is_file():
        raise ValueError(f"文件不存在或不是普通文件: {normalized_input}")

    try:
        relative_path = actual_path.relative_to(outputs_dir)
    except ValueError as exc:
        raise ValueError(f"只允许展示 {outputs_virtual_prefix}/ 下的文件: {normalized_input}") from exc

    if relative_path.parts and relative_path.parts[0] in _PRESENT_ARTIFACTS_INTERNAL_DIR_NAMES:
        raise ValueError(f"不允许展示工具调用阶段文件: {outputs_virtual_prefix}/{relative_path.as_posix()}")

    return f"{outputs_virtual_prefix}/{relative_path.as_posix()}"


PRESENT_ARTIFACTS_DESCRIPTION = f"""
将已经生成好的结果文件展示给用户。

使用场景：
1. 你已经在 `{VIRTUAL_PATH_OUTPUTS}` 下写好了最终结果文件
2. 你希望前端在对话结束后显示这些结果文件卡片
3. 这些文件需要支持下载或预览

注意事项：
1. 只能传入 `{VIRTUAL_PATH_OUTPUTS}` 下的文件
2. 不要传入中间过程文件，只有真正需要给用户看的结果文件才调用
3. 不要传入工具调用阶段文件，例如：
   - `{VIRTUAL_PATH_OUTPUTS}/{LARGE_TOOL_RESULTS_DIR_NAME}`
   - `{VIRTUAL_PATH_OUTPUTS}/{CONVERSATION_HISTORY_DIR_NAME}`
4. 可以一次传多个文件
"""


@tool(
    category="buildin",
    tags=["文件", "交付物"],
    display_name="展示交付物",
    description=PRESENT_ARTIFACTS_DESCRIPTION,
    args_schema=PresentArtifactsInput,
)
def present_artifacts(
    filepaths: list[str],
    runtime: ToolRuntime,
    tool_call_id: Annotated[str, InjectedToolCallId],
) -> Command:
    """登记当前线程 outputs 目录下的交付物文件，使前端在对话结束后展示给用户。"""
    try:
        normalized_paths = [_normalize_presented_artifact_path(filepath, runtime) for filepath in filepaths]
    except ValueError as exc:
        return Command(update={"messages": [ToolMessage(content=f"Error: {exc}", tool_call_id=tool_call_id)]})

    return Command(
        update={
            "artifacts": normalized_paths,
            "messages": [ToolMessage(content="已将交付物展示给用户", tool_call_id=tool_call_id)],
        }
    )


class OcrParseFileInput(BaseModel):
    """Parse a sandbox file with OCR and save the Markdown result."""

    file_path: str = Field(description="需要 OCR 解析的沙盒虚拟路径，必须位于 /home/gem/user-data 下")
    ocr_engine: str | None = Field(default=None, description="可选 OCR 引擎；省略时使用系统默认 OCR 引擎")


OCR_PARSE_FILE_DESCRIPTION = f"""
将沙盒中的 PDF、Office 文档或图片文件解析为 Markdown 文本，并把结果保存为文件。

使用场景：
1. 用户上传了 PDF、Office 文档或图片附件，需要提取其中的文字内容
2. 工作区、uploads 或 outputs 下已有文件，需要转成可读取的 Markdown
3. 解析结果较长，后续应使用 read_file 读取保存后的 Markdown 文件

注意事项：
1. file_path 必须是 /home/gem/user-data 下的虚拟路径
2. 只允许读取 workspace、uploads、outputs 下的普通文件
3. 解析结果会写入 {VIRTUAL_PATH_OUTPUTS}/{_OCR_OUTPUT_DIR_NAME}/
4. 工具只返回结果文件路径和短预览，不直接返回完整 OCR 文本
5. 如需在前端展示结果文件，请再调用 present_artifacts
"""


@tool(
    category="buildin",
    tags=["文件", "OCR"],
    display_name="OCR 解析文件",
    description=OCR_PARSE_FILE_DESCRIPTION,
    args_schema=OcrParseFileInput,
)
async def ocr_parse_file(file_path: str, runtime: ToolRuntime, ocr_engine: str | None = None) -> dict:
    """Parse a sandbox file with OCR, persist Markdown output, and return only a short result summary."""
    from yuxi.agents.backends.sandbox.paths import virtual_path_for_thread_file
    from yuxi.knowledge.parser.unified import Parser

    file_thread_id, uid, actual_path = _resolve_ocr_source_path(file_path, runtime)
    engine = _resolve_ocr_engine(ocr_engine)
    markdown = await Parser.aparse(str(actual_path), params={"ocr_engine": engine})

    output_path = _next_ocr_output_path(file_thread_id, actual_path)
    output_path.write_text(markdown, encoding="utf-8")
    parsed_path = virtual_path_for_thread_file(file_thread_id, output_path, uid=uid)
    source_virtual_path = virtual_path_for_thread_file(file_thread_id, actual_path, uid=uid)
    preview, truncated = _ocr_preview(markdown)

    return {
        "source_path": source_virtual_path,
        "parsed_path": parsed_path,
        "ocr_engine": engine,
        "char_count": len(markdown),
        "preview": preview,
        "truncated": truncated,
    }


def _extract_user_image_data_uri(runtime: ToolRuntime) -> str | None:
    """从运行时状态的消息里取最近一张用户图片的 data URI（无则返回 None）。"""
    state = getattr(runtime, "state", None) or {}
    messages = state.get("messages") if isinstance(state, dict) else getattr(state, "messages", None)
    if not messages:
        return None
    for message in reversed(list(messages)):
        if getattr(message, "type", None) != "human":
            continue
        content = getattr(message, "content", None)
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict) or part.get("type") != "image_url":
                continue
            image_url = part.get("image_url")
            url = image_url.get("url") if isinstance(image_url, dict) else str(image_url or "")
            if isinstance(url, str) and url.startswith("data:image/"):
                return url
    return None


class ProductImageSearchInput(BaseModel):
    """Search product reference images in the knowledge base by appearance."""

    query_text: str = Field(default="", description="可选的文字描述，用于补充/限定检索意图")


PRODUCT_IMAGE_SEARCH_DESCRIPTION = """按产品图片外观检索知识库中的产品参照图，返回外观最相近的产品候选。

适用场景：用户上传的产品图片上无铭牌/型号文字、或属于贴牌产品时，无法直接读出型号。
此工具把当前用户图片与知识库中已建索引的产品参照图做外观相似度匹配，返回外观相近的产品候选（含所属知识库、产品名、相似度）。

注意事项：
1. 必须已有用户上传的图片（取当前对话中最新一张用户图片）。
2. 参照图由运营在知识库中维护（MinIO public/{kb_id}/product-images/{产品名}.jpg）并建立索引后才有效；
   无索引时返回空列表。
3. 返回的是外观候选，最终型号请与用户确认后再定，不要仅凭相似度直接断定型号。
"""


@tool(
    category="buildin",
    tags=["产品识别", "图片"],
    display_name="按外观检索产品",
    description=PRODUCT_IMAGE_SEARCH_DESCRIPTION,
    args_schema=ProductImageSearchInput,
)
async def search_product_image(query_text: str = "", runtime: ToolRuntime = None) -> dict:
    """按产品图片外观检索知识库中的产品参照图，返回外观最相近的产品候选。"""
    data_uri = _extract_user_image_data_uri(runtime)
    if not data_uri:
        return {"error": "当前对话中没有可用的用户图片，无法按外观检索产品。", "matches": []}

    from yuxi.knowledge.product_image_index import ProductImageIndex

    try:
        matches = await ProductImageIndex().search(data_uri, top_k=5)
    except Exception as exc:
        logger.error(f"search_product_image 检索失败: {exc}")
        return {"error": f"产品参照图检索失败：{exc}", "matches": []}
    return {"query_text": query_text, "matches": matches}


class ProductImageRecognizeInput(BaseModel):
    """本地识别产品型号的输入：可显式给沙盒虚拟路径，或省略取最新用户图片。"""

    file_path: str | None = Field(
        default=None,
        description="可选的沙盒虚拟图片路径（uploads/workspace/outputs 下）；省略时取当前对话最新一张用户图片",
    )


PRODUCT_RECOGNIZE_DESCRIPTION = """本地识别产品/设备图片中的具体型号（内置 8 款：艾尔锐EH01、倍控M200、彬其E600、
九分F10、森海克斯D11、森海克斯D12、森海克斯D21、森海克斯D22）。

适用场景：用户上传的产品图片上看不出型号——无铭牌、铭牌文字不清、贴牌产品、或需按外观确认具体型号时，
调用本地目标检测模型做一次外观识别，得到最可能的型号候选。
不适用：普通聊天截图、文档/表格图片等非产品图——不要调用本工具。

返回 detections（按置信度降序的候选型号列表）与 hit（最可能型号是否达标）。识别结果只是线索：给出型号结论前应
结合知识库检索（query_kbs）核对参数；若本地识别模型未启用（enabled=false）或 detections 为空，表示没有识别出
内置几款型号，应改用 OCR 读铭牌文字或按外观检索（search_product_image），不要臆断型号。
"""


@tool(
    category="buildin",
    tags=["产品识别", "图片"],
    display_name="本地识别产品型号",
    description=PRODUCT_RECOGNIZE_DESCRIPTION,
    args_schema=ProductImageRecognizeInput,
)
async def recognize_product_image(file_path: str | None = None, runtime: ToolRuntime = None) -> dict:
    """用本地 YOLO 模型识别用户图片中的产品型号（内置 8 款），返回候选型号与命中标志。"""
    from yuxi.knowledge.product_detector import get_product_detector, top_hit

    detector = get_product_detector()
    if not detector.available:
        return {"enabled": False, "hit": False, "detections": []}

    if file_path:
        source = "file_path"
        try:
            _, _, actual_path = _resolve_ocr_source_path(file_path, runtime)
        except ValueError as exc:
            return {"enabled": True, "hit": False, "detections": [], "note": str(exc)}
        payload = actual_path.read_bytes()
    else:
        source = "latest_user_image"
        payload = _extract_user_image_data_uri(runtime)
        if not payload:
            return {"enabled": True, "hit": False, "detections": [], "note": "当前对话中没有可用的用户图片。"}

    try:
        detections = await detector.detect(payload)
    except Exception as exc:  # noqa: BLE001
        logger.error(f"recognize_product_image 识别失败: {exc}")
        return {"enabled": True, "hit": False, "detections": [], "note": f"本地识别失败：{exc}"}

    result: dict = {"enabled": True, "source": source, "hit": top_hit(detections) is not None, "detections": detections}
    if not detections:
        result["note"] = "未识别出内置 8 款产品型号，请改用 OCR 读铭牌文字或按外观检索。"
    return result


def _resolve_ocr_source_path(file_path: str, runtime: ToolRuntime) -> tuple[str, str, Path]:
    """Resolve a sandbox virtual path to a host file inside the Agent-visible user-data roots."""
    from yuxi.agents.backends.sandbox.paths import get_virtual_path_prefix, resolve_virtual_path

    file_thread_id, uid = _resolve_runtime_file_scope(runtime)

    normalized_input = str(file_path or "").strip()
    if not normalized_input:
        raise ValueError("文件路径不能为空")

    virtual_prefix = get_virtual_path_prefix().rstrip("/")
    clean_virtual_path = "/" + normalized_input.lstrip("/")
    if clean_virtual_path != virtual_prefix and not clean_virtual_path.startswith(f"{virtual_prefix}/"):
        raise ValueError(f"只允许解析 {virtual_prefix} 下的沙盒虚拟路径")

    relative_path = clean_virtual_path[len(virtual_prefix) :].lstrip("/")
    namespace = Path(relative_path).parts[0] if relative_path else ""
    if namespace not in _OCR_PARSE_ALLOWED_DIRS:
        allowed = ", ".join(f"{virtual_prefix}/{item}" for item in sorted(_OCR_PARSE_ALLOWED_DIRS))
        raise ValueError(f"只允许解析 {allowed} 下的文件")

    try:
        actual_path = resolve_virtual_path(file_thread_id, clean_virtual_path, uid=uid)
    except ValueError as exc:
        raise ValueError(f"只允许解析 {virtual_prefix} 下的沙盒虚拟路径") from exc
    if not actual_path.exists():
        raise ValueError(f"文件不存在: {clean_virtual_path}")
    if not actual_path.is_file():
        raise ValueError(f"路径不是普通文件: {clean_virtual_path}")

    return file_thread_id, uid, actual_path


def _resolve_runtime_file_scope(runtime: ToolRuntime) -> tuple[str, str]:
    """Read the thread and user scope needed for sandbox path mapping from ToolRuntime."""
    thread_id = _runtime_scope_value(runtime, "file_thread_id") or _runtime_scope_value(runtime, "thread_id")
    uid = _runtime_scope_value(runtime, "uid")
    if not thread_id:
        raise ValueError("当前运行时缺少 thread_id")
    if not uid:
        raise ValueError("当前运行时缺少 uid")
    return thread_id, uid


def _runtime_scope_value(runtime: ToolRuntime, key: str) -> str | None:
    """Look up a runtime scope value from LangGraph config, context, or state."""
    config = getattr(runtime, "config", None)
    configurable = config.get("configurable", {}) if isinstance(config, dict) else {}
    sources = (
        configurable if isinstance(configurable, dict) else {},
        getattr(runtime, "context", None),
        getattr(runtime, "state", None) if isinstance(getattr(runtime, "state", None), dict) else {},
    )
    for source in sources:
        value = source.get(key) if isinstance(source, dict) else getattr(source, key, None)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _resolve_ocr_engine(ocr_engine: str | None) -> str:
    """Validate the requested OCR engine, falling back to the system default when omitted."""
    from yuxi import config
    from yuxi.knowledge.parser.factory import DocumentProcessorFactory

    engine = str(ocr_engine or config.default_ocr_engine).strip() or config.default_ocr_engine
    allowed = {"disable", *DocumentProcessorFactory.get_available_processors()}
    if engine not in allowed:
        raise ValueError(f"不支持的 OCR 引擎: {engine}")
    return engine


def _next_ocr_output_path(thread_id: str, source_path: Path) -> Path:
    """Choose a non-conflicting Markdown output path under the thread outputs/ocr directory."""
    from yuxi.agents.backends.sandbox.paths import sandbox_outputs_dir

    output_dir = sandbox_outputs_dir(thread_id) / _OCR_OUTPUT_DIR_NAME
    output_dir.mkdir(parents=True, exist_ok=True)

    base_name = _safe_ocr_output_stem(source_path)
    candidate = output_dir / f"{base_name}.md"
    index = 1
    while candidate.exists():
        candidate = output_dir / f"{base_name}-{index}.md"
        index += 1
    return candidate


def _safe_ocr_output_stem(source_path: Path) -> str:
    """Build a filesystem-friendly output filename stem from the source file name."""
    stem = source_path.stem.strip() or "ocr_result"
    safe_stem = _SAFE_OUTPUT_STEM_RE.sub("_", stem).strip("._-")
    return safe_stem or "ocr_result"


def _ocr_preview(markdown: str) -> tuple[str, bool]:
    """Return the short preview included in the tool result and whether it was truncated."""
    if len(markdown) <= _OCR_PREVIEW_LIMIT:
        return markdown, False
    return markdown[:_OCR_PREVIEW_LIMIT].rstrip(), True


ASK_USER_QUESTION_DESCRIPTION = """
在执行过程中，当你需要用户做决定或补充需求时，使用这个工具向用户提问。

适用场景：
1. 收集用户偏好或需求（例如风格、范围、优先级）
2. 澄清模糊指令（存在多种合理解释时）
3. 在实现过程中让用户选择方案方向
4. 在有明显权衡时让用户做取舍

使用规范：
1. questions 提供 1-5 个问题，每项包含：question、options、multi_select、allow_other
2. 每个问题的 options 提供 2-5 个有区分度的选项，每项包含 label 和 value
3. 若有推荐选项：把推荐项放在第一位，并在 label 末尾加 "(Recommended)"
4. 若需要多选：将该问题的 multi_select 设为 true
5. allow_other 通常保持 true，用户可通过 Other 输入自定义答案

注意事项：
1. 不要用这个工具询问“是否继续执行”“计划是否准备好”这类流程控制问题
2. 不要在信息已充分、无需用户决策时滥用该工具
3. 先基于现有上下文自行决策，只有关键不确定性时才提问

返回结果：
answer 为 object，格式为 {question_id: answer}。
其中 answer 可能是 string（单选）、list（多选）或 object（Other 文本）。
"""


@tool(
    category="buildin",
    tags=["交互"],
    display_name="向用户提问",
    description=ASK_USER_QUESTION_DESCRIPTION,
)
def ask_user_question(
    questions: Annotated[
        list[dict] | str | None,
        "问题列表，每项格式 {question, options, multi_select, allow_other, question_id(optional)}",
    ] = None,
) -> dict:
    """向用户发起问题并等待回答。"""
    # 解析 questions 参数：如果是字符串，尝试解析为 JSON
    if isinstance(questions, str):
        try:
            import json

            questions = json.loads(questions)
            logger.debug(f"Parsed string questions to list: {questions}")
        except Exception as e:
            logger.error(f"Failed to parse questions string: {e}, using None")
            questions = None

    normalized_questions = normalize_questions(questions or [])

    if not normalized_questions:
        raise ValueError("questions 至少需要包含一个有效问题")

    interrupt_payload = {
        "questions": normalized_questions,
        "source": "ask_user_question",
    }
    answer = interrupt(interrupt_payload)

    return {
        "questions": normalized_questions,
        "answer": answer,
    }
