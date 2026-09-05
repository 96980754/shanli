from __future__ import annotations

import base64
import hashlib
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any
from uuid import uuid4

from langchain.agents.middleware.types import AgentMiddleware, ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_openai import ChatOpenAI

from yuxi.agents.backends.sandbox.paths import (
    ensure_thread_dirs,
    sandbox_uploads_dir,
    virtual_path_for_thread_file,
)
from yuxi.agents.toolkits.buildin.tools import ocr_parse_file, recognize_product_image
from yuxi.knowledge.product_detector import get_product_detector

_TOOL_IMAGE_USER_TEXT = "Images returned by read_file are attached below. Inspect them when answering."
_IMAGE_ERROR_TERMS = ("image", "vision", "multimodal", "multi-modal")
_REJECTION_TERMS = (
    "does not support",
    "no endpoints found that support",
    "not allowed",
    "not a vlm",
    "not supported",
    "text-only prompts",
    "unsupported",
)
# 用户对话里上传的产品图片：模型拒绝图片输入时落沙盒 uploads 供 ocr_parse_file / recognize_product_image 解析。
_CHAT_IMAGE_STEM = "chat-image"
_CHAT_IMAGE_PLACEHOLDER = "（用户上传的图片，内容已由系统工具解析）"
# 图片被某工具（OCR/本地识别）在后续消息中解析时，该图片块不再原样进纯文本模型。
_HANDLED_TOOL_NAMES = frozenset({"ocr_parse_file", "recognize_product_image"})
_IMAGE_MIME_TO_EXT = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/bmp": ".bmp",
    "image/gif": ".gif",
}


class ImageInputCompatibilityMiddleware(AgentMiddleware[Any, Any, Any]):
    """Bridge OpenAI tool images and translate explicit image capability errors.

    product_detect=True 时（主对话纯文本兜底），图片被拒后优先注入本地产品型号识别，
    识别未命中再由模型自行调 OCR；否则维持 OCR 兜底现状。
    """

    tools = [ocr_parse_file, recognize_product_image]

    def __init__(self, product_detect: bool = False) -> None:
        self.product_detect = product_detect

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        image_paths = _read_file_image_paths(request.messages)
        request = _bridge_openai_tool_images(request)
        try:
            return handler(request)
        except Exception as exc:  # noqa: BLE001
            if _has_image(request.messages) and _is_image_input_rejection(exc):
                return _image_fallback_response(
                    image_paths or _materialize_chat_images(request),
                    product_detect=self.product_detect,
                )
            raise

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        image_paths = _read_file_image_paths(request.messages)
        request = _bridge_openai_tool_images(request)
        try:
            return await handler(request)
        except Exception as exc:  # noqa: BLE001
            if _has_image(request.messages) and _is_image_input_rejection(exc):
                return _image_fallback_response(
                    image_paths or _materialize_chat_images(request),
                    product_detect=self.product_detect,
                )
            raise


