"""default-chatbot 系统提示词组装与图片展示指令的单测。"""

from __future__ import annotations

from types import SimpleNamespace

from yuxi.agents.buildin.chatbot.prompt import (
    BUSINESS_RESPONSE_PROMPT,
    HARD_GUARDRAILS_PROMPT,
    IMAGE_RESPONSE_PROMPT,
    KNOWLEDGE_REFUSAL_REPLY,
    KNOWLEDGE_REFUSAL_REPLY_EN,
    PRODUCT_RECOGNITION_PROMPT,
    SYSTEM_ERROR_REPLY,
    SYSTEM_ERROR_REPLY_EN,
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
    assert "不得仅凭相似度" in prompt
    assert "直接断定型号" in prompt
    assert "不得编造型号" in prompt


def test_build_prompt_includes_bilingual_fixed_refusals():
    prompt = build_prompt_with_context(_empty_context())

    assert KNOWLEDGE_REFUSAL_REPLY in prompt
    assert KNOWLEDGE_REFUSAL_REPLY_EN in prompt
    assert SYSTEM_ERROR_REPLY in prompt
    assert SYSTEM_ERROR_REPLY_EN in prompt
    assert "中文问题只回复" in prompt
    assert "英文问题只回复" in prompt


def test_business_response_prompt_covers_question_types_and_product_line():
    prompt = build_prompt_with_context(_empty_context())

    for label in ("操作类", "参数类", "支持类", "排查类", "概念类"):
        assert label in prompt
    assert "锁定产品线" in prompt
    assert "不得作为回答依据" in prompt
    assert "产品对比" in prompt
    assert "问题指代不清" in prompt


def test_business_response_section_defers_to_unified_refusal():
    """产品线锁定只约束「用哪条线的知识」，本线缺料时仍由统一拒答兜底，不得自己另起话术。"""
    prompt = build_prompt_with_context(_empty_context())

    assert prompt.index(BUSINESS_RESPONSE_PROMPT.strip()) < prompt.index(HARD_GUARDRAILS_PROMPT.strip())
    assert "仍按“知识证据与统一拒答”处理" in BUSINESS_RESPONSE_PROMPT


def test_build_prompt_requires_artifact_registration():
    """成果文件必须登记，否则前端不会展示——这条硬要求要常驻系统提示词，不能只写在技能文档里。"""
    prompt = build_prompt_with_context(_empty_context())

    assert "present_artifacts" in prompt
    assert "未登记的文件不会出现在对话中" in prompt
    # 子智能体侧该工具被硬禁用（_SUBAGENT_DISABLED_TOOLS），提示词必须把登记责任交回主智能体
    assert "子智能体不具备 `present_artifacts`" in prompt
    assert "由你确认后用 `present_artifacts` 登记" in prompt


def test_product_recognition_section_before_image_and_guardrails():
    prompt = build_prompt_with_context(_empty_context())

    recognition = prompt.index(PRODUCT_RECOGNITION_PROMPT.strip())
    assert recognition < prompt.index(IMAGE_RESPONSE_PROMPT.strip())
    assert recognition < prompt.index(VISUALIZATION_PROMPT.strip())
    assert prompt.index(IMAGE_RESPONSE_PROMPT.strip()) < prompt.index(HARD_GUARDRAILS_PROMPT.strip())
