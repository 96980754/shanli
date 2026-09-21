"""knowledge-base 技能文档与系统提示词的两套答法规则必须同步。

同一套「问题类型 → 答法」与产品线约束同时写在 `chatbot/prompt.py` 与
`knowledge-base/SKILL.md`，只改一处会让模型同时拿到两套说法。
"""

from __future__ import annotations

import pytest
from yuxi.agents.buildin.chatbot.prompt import BUSINESS_RESPONSE_PROMPT
from yuxi.agents.skills.buildin import BUILTIN_SKILLS

pytestmark = pytest.mark.unit

QUESTION_TYPE_LABELS = ("操作类", "参数类", "支持类", "排查类", "概念类")


def _knowledge_base_skill_md() -> str:
    for spec in BUILTIN_SKILLS:
        if spec.slug == "knowledge-base":
            return (spec.source_dir / "SKILL.md").read_text(encoding="utf-8")
    raise AssertionError("knowledge-base builtin skill spec not found")


def test_question_type_rules_match_system_prompt():
    skill_md = _knowledge_base_skill_md()

    for label in QUESTION_TYPE_LABELS:
        assert label in skill_md
        assert label in BUSINESS_RESPONSE_PROMPT


def test_product_line_rules_match_system_prompt():
    skill_md = _knowledge_base_skill_md()

    for rule in ("只使用该产品线的知识作答", "不得作为回答依据", "统一拒答"):
        assert rule in skill_md
        assert rule in BUSINESS_RESPONSE_PROMPT
