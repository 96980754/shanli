"""Validation and provider-backed generation for document QA pairs."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import unicodedata
from typing import Any

from yuxi.models import select_model

QA_GENERATOR_VERSION = "1.0"
_SPACE_RE = re.compile(r"\s+")
_QUESTION_PUNCTUATION_RE = re.compile(r"[?？]+$")
_URL_RE = re.compile(r"https?://[^\s)>]+")
_NUMBER_RE = re.compile(r"\b\d+(?:[.,/_-]\d+)*\b")
_MODEL_RE = re.compile(r"\b[A-Z][A-Z0-9]*(?:[-_/][A-Za-z0-9.]+)+\b")


class QAValidationError(ValueError):
    """Raised when a QA pair cannot be grounded in its source chunks."""


class QAProviderUnavailable(RuntimeError):
    """Raised when document QA generation has no configured provider."""


def normalize_question(question: str) -> str:
    normalized = _SPACE_RE.sub(" ", unicodedata.normalize("NFKC", str(question))).strip().casefold()
    return _QUESTION_PUNCTUATION_RE.sub("", normalized).strip()


def question_hash(question: str) -> str:
    return hashlib.sha256(normalize_question(question).encode("utf-8")).hexdigest()


def _assert_fact_subset(answer: str, evidence: str) -> None:
    checks = (
        (_MODEL_RE, "答案包含证据中不存在的型号或版本"),
        (_URL_RE, "答案包含证据中不存在的链接"),
        (_NUMBER_RE, "答案包含证据中不存在的数字"),
    )
    normalized_evidence = unicodedata.normalize("NFKC", evidence).casefold()
    for pattern, message in checks:
        missing = {
            value
            for value in pattern.findall(unicodedata.normalize("NFKC", answer))
            if value.casefold() not in normalized_evidence
        }
        if missing:
            raise QAValidationError(message)


def normalize_and_validate_qa(
    payload: dict[str, Any],
    source_chunks: dict[str, str],
    *,
    question_max_chars: int = 300,
    answer_max_chars: int = 2000,
) -> dict[str, Any]:
    question = _SPACE_RE.sub(" ", str(payload.get("question") or "")).strip()
    answer = str(payload.get("answer") or "").strip()
    if not question or len(question) > max(1, int(question_max_chars)):
        raise QAValidationError("问题为空或超过长度限制")
    if not answer or len(answer) > max(1, int(answer_max_chars)):
        raise QAValidationError("答案为空或超过长度限制")
    if normalize_question(answer) in {"是", "否", "yes", "no"}:
        raise QAValidationError("答案不能只有是或否")

    source_chunk_ids = list(dict.fromkeys(str(value) for value in payload.get("source_chunk_ids") or [] if value))
    if not source_chunk_ids or any(chunk_id not in source_chunks for chunk_id in source_chunk_ids):
        raise QAValidationError("QA 来源 chunk 无效")

    normalized_evidence: list[dict[str, str]] = []
    for item in payload.get("evidence") or []:
        if not isinstance(item, dict):
            continue
        chunk_id = str(item.get("chunk_id") or "")
        text = str(item.get("text") or "").strip()
        chunk_content = source_chunks.get(chunk_id)
        if chunk_id not in source_chunk_ids or not text or chunk_content is None or text not in chunk_content:
            raise QAValidationError("QA 证据不属于绑定的来源 chunk")
        normalized_evidence.append({"chunk_id": chunk_id, "text": text})
    if not normalized_evidence:
        raise QAValidationError("QA 证据不能为空")

    evidence_text = "\n".join(item["text"] for item in normalized_evidence)
    _assert_fact_subset(answer, evidence_text)
    return {
        "question": question,
        "question_hash": question_hash(question),
        "answer": answer,
        "source_chunk_ids": source_chunk_ids,
        "evidence": normalized_evidence,
    }


def _extract_json(value: str) -> list[dict[str, Any]]:
    text = value.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise QAValidationError("模型输出不是有效 JSON") from exc
    if isinstance(payload, dict):
        payload = payload.get("qas")
    if not isinstance(payload, list):
        raise QAValidationError("模型输出必须包含 qas 数组")
    return [item for item in payload if isinstance(item, dict)]


class DocumentQAGenerator:
    """Generate QA drafts through the existing configurable model boundary."""

    async def generate(
        self,
        chunks: list[Any],
        *,
        model_spec: str | None,
        temperature: float,
        timeout_seconds: float,
        attempts: int,
        max_pairs: int,
        question_max_chars: int,
        answer_max_chars: int,
    ) -> list[dict[str, Any]]:
        if not model_spec:
            raise QAProviderUnavailable("文档 QA 生成模型未配置")
        source_chunks = {str(chunk.chunk_id): str(chunk.content) for chunk in chunks}
        if not source_chunks:
            raise QAValidationError("文档没有可用于生成 QA 的正式 chunks")

        model = select_model(model_spec=model_spec, temperature=temperature)
        prompt = [
            {
                "role": "system",
                "content": (
                    "只依据给定 chunks 生成可独立理解的问答对，不得扩写、推断或加入新事实。"
                    '返回 JSON 对象 {"qas": [...]}。每项必须包含 question、answer、'
                    "source_chunk_ids 和 evidence；evidence 是含 chunk_id、text 的数组，"
                    "text 必须逐字取自对应 chunk。不要输出推理过程。"
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    [{"chunk_id": chunk_id, "content": content} for chunk_id, content in source_chunks.items()],
                    ensure_ascii=False,
                ),
            },
        ]
        last_error: Exception | None = None
        raw_items: list[dict[str, Any]] | None = None
        for attempt in range(max(1, min(int(attempts), 2))):
            try:
                response = await asyncio.wait_for(
                    model.model.ainvoke(prompt),
                    timeout=max(1.0, float(timeout_seconds)),
                )
                content = getattr(response, "text", None) or getattr(response, "content", None) or ""
                raw_items = _extract_json(str(content))
                break
            except Exception as exc:  # noqa: BLE001 - bounded structured-output repair
                last_error = exc
                if attempt == 0:
                    prompt.append({"role": "system", "content": "上次输出无效，请仅返回约定的 JSON 对象。"})
        if raw_items is None:
            raise QAValidationError("模型未返回有效的 QA 结构") from last_error

        results: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in raw_items:
            validated = normalize_and_validate_qa(
                item,
                source_chunks,
                question_max_chars=question_max_chars,
                answer_max_chars=answer_max_chars,
            )
            if validated["question_hash"] in seen:
                continue
            seen.add(validated["question_hash"])
            results.append(
                {
                    **validated,
                    "model_name": str(getattr(model, "model_name", "") or model_spec),
                    "model_version": QA_GENERATOR_VERSION,
                }
            )
            if len(results) >= max(1, int(max_pairs)):
                break
        if not results:
            raise QAValidationError("模型没有生成可验证的 QA")
        return results


__all__ = [
    "DocumentQAGenerator",
    "QAProviderUnavailable",
    "QAValidationError",
    "normalize_and_validate_qa",
    "normalize_question",
    "question_hash",
]