def _bridge_openai_tool_images(request: ModelRequest) -> ModelRequest:
    if not isinstance(request.model, ChatOpenAI):
        return request

    bridged_messages = []
    pending_images: list[dict[str, Any]] = []
    latest_handled_call_by_path: dict[str, tuple[int, str]] = {}
    for index, message in enumerate(request.messages):
        if not isinstance(message, AIMessage):
            continue
        for tool_call in message.tool_calls:
            if tool_call.get("name") not in _HANDLED_TOOL_NAMES:
                continue
            file_path = tool_call.get("args", {}).get("file_path")
            if isinstance(file_path, str) and file_path:
                latest_handled_call_by_path[file_path] = (index, str(tool_call.get("name")))

    def flush_pending_images() -> None:
        if not pending_images:
            return
        bridged_messages.append(
            HumanMessage(content_blocks=[{"type": "text", "text": _TOOL_IMAGE_USER_TEXT}, *pending_images])
        )
        pending_images.clear()

    for index, message in enumerate(request.messages):
        if not isinstance(message, ToolMessage):
            flush_pending_images()
            bridged_messages.append(_strip_handled_chat_images(message, index, latest_handled_call_by_path, request))
            continue

        image_blocks = [block for block in message.content_blocks if block.get("type") == "image"]
        if not image_blocks:
            bridged_messages.append(message)
            continue

        image_path = message.additional_kwargs.get("read_file_path")
        handled_by = None
        if isinstance(image_path, str):
            handled_entry = latest_handled_call_by_path.get(image_path)
            if handled_entry is not None and handled_entry[0] > index:
                handled_by = handled_entry[1]
        if handled_by is None:
            pending_images.extend(image_blocks)
        handled_note = "The image content is attached in the following user message for visual inspection."
        if handled_by == "ocr_parse_file":
            handled_note = "OCR fallback was requested for this image."
        elif handled_by == "recognize_product_image":
            handled_note = "Local product-model recognition was requested for this image."
        text = "\n".join(
            block["text"]
            for block in message.content_blocks
            if block.get("type") == "text" and isinstance(block.get("text"), str)
        )
        bridged_messages.append(
            message.model_copy(
                update={
                    "content": text
                    or f"read_file returned {len(image_blocks)} image(s). {handled_note}"
                }
            )
        )

    flush_pending_images()
    if bridged_messages == request.messages:
        return request
    return request.override(messages=bridged_messages)


def _request_thread_scope(request: ModelRequest) -> tuple[str, str] | None:
    """从模型请求的运行时上下文解析 (thread_id, uid)，供把用户图片落沙盒使用。"""
    context = getattr(getattr(request, "runtime", None), "context", None)
    thread_id = getattr(context, "file_thread_id", None) or getattr(context, "thread_id", None)
    uid = getattr(context, "uid", None)
    if isinstance(thread_id, str) and thread_id and isinstance(uid, str) and uid:
        return thread_id, uid
    return None


def _user_image_data_uris(messages: list[Any]) -> list[str]:
    """提取历史消息里用户上传的 base64 图片 data URI（保序、去重）。

    注意读原始 content（`{"type":"image_url",...}` 结构），content_blocks 已被 langchain
    归一化成 `{"type":"image","base64":...}`，丢失 data URI 前缀。
    """
    seen: set[str] = set()
    uris: list[str] = []
    for message in messages:
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
            if isinstance(url, str) and url.startswith("data:image/") and url not in seen:
                seen.add(url)
                uris.append(url)
    return uris


def _chat_image_location(data_uri: str, thread_id: str, uid: str) -> tuple[Path, bytes] | None:
    """解析用户 base64 图片的 (落盘路径, 字节)。按内容 sha 确定性命名，非法输入返回 None，不写盘。"""
    header, _, b64 = data_uri.partition(",")
    if not header.startswith("data:image/") or not b64:
        return None
    mime = header[len("data:") :].partition(";")[0]
    ext = _IMAGE_MIME_TO_EXT.get(mime, ".jpg")
    try:
        payload = base64.b64decode(b64, validate=True)
    except Exception:
        payload = base64.b64decode(b64)
    if not payload:
        return None
    filename = f"{_CHAT_IMAGE_STEM}-{hashlib.sha256(payload).hexdigest()[:16]}{ext}"
    return sandbox_uploads_dir(thread_id) / filename, payload


def _chat_image_virtual_path(data_uri: str, thread_id: str, uid: str) -> str | None:
    """计算用户 base64 图片的沙盒 uploads 虚拟路径（不写盘；失败返回 None）。"""
    try:
        location = _chat_image_location(data_uri, thread_id, uid)
        if location is None:
            return None
        return virtual_path_for_thread_file(thread_id, location[0], uid=uid)
    except Exception:
        return None


