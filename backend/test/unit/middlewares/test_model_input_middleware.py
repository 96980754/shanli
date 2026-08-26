from __future__ import annotations

import base64
from types import SimpleNamespace

import pytest
from langchain.agents.middleware.types import ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_openai import ChatOpenAI

from yuxi.agents.middlewares.model_input import ImageInputCompatibilityMiddleware, _chat_image_virtual_path


def _request(model, messages, *, runtime=None) -> ModelRequest:
    return ModelRequest(model=model, messages=messages, runtime=runtime)


_ONE_PX_PNG = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
_NON_VLM_ERROR = "Error code: 400 - The model is not a VLM (Vision Language Model). Please use text-only prompts."


def _user_image_message(data_uri: str = f"data:image/png;base64,{_ONE_PX_PNG}", *, with_text: str = "") -> HumanMessage:
    content: list[dict] = [{"type": "image_url", "image_url": {"url": data_uri}}]
    if with_text:
        content.insert(0, {"type": "text", "text": with_text})
    return HumanMessage(content=content)


def _thread_runtime(thread_id: str = "thread-1", uid: str = "user-1") -> SimpleNamespace:
    return SimpleNamespace(context=SimpleNamespace(thread_id=thread_id, uid=uid))


def _non_vlm_handler():
    async def handler(_request):
        error = RuntimeError(_NON_VLM_ERROR)
        error.status_code = 400
        raise error

    return handler


def _openai_model() -> ChatOpenAI:
    return ChatOpenAI(model="test-model", api_key="test-key", base_url="https://example.com/v1")


def _read_file_image_message(path: str = "/home/gem/user-data/uploads/image.png") -> ToolMessage:
    return ToolMessage(
        content_blocks=[{"type": "image", "base64": "abc", "mime_type": "image/png"}],
        tool_call_id="call_image",
        additional_kwargs={"read_file_path": path, "read_file_media_type": "image/png"},
    )


def test_bridges_openai_tool_images_after_parallel_tool_results_without_mutating_state() -> None:
    middleware = ImageInputCompatibilityMiddleware()
    original_messages = [
        HumanMessage("读图并列目录"),
        ToolMessage(
            content_blocks=[{"type": "image", "base64": "abc", "mime_type": "image/png"}],
            name="read_file",
            tool_call_id="call_image",
        ),
        ToolMessage(content="['a.png']", name="ls", tool_call_id="call_ls"),
    ]
    seen = {}

    def handler(request):
        seen["messages"] = request.messages
        return ModelResponse(result=[AIMessage(content="ok")])

    middleware.wrap_model_call(_request(_openai_model(), original_messages), handler)

    messages = seen["messages"]
    assert original_messages[1].content_blocks[0]["type"] == "image"
    assert [message.type for message in messages] == ["human", "tool", "tool", "human"]
    assert messages[1].tool_call_id == "call_image"
    assert isinstance(messages[1].content, str)
    assert messages[2].tool_call_id == "call_ls"
    assert messages[3].content_blocks[1] == {
        "type": "image",
        "base64": "abc",
        "mime_type": "image/png",
    }


def test_keeps_non_openai_tool_images_unchanged() -> None:
    middleware = ImageInputCompatibilityMiddleware()
    messages = [
        ToolMessage(
            content_blocks=[{"type": "image", "base64": "abc", "mime_type": "image/png"}],
            tool_call_id="call_image",
        )
    ]
    seen = {}

    def handler(request):
        seen["messages"] = request.messages
        return ModelResponse(result=[AIMessage(content="ok")])

    middleware.wrap_model_call(_request(SimpleNamespace(), messages), handler)

    assert seen["messages"] is messages


@pytest.mark.asyncio
async def test_translates_explicit_provider_image_rejection() -> None:
    middleware = ImageInputCompatibilityMiddleware()
    request = _request(
        SimpleNamespace(),
        [_read_file_image_message()],
    )

    async def handler(_request):
        error = RuntimeError("This model does not support image input")
        error.status_code = 400
        raise error

    response = await middleware.awrap_model_call(request, handler)

    assert response.result[0].content == "当前模型不支持图片输入，正在改用 OCR 工具提取图片文字。"
    assert response.result[0].tool_calls[0]["name"] == "ocr_parse_file"
    assert response.result[0].tool_calls[0]["args"] == {"file_path": "/home/gem/user-data/uploads/image.png"}


@pytest.mark.asyncio
async def test_does_not_mask_unrelated_provider_errors_when_image_is_present() -> None:
    middleware = ImageInputCompatibilityMiddleware()
    request = _request(
        SimpleNamespace(),
        [HumanMessage(content=[{"type": "image_url", "image_url": {"url": "https://example.com/a.png"}}])],
    )

    async def handler(_request):
        error = RuntimeError("invalid tool schema")
        error.status_code = 400
        raise error

    with pytest.raises(RuntimeError, match="invalid tool schema"):
        await middleware.awrap_model_call(request, handler)


@pytest.mark.asyncio
async def test_translates_openrouter_missing_vision_endpoint() -> None:
    middleware = ImageInputCompatibilityMiddleware()
    request = _request(
        SimpleNamespace(),
        [_read_file_image_message()],
    )

    async def handler(_request):
        error = RuntimeError("No endpoints found that support image input")
        error.status_code = 404
        raise error

    response = await middleware.awrap_model_call(request, handler)

    assert response.result[0].tool_calls[0]["name"] == "ocr_parse_file"


