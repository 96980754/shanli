from __future__ import annotations

import asyncio
from typing import Any

import httpx
import json_repair

from yuxi.utils import logger

from yuxi.knowledge.graphs.extractors.base import normalize_extraction_result
from yuxi.knowledge.graphs.ontology import (
    compile_ontology_prompt,
    load_ontology,
    merge_ontology,
    normalize_ontology_aliases,
    parse_domain_extension,
    resolve_ontology_registry,
    validate_ontology_result,
)
from yuxi.models.chat import select_model

from .base import GraphExtractor

DEFAULT_TRIPLE_EXTRACTION_PROMPT = """请从下面文本中抽取实体和实体关系，返回严格 JSON，不要输出解释。
JSON 格式：
{
  "entities": [
    {"text": "实体文本", "label": "实体类型", "attributes": [{"text": "属性值", "label": "属性名称"}]}
  ],
  "relations": [
    {
      "source": {"text": "实体文本", "label": "实体类型", "attributes": [{"text": "属性值", "label": "属性名称"}]},
      "target": {"text": "实体文本", "label": "实体类型", "attributes": [{"text": "属性值", "label": "属性名称"}]},
      "text": "关系显示文本",
      "label": "关系类型",
      "polarity": "positive 或 negative",
      "assertion_kind": "fact 或 retraction",
      "evidence": {"quote": "支持该断言的原文短句"}
    }
  ]
}
要求：
- 明确区分肯定事实（positive）和否定事实（negative）。
- 只有原文明确出现“取消、不再、废止、撤回”等语义时，assertion_kind 才使用 retraction。
- 不得因为文本没有提到某项能力而推断 negative 或 retraction。
- evidence.quote 必须逐字引用当前文本中的最短完整证据句。
"""

SCHEMA_INSTRUCTION = """抽取 Schema 约束：
{schema}
"""

DEFAULT_CONCURRENCY_COUNT = 5
MAX_CONCURRENCY_COUNT = 20
MODEL_TIMEOUT_SECONDS = 180.0
MODEL_MAX_ATTEMPTS = 3


class OntologyIdentityMismatchError(ValueError):
    pass