def _materialize_chat_images(request: ModelRequest) -> list[str]:
    """模型拒绝图片输入时，把对话里的用户 base64 图片落沙盒 uploads，返回可 OCR 的虚拟路径。"""
    scope = _request_thread_scope(request)
    if not scope:
        return []
    thread_id, uid = scope
    try:
        ensure_thread_dirs(thread_id, uid)
    except Exception:
        return []
    paths: list[str] = []
    for data_uri in _user_image_data_uris(request.messages):
        try:
            location = _chat_image_location(data_uri, thread_id, uid)
            if location is None:
                continue
            actual, payload = location
            if not actual.exists():
                actual.write_bytes(payload)
            paths.append(virtual_path_for_thread_file(thread_id, actual, uid=uid))
        except Exception:
            continue
    return paths


def _strip_handled_chat_images(
    message, index: int, latest_handled_call_by_path: dict[str, tuple[int, str]], request: ModelRequest
) -> Any:
    """同一对话中已被 OCR / 本地识别处理过的用户 base64 图片，不再发给纯文本模型。"""
    if getattr(message, "type", None) != "human" or not latest_handled_call_by_path:
        return message
    scope = _request_thread_scope(request)
    if not scope:
        return message
    thread_id, uid = scope
    content = getattr(message, "content", None)
    if not isinstance(content, list):
        return message
    kept: list[dict[str, Any]] = []
    dropped = False
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "image_url":
            kept.append(block)
            continue
        image_url = block.get("image_url")
        url = image_url.get("url") if isinstance(image_url, dict) else str(image_url or "")
        if not (isinstance(url, str) and url.startswith("data:image/")):
            kept.append(block)
            continue
        path = _chat_image_virtual_path(url, thread_id, uid)
        handled_entry = latest_handled_call_by_path.get(path) if path else None
        if handled_entry is not None and handled_entry[0] > index:
            dropped = True
            continue
        kept.append(block)
    if not dropped:
        return message
    kept = [
        block
        for block in kept
        if not (isinstance(block, dict) and block.get("type") == "text" and not str(block.get("text") or "").strip())
    ]
    if not kept:
        kept = [{"type": "text", "text": _CHAT_IMAGE_PLACEHOLDER}]
    return message.model_copy(update={"content": kept})


def _read_file_image_paths(messages: list[Any]) -> list[str]:
    paths: list[str] = []
    for message in messages:
        if not isinstance(message, ToolMessage):
            continue
        if not any(block.get("type") == "image" for block in message.content_blocks):
            continue
        path = message.additional_kwargs.get("read_file_path")
        if isinstance(path, str) and path and path not in paths:
            paths.append(path)
    return paths


def _image_fallback_response(image_paths: list[str], *, product_detect: bool = False) -> ModelResponse:
    """图片被纯文本模型拒绝时的兜底：优先本地产品识别，未启用时维持 OCR 提取文字。"""
    if not image_paths:
        return ModelResponse(result=[AIMessage(content="当前模型无法读取图片，且没有可供 OCR 工具解析的文件路径。")])

    use_detector = product_detect and get_product_detector().available
    tool_name = "recognize_product_image" if use_detector else "ocr_parse_file"
    id_prefix = "call_recog_" if use_detector else "call_ocr_"
    content = (
        "当前模型不支持图片输入，正在用本地识别模型判断图片中的产品型号。"
        if use_detector
        else "当前模型不支持图片输入，正在改用 OCR 工具提取图片文字。"
    )
    tool_calls = [
        {"name": tool_name, "args": {"file_path": path}, "id": f"{id_prefix}{uuid4().hex}"} for path in image_paths
    ]
    return ModelResponse(result=[AIMessage(content=content, tool_calls=tool_calls)])


def _has_image(messages: list[Any]) -> bool:
    return any(
        isinstance(block, dict) and block.get("type") in {"image", "image_url", "input_image"}
        for message in messages
        for block in getattr(message, "content_blocks", [])
    )


def _is_image_input_rejection(exc: Exception) -> bool:
    status_code = getattr(exc, "status_code", None)
    if status_code is None:
        status_code = getattr(getattr(exc, "response", None), "status_code", None)
    if status_code not in {400, 404, 415, 422} and not isinstance(exc, ValueError):
        return False

    detail = str(exc).lower()
    return any(term in detail for term in _IMAGE_ERROR_TERMS) and any(term in detail for term in _REJECTION_TERMS)
