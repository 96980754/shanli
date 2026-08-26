"""default-chatbot 系统提示词组装与图片展示指令的单测。"""

from __future__ import annotations

from types import SimpleNamespace

from yuxi.agents.buildin.chatbot.prompt import (
    HARD_GUARDRAILS_PROMPT,
    IMAGE_RESPONSE_PROMPT,
    PRODUCT_RECOGNITION_PROMPT,
    VISUALIZATION_PROMPT,
    build_prompt_with_context,
)


def _empty_context() -> SimpleNamespace:
    return SimpleNamespace(system_prompt="")


def test_build_prompt_includes_image_response_instruction():
    prompt = build_prompt_with_context(_empty_context())

    assert "图片展示" in prompt
    assert "以 Markdown 图片形式" in prompt
    assert "只能使用检索结果中出现的原始图片 URL" in prompt
    assert "不要虚构图片链接" in prompt


def test_image_response_section_before_hard_guardrails():
    prompt = build_prompt_with_context(_empty_context())

    assert prompt.index(IMAGE_RESPONSE_PROMPT.strip()) < prompt.index(HARD_GUARDRAILS_PROMPT.strip())
    assert prompt.index(IMAGE_RESPONSE_PROMPT.strip()) < prompt.index(VISUALIZATION_PROMPT.strip())


def test_business_system_prompt_kept_before_fixed_sections():
    prompt = build_prompt_with_context(SimpleNamespace(system_prompt="客户业务配置 A"))

    assert "客户业务配置 A" in prompt
    assert "图片展示" in prompt


def test_build_prompt_includes_product_recognition_instruction():
    prompt = build_prompt_with_context(_empty_context())

    assert "产品图片识别" in prompt
    assert "search_product_image" in prompt
    assert "ask_user_question" in prompt
    assert "不得仅凭相似度直接断定型号" in prompt
    assert "不得编造型号" in prompt


def test_product_recognition_section_before_image_and_guardrails():
    prompt = build_prompt_with_context(_empty_context())

    recognition = prompt.index(PRODUCT_RECOGNITION_PROMPT.strip())
    assert recognition < prompt.index(IMAGE_RESPONSE_PROMPT.strip())
    assert recognition < prompt.index(VISUALIZATION_PROMPT.strip())
    assert prompt.index(IMAGE_RESPONSE_PROMPT.strip()) < prompt.index(HARD_GUARDRAILS_PROMPT.strip())