class LLMGraphExtractor(GraphExtractor):
    extractor_type = "llm"

    def __init__(self, options: dict[str, Any] | None = None):
        super().__init__(options)
        self.ontology = None
        self.ontology_entry = None
        self.ontology_prompt = ""
        self._validated = False

    def validate_options(self) -> None:
        if self._validated:
            return
        if not self.options.get("model_spec"):
            raise ValueError("LLM 抽取器需要 model_spec")
        if self.options.get("prompt"):
            raise ValueError("LLM 图谱抽取器不支持自定义完整 Prompt，请使用 Ontology 或领域 Schema 配置")
        concurrency_count = self.options.get("concurrency_count", DEFAULT_CONCURRENCY_COUNT)
        try:
            concurrency_count = int(concurrency_count)
        except (TypeError, ValueError) as exc:
            raise ValueError("LLM 抽取器 concurrency_count 必须是整数") from exc
        if concurrency_count < 1 or concurrency_count > MAX_CONCURRENCY_COUNT:
            raise ValueError(f"LLM 抽取器 concurrency_count 必须在 1 到 {MAX_CONCURRENCY_COUNT} 之间")
        self.options["concurrency_count"] = concurrency_count
        if self.options.get("model_params") is not None and not isinstance(self.options["model_params"], dict):
            raise ValueError("LLM 抽取器 model_params 必须是对象")
        self._prepare_ontology()
        self._validated = True

    async def extract(self, text: str, *, chunk_metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        self.validate_options()
        model = select_model(
            model_spec=self.options["model_spec"],
            timeout=MODEL_TIMEOUT_SECONDS,
            model_params=self.options.get("model_params") or {},
        )
        chunk_id = str((chunk_metadata or {}).get("chunk_id") or "unknown")
        for attempt in range(1, MODEL_MAX_ATTEMPTS + 1):
            try:
                response = await model.call(self._build_messages(text), stream=False)
                result = json_repair.loads(response.content if response else "")
                return self._enrich_evidence(result, text)
            except Exception as exc:
                if attempt == MODEL_MAX_ATTEMPTS or not _is_retryable_model_error(exc):
                    raise
                delay = 2 ** (attempt - 1)
                logger.warning(
                    f"图谱模型调用失败，准备重试 chunk_id={chunk_id}, "
                    f"attempt={attempt}/{MODEL_MAX_ATTEMPTS}, delay={delay}s, "
                    f"error_type={_root_cause(exc).__class__.__name__}"
                )
                await asyncio.sleep(delay)
        raise RuntimeError("图谱模型调用重试状态异常")

    def normalize_result(self, result: dict[str, Any]) -> dict[str, Any]:
        normalized = normalize_extraction_result(result, self.extractor_type)
        if self.ontology is None:
            return normalized

        metadata = normalized["metadata"]
        configured_identity = (
            self.ontology.registry_id,
            self.ontology.version,
            self.ontology_entry.digest,
        )
        cached_identity = (
            str(metadata.get("ontology_registry_id") or ""),
            str(metadata.get("ontology_version") or ""),
            str(metadata.get("ontology_digest") or ""),
        )
        if any(cached_identity) and cached_identity != configured_identity:
            raise OntologyIdentityMismatchError("抽取结果使用了不同的 Core Ontology，请先清空抽取结果后重试")

        normalize_ontology_aliases(normalized, self.ontology)
        validate_ontology_result(normalized, self.ontology)
        metadata.update(
            {
                "ontology_registry_id": self.ontology.registry_id,
                "ontology_version": self.ontology.version,
                "ontology_digest": self.ontology_entry.digest,
            }
        )
        return normalized

    def _enrich_evidence(self, result: Any, source_text: str) -> Any:
        if not isinstance(result, dict):
            return result

        metadata = result.setdefault("metadata", {})
        if isinstance(metadata, dict):
            metadata["schema_version"] = 2

        relations = result.get("relations")
        if not isinstance(relations, list):
            return result

        for relation in relations:
            if not isinstance(relation, dict):
                continue
            evidence = relation.get("evidence")
            if not isinstance(evidence, dict):
                continue
            quote = str(evidence.get("quote") or "").strip()
            start_char = source_text.find(quote) if quote else -1
            evidence["start_char"] = start_char if start_char >= 0 else None
            evidence["end_char"] = start_char + len(quote) if start_char >= 0 else None
        return result

    def ontology_summary(self) -> dict[str, Any] | None:
        if self.ontology_entry is None:
            return None
        return self.ontology_entry.public_dict()

    def _prepare_ontology(self) -> None:
        registry_id = str(self.options.get("ontology_registry_id") or "").strip()
        if not registry_id:
            self.ontology = None
            self.ontology_entry = None
            self.ontology_prompt = ""
            return

        expected_version = str(self.options.get("ontology_version") or "").strip()
        if not expected_version:
            raise ValueError("Ontology 模式需要 ontology_version")
        expected_digest = str(self.options.get("ontology_digest") or "").strip() or None
        self.ontology_entry = resolve_ontology_registry(registry_id, expected_version, expected_digest)
        core = load_ontology(
            self.ontology_entry.registry_id,
            self.ontology_entry.version,
            self.ontology_entry.digest,
        )

        extension = parse_domain_extension(self.options.get("domain_schema"))
        self.ontology = merge_ontology(core, extension)
        if not self.ontology.entities:
            raise ValueError("Ontology 没有可用实体类型，请配置领域 Ontology 扩展")
        self.ontology_prompt = compile_ontology_prompt(self.ontology)

    def _build_messages(self, text: str) -> list[dict[str, str]]:
        return [
            {"role": "system", "content": self._build_system_prompt()},
            {"role": "user", "content": f"文本：\n{text}"},
        ]

    def _build_system_prompt(self) -> str:
        if self.ontology is not None:
            return f"{DEFAULT_TRIPLE_EXTRACTION_PROMPT}\n{self.ontology_prompt}"

        extraction_prompt = DEFAULT_TRIPLE_EXTRACTION_PROMPT
        schema = str(self.options.get("schema") or "").strip()
        if schema:
            extraction_prompt = f"{extraction_prompt}\n{SCHEMA_INSTRUCTION.format(schema=schema)}"
        return extraction_prompt

    def _build_prompt(self, text: str) -> str:
        return f"{self._build_system_prompt()}\n\n文本：\n{text}"


def _root_cause(exc: BaseException) -> BaseException:
    current = exc
    seen: set[int] = set()
    while current.__cause__ is not None and id(current.__cause__) not in seen:
        seen.add(id(current))
        current = current.__cause__
    return current


def _is_retryable_model_error(exc: BaseException) -> bool:
    cause = _root_cause(exc)
    if isinstance(cause, (TimeoutError, ConnectionError, httpx.TimeoutException, httpx.NetworkError)):
        return True
    if isinstance(cause, httpx.HTTPStatusError):
        return cause.response.status_code == 429 or cause.response.status_code >= 500
    status_code = getattr(cause, "status_code", None)
    return status_code == 429 or isinstance(status_code, int) and status_code >= 500
