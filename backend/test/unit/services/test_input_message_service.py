"""build_chat_input_message 输出格式/行业方案指令注入的单测。"""

import pytest
from yuxi.services.input_message_service import (
    OUTPUT_FORMAT_INSTRUCTIONS,
    build_chat_input_message,
)


def _model_query(msg) -> str:
    """取 langchain_message 的文本内容（文本模式 content 是字符串）。"""
    return msg.require_langchain_message().content


def test_output_format_table_appends_instruction_and_keeps_content_clean():
    msg = build_chat_input_message("某产品有哪些认证？", output_format="table")

    assert msg.content == "某产品有哪些认证？"  # 持久化/展示内容保持原始问题
    model_query = _model_query(msg)
    assert "<output_format>" in model_query
    assert OUTPUT_FORMAT_INSTRUCTIONS["table"] in model_query
    assert "请严格遵守该输出格式" in model_query
    assert msg.extra_metadata["output_format"] == "table"


@pytest.mark.parametrize("output_format", ["steps", "list"])
def test_output_format_supported_values(output_format):
    msg = build_chat_input_message("如何部署？", output_format=output_format)

    assert msg.content == "如何部署？"
    assert OUTPUT_FORMAT_INSTRUCTIONS[output_format] in _model_query(msg)
    assert msg.extra_metadata["output_format"] == output_format


@pytest.mark.parametrize("output_format", ["default", "", None])
def test_output_format_default_or_empty_does_not_append(output_format):
    msg = build_chat_input_message("如何部署？", output_format=output_format)

    assert _model_query(msg) == "如何部署？"  # 不追加任何指令
    assert "output_format" not in msg.extra_metadata


def test_output_format_unknown_value_ignored_without_error():
    msg = build_chat_input_message("如何部署？", output_format="card")

    assert _model_query(msg) == "如何部署？"
    assert "output_format" not in msg.extra_metadata


def test_industry_solution_and_output_format_stack_keep_content_clean():
    industry_solution = {"industry": "电力", "requirement": "做一套监控方案", "products": []}
    msg = build_chat_input_message(
        "做一套监控方案",
        industry_solution=industry_solution,
        output_format="steps",
    )

    assert msg.content == "做一套监控方案"  # 两条指令都只在模型输入
    model_query = _model_query(msg)
    assert "<industry_solution_request>" in model_query
    assert "<output_format>" in model_query
    assert OUTPUT_FORMAT_INSTRUCTIONS["steps"] in model_query
    assert msg.extra_metadata["industry_solution"] == industry_solution
    assert msg.extra_metadata["output_format"] == "steps"


def test_build_chat_input_message_with_image_content_constructs_multimodal():
    msg = build_chat_input_message("这是什么产品？", image_content="iVBORw0KGgo=")

    assert msg.content == "这是什么产品？"  # 展示内容保持干净
    assert msg.message_type == "multimodal_image"
    assert msg.image_content == "iVBORw0KGgo="
    content = msg.require_langchain_message().content
    assert content == [
        {"type": "text", "text": "这是什么产品？"},
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,iVBORw0KGgo="}},
    ]


def test_build_chat_input_message_image_only_keeps_text_empty():
    msg = build_chat_input_message("", image_content="iVBORw0KGgo=")

    assert msg.content == ""
    assert msg.message_type == "multimodal_image"
    content = msg.require_langchain_message().content
    assert content[0] == {"type": "text", "text": ""}
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
