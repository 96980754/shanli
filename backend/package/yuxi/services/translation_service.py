"""多语言边界翻译：中文内部规范语 + 边界进出翻译。

运行约定：知识库内容、跑题门关键词、拒答前缀分类、judge 提示词、无依据兜底与《》来源匹配
全部以中文为内部规范语。因此这里只提供两条边界能力，内部机制零改动：

- 入口（translate_to_chinese）：把非中文问题归一成简体中文，供判定链与检索消费；
- 出口（translate_from_chinese）：把最终可见的中文回复翻译回提问语言（id/ms/en/…），
  中文原文仍留在消息 extra_metadata 中，便于审计与统计。

翻译复用 REFUSAL_JUDGE_MODEL（可用 TRANSLATION_MODEL 覆盖）的 LLM 调用完成，一次调用
同时做语言检测与中译；任何失败都回退原文，绝不抛错中断对话。测试可通过 caller 注入模型。
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from yuxi.models import select_model
from yuxi.utils.logging_config import logger

# 出口翻译模型：TRANSLATION_MODEL 优先，缺省退回拒答 judge 用的快模型；两者均未配置则跳过翻译。
TRANSLATION_MODEL = os.getenv("TRANSLATION_MODEL", "").strip() or os.getenv("REFUSAL_JUDGE_MODEL", "").strip()

# 出口翻译逐段上限：超过则按行分组、逐段翻译，避免单次输出把长回答腰斩。
_OUTBOUND_CHUNK_CHARS = 2200
# 出口翻译总源文本上限（防御超长回答；超过则保留中文并告警）。
_OUTBOUND_MAX_TOTAL_CHARS = 30000

# 目标语言在提示词里的人类可读标签；未登记的 ISO 码直接用码本身。
_LANG_LABELS = {
    "id": "印尼语（Bahasa Indonesia）",
    "ms": "马来语（Bahasa Melayu）",
    "en": "英文（English）",
    "vi": "越南语（Tiếng Việt）",
    "th": "泰语（ภาษาไทย）",
}

_LANG_ALIASES = {
    "zh-cn": "zh",
    "zh_cn": "zh",
    "chinese": "zh",
    "mandarin": "zh",
    "zhs": "zh",
    "indonesian": "id",
    "bahasa-malaysia": "ms",
    "bahasa-melayu": "ms",
    "malay": "ms",
    "english": "en",
    "vietnamese": "vi",
    "thai": "th",
}

# 翻进/翻出都要原样保留的专名片段：产品型号、URL、数字版本号、《》文档名、Markdown 结构与代码块。
_SPAN_KEEP = (
    "翻译时严格原样保留以下内容，不要改写：产品型号/编号（如 cat1、mcx、f10、POCSTARS）、"
    "URL 与网址、阿拉伯数字与版本号、《》书名号中的文档名、Markdown 表格与代码块结构。"
    "只翻译自然语言正文。"
)

_CJK_RE = re.compile(r"[一-鿿㐀-䶿]")

ModelCaller = Callable[[list[dict[str, Any]]], Awaitable[str | None]]


def is_chinese_text(text: str) -> bool:
    """含 CJK 汉字（含中英混合技术提问）即视为中文，免入口翻译。"""
    return bool(_CJK_RE.search(text or ""))


def normalize_lang(code: str) -> str:
    """把模型给的语言码归一为小写 ISO-639-1；未知别名原样返回小写主码。"""
    raw = (code or "").strip().lower().replace("_", "-")
    if not raw:
        return ""
    if raw in _LANG_ALIASES:
        return _LANG_ALIASES[raw]
    return raw.split("-")[0]


@dataclass(frozen=True)
class InboundTranslation:
    """入口翻译结果：检测到的源语言 + 归一后的中文 query。"""

    source_lang: str
    chinese: str


def _extract_json_object(raw: str) -> dict[str, Any] | None:
    """稳健提取模型输出中的 JSON 对象（容忍代码块围栏/前后缀噪声）。"""
    content = (raw or "").strip()
    if content.startswith("```"):
        content = re.sub(r"^```[a-zA-Z]*\s*", "", content)
        content = re.sub(r"\s*```$", "", content).strip()
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        start, end = content.find("{"), content.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            payload = json.loads(content[start : end + 1])
        except json.JSONDecodeError:
            return None
    return payload if isinstance(payload, dict) else None


async def _call_model(messages: list[dict[str, Any]], *, caller: ModelCaller | None = None) -> str | None:
    """走配置模型（或测试注入的 caller）拿文本；失败返回 None。"""
    if caller is not None:
        return await caller(messages)
    try:
        adapter = select_model(TRANSLATION_MODEL)
        response = await adapter.call(messages)
        return str(getattr(response, "content", "") or "").strip() or None
    except Exception as exc:  # noqa: BLE001 — 翻译失败按原文处理，不阻断对话
        logger.warning("多语言翻译调用失败，按原文处理: %s", exc)
        return None


async def translate_to_chinese(
    text: str,
    *,
    caller: ModelCaller | None = None,
) -> InboundTranslation | None:
    """入口归一：把非中文问题一次性完成「语言检测 + 简体中文翻译」。

    返回 None 表示无需/无法翻译（中文输入、模型未配置、输出不可解析），调用方沿用原文。
    """
    original = (text or "").strip()
    if not original or is_chinese_text(original):
        return None
    if caller is None and not TRANSLATION_MODEL:
        return None

    messages = [
        {
            "role": "system",
            "content": (
                "你是多语种问答入口的语言检测与翻译工具。判断用户文本的主要语言，并完整翻译成简体中文。"
                "只输出一个 JSON 对象，不要多余文字或代码块标记：\n"
                '{"source_lang": "<ISO 639-1 语言码，如 zh/en/id/ms>", "chinese": "<完整简体中文译文>"}\n'
                "若输入已经是中文，source_lang 填 zh，chinese 填原文。\n"
                + _SPAN_KEEP
            ),
        },
        {"role": "user", "content": f"需要检测并翻译的文本：\n{original}"},
    ]
    raw = await _call_model(messages, caller=caller)
    payload = _extract_json_object(str(raw or ""))
    if not isinstance(payload, dict):
        return None
    source_lang = normalize_lang(str(payload.get("source_lang") or ""))
    chinese = str(payload.get("chinese") or "").strip()
    if source_lang in {"", "zh"} or not chinese:
        return None
    return InboundTranslation(source_lang=source_lang, chinese=chinese)


def _split_for_translation(text: str, *, max_chunk: int = _OUTBOUND_CHUNK_CHARS) -> list[str]:
    """把出口文本按行聚成不超过 max_chunk 的段，逐段翻译再拼接，保 Markdown 行结构。"""
    chunks: list[str] = []
    current = ""
    for line in text.splitlines(keepends=True):
        # 单行本身超上限：先清空当前段，再按硬长度切行；切剩的残行续进 current 与后续行聚段。
        if len(line) > max_chunk:
            if current:
                chunks.append(current)
                current = ""
            while len(line) > max_chunk:
                chunks.append(line[:max_chunk])
                line = line[max_chunk:]
        # 行放不进当前段：先落盘当前段，再把该行开新段（避免行被吞）。
        elif current and len(current) + len(line) > max_chunk:
            chunks.append(current)
            current = ""
        current += line
    if current:
        chunks.append(current)
    return chunks


def _outbound_messages(chunk: str, target: str) -> list[dict[str, str]]:
    label = _LANG_LABELS.get(target, target)
    return [
        {
            "role": "system",
            "content": (
                f"你是企业客服回复的翻译器。把用户提供的中文回复完整、自然地翻译成{label}。"
                f"保留原文的 Markdown 结构与换行。{_SPAN_KEEP}只输出译文本身，不要解释或补充。"
            ),
        },
        {"role": "user", "content": f"中文回复：\n{chunk}"},
    ]


async def translate_from_chinese(
    text: str,
    target_lang: str,
    *,
    caller: ModelCaller | None = None,
) -> str | None:
    """出口本地化：把中文回复翻译回提问语言。

    target_lang 为空/zh、文本为空或翻译不可用时原样返回输入；任一段翻译失败则整体放弃
    （返回 None，调用方保留中文），避免半翻半不翻。
    """
    content = (text or "").strip()
    target = normalize_lang(target_lang)
    if not content or target in {"", "zh"}:
        return content
    if caller is None and not TRANSLATION_MODEL:
        return content
    if len(content) > _OUTBOUND_MAX_TOTAL_CHARS:
        logger.warning("出口翻译源文本超过上限(%s 字符)，保留中文回复", _OUTBOUND_MAX_TOTAL_CHARS)
        return content

    pieces: list[str] = []
    for chunk in _split_for_translation(content):
        piece = await _call_model(_outbound_messages(chunk, target), caller=caller)
        if not piece or not piece.strip():
            logger.warning("出口翻译某段失败，放弃整段翻译，保留中文回复")
            return content
        pieces.append(piece.strip())
    return "\n".join(pieces)