@pytest.mark.asyncio
async def test_translates_siliconflow_non_vlm_error_without_retrying() -> None:
    middleware = ImageInputCompatibilityMiddleware()
    request = _request(SimpleNamespace(), [_read_file_image_message()])
    calls = 0

    async def handler(_request):
        nonlocal calls
        calls += 1
        error = RuntimeError(
            "Error code: 400 - {'code': 20041, 'message': "
            "'The model is not a VLM (Vision Language Model). Please use text-only prompts.'}"
        )
        error.status_code = 400
        raise error

    response = await middleware.awrap_model_call(request, handler)

    assert calls == 1
    assert response.result[0].tool_calls[0]["name"] == "ocr_parse_file"


def test_omits_historical_tool_image_after_ocr_fallback() -> None:
    middleware = ImageInputCompatibilityMiddleware()
    path = "/home/gem/user-data/uploads/image.png"
    messages = [
        _read_file_image_message(path),
        AIMessage(
            content="正在改用 OCR。",
            tool_calls=[
                {
                    "name": "ocr_parse_file",
                    "args": {"file_path": path},
                    "id": "call_ocr",
                }
            ],
        ),
        ToolMessage(content="OCR result", tool_call_id="call_ocr"),
    ]
    seen = {}

    def handler(request):
        seen["messages"] = request.messages
        return ModelResponse(result=[AIMessage(content="ok")])

    middleware.wrap_model_call(_request(_openai_model(), messages), handler)

    assert [message.type for message in seen["messages"]] == ["tool", "ai", "tool"]
    assert "OCR fallback was requested" in seen["messages"][0].content


def test_registers_ocr_tool_for_automatic_fallback() -> None:
    assert [tool.name for tool in ImageInputCompatibilityMiddleware().tools] == ["ocr_parse_file"]


@pytest.mark.asyncio
async def test_does_not_report_malformed_image_as_unsupported_model() -> None:
    middleware = ImageInputCompatibilityMiddleware()
    request = _request(
        SimpleNamespace(),
        [HumanMessage(content=[{"type": "image_url", "image_url": {"url": "broken"}}])],
    )

    async def handler(_request):
        error = RuntimeError("image_url provided is not a valid image")
        error.status_code = 400
        raise error

    with pytest.raises(RuntimeError, match="not a valid image"):
        await middleware.awrap_model_call(request, handler)


@pytest.mark.asyncio
async def test_materializes_user_chat_image_for_ocr_on_rejection(tmp_path, monkeypatch) -> None:
    """纯文本模型拒图时，把用户对话里的 base64 图落沙盒 uploads，供 ocr_parse_file 解析。"""
    monkeypatch.setattr("yuxi.config.save_dir", str(tmp_path))
    middleware = ImageInputCompatibilityMiddleware()
    request = _request(
        SimpleNamespace(),
        [_user_image_message()],
        runtime=_thread_runtime(),
    )

    response = await middleware.awrap_model_call(request, _non_vlm_handler())

    result = response.result[0]
    assert result.tool_calls[0]["name"] == "ocr_parse_file"
    file_path = result.tool_calls[0]["args"]["file_path"]
    assert file_path.startswith("/home/gem/user-data/uploads/chat-image-")
    uploads_dir = tmp_path / "threads" / "thread-1" / "user-data" / "uploads"
    files = list(uploads_dir.glob("chat-image-*"))
    assert len(files) == 1
    assert base64.b64encode(files[0].read_bytes()).decode() == _ONE_PX_PNG
    assert files[0].name == file_path.rsplit("/", 1)[-1]


def test_strips_handled_chat_image_from_followup_request(tmp_path, monkeypatch) -> None:
    """已被 OCR 过的用户图片不再发给纯文本模型，避免每轮都重触发 OCR。"""
    monkeypatch.setattr("yuxi.config.save_dir", str(tmp_path))
    middleware = ImageInputCompatibilityMiddleware()
    data_uri = f"data:image/png;base64,{_ONE_PX_PNG}"
    path = _chat_image_virtual_path(data_uri, "thread-1", "user-1")
    messages = [
        _user_image_message(data_uri, with_text=""),
        AIMessage(
            content="正在改用 OCR。",
            tool_calls=[{"name": "ocr_parse_file", "args": {"file_path": path}, "id": "call_ocr"}],
        ),
        ToolMessage(content="铭牌型号：X-2000", tool_call_id="call_ocr"),
    ]
    seen: dict[str, object] = {}

    def handler(request):
        seen["messages"] = request.messages
        return ModelResponse(result=[AIMessage(content="ok")])

    middleware.wrap_model_call(
        _request(_openai_model(), messages, runtime=_thread_runtime()),
        handler,
    )

    human = seen["messages"][0]
    blocks = list(human.content_blocks)
    assert not any(block.get("type") == "image_url" for block in blocks)
    assert blocks == [{"type": "text", "text": "（用户上传的产品图片，文字已由 OCR 工具提取）"}]


@pytest.mark.asyncio
async def test_user_image_fallback_without_thread_scope_keeps_honest_deadend() -> None:
    """拿不到沙盒作用域时不落盘，仍给出诚实的统一提示而不是抛异常。"""
    middleware = ImageInputCompatibilityMiddleware()
    request = _request(SimpleNamespace(), [_user_image_message()], runtime=None)

    response = await middleware.awrap_model_call(request, _non_vlm_handler())

    result = response.result[0]
    assert result.content == "当前模型无法读取图片，且没有可供 OCR 工具解析的文件路径。"
    assert result.tool_calls == []
