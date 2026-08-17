"""清洗质量校验器：确保清洗是“排版整理”而非“改写”。

校验数字、产品名/专有名词、长度的一致性。仅产生告警，不阻断；
若内容严重丢失（长度暴跌），标记需回退。
"""

from __future__ import annotations

import re

# 原文与清洗后文本的长度阈值
_MIN_KEEP_RATIO = 0.5  # 清洗后至少保留原文 50%
_MAX_GROW_RATIO = 2.0  # 清洗后最多膨胀到原文 200%（标题重排等轻微增长不误报）

# 数字 token：带单位的数值（如 4GB、32G、12.5%、1,000 元、2026 年）
_NUMBER_PATTERN = re.compile(
    r"\d+(?:[.,]\d+)*\s*(?:GB|G|MB|M|KB|K|TB|%|％|元|万元|亿|年|月|日|号|V|W|Hz|Mbps|km|m|cm|mm)?",
    re.IGNORECASE,
)

# 中文/英文专有名词候选：连续大写词（MCSTARS、POCSTARS、Geo-location、GB 等）
_ENTITY_PATTERN = re.compile(r"\b[A-Z][A-Za-z0-9-]{2,}\b")


def _extract_numbers(text: str) -> set[str]:
    return {m.group(0).strip() for m in _NUMBER_PATTERN.finditer(text)}


def _extract_entities(text: str) -> set[str]:
    return {m.group(0) for m in _ENTITY_PATTERN.finditer(text)}


def validate_clean_result(original: str, cleaned: str) -> dict:
    """校验清洗结果，返回 {ok, warnings, should_fallback}。

    - ok: 是否无明显问题
    - warnings: 告警列表
    - should_fallback: 若清洗严重丢失内容，是否应回退原文
    """
    warnings: list[str] = []

    original_clean = original or ""
    cleaned_clean = cleaned or ""

    # 1. 长度一致性
    orig_len = len(original_clean)
    clean_len = len(cleaned_clean)
    if orig_len > 0:
        ratio = clean_len / orig_len
        if ratio < _MIN_KEEP_RATIO:
            warnings.append(f"清洗后内容长度异常缩减（{ratio:.0%}），疑似丢失大量内容")
        elif ratio > _MAX_GROW_RATIO:
            warnings.append(f"清洗后内容长度异常膨胀（{ratio:.0%}），疑似额外生成")

    # 2. 数字一致性：原文有而清洗后缺失的数字
    orig_numbers = _extract_numbers(original_clean)
    clean_numbers = _extract_numbers(cleaned_clean)
    missing_numbers = orig_numbers - clean_numbers
    if missing_numbers:
        shown = ", ".join(sorted(missing_numbers)[:5])
        warnings.append(f"清洗后缺失原文数字：{shown}")

    # 3. 实体一致性：原文专有名词缺失
    orig_entities = _extract_entities(original_clean)
    clean_entities = _extract_entities(cleaned_clean)
    missing_entities = orig_entities - clean_entities
    if missing_entities:
        shown = ", ".join(sorted(missing_entities)[:5])
        warnings.append(f"清洗后缺失原文专有名词：{shown}")

    should_fallback = bool(orig_len > 0 and clean_len / orig_len < _MIN_KEEP_RATIO)
    return {
        "ok": len(warnings) == 0,
        "warnings": warnings,
        "should_fallback": should_fallback,
    }
