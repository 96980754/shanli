from __future__ import annotations

import asyncio
from typing import Any

import httpx
import json_repair

from yuxi.knowledge.graphs.extractors.base import normalize_extraction_result
from yuxi.knowledge.graphs.graph_utils import locate_evidence_quote
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
from yuxi.utils import logger

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

# 领域无关的文档级主实体扫描：不预设领域，LLM 依据文档内容自行判断
# 文档围绕哪些核心实体展开（产品、人物、项目、概念等均可）。
DOCUMENT_ENTITY_EXTRACTION_PROMPT = """请通读以下文档，识别这份文档主要围绕哪些核心实体展开讨论。
这些实体可以是产品、设备型号、人物、组织、项目、概念、地区等任何类型，不依赖预设领域。

返回严格 JSON，不要输出解释：
{
  "main_entities": [
    {"name": "实体的规范全名", "label": "实体类型", "reason": "一句话说明该实体为何是文档主实体"}
  ]
}

要求：
- 只列出文档真正围绕其展开讨论的核心实体，通常 1~5 个；仅顺带提及的不要列出。
- 每个实体只列一次，name 使用文档中最正式、最完整的叫法。
- label 使用通用实体类型（如 Product、Person、Organization、Project、Concept）。
"""

DEFAULT_CONCURRENCY_COUNT = 5
MAX_CONCURRENCY_COUNT = 20
MODEL_TIMEOUT_SECONDS = 180.0
MODEL_MAX_ATTEMPTS = 3
MAX_DOCUMENT_SCAN_CHARS = 20000


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
        chunk_id = str((chunk_metadata or {}).get("chunk_id") or "unknown")
        document_entities = (chunk_metadata or {}).get("document_entities") or []
        messages = self._build_messages(text, document_entities=document_entities)
        response = await self._call_model_with_retry(
            messages,
            label=f"图谱模型调用失败 chunk_id={chunk_id}",
        )
        result = json_repair.loads(response.content if response else "")
        return self._enrich_evidence(result, text)

    async def extract_document_entities(self, text: str) -> list[dict[str, str]]:
        """LLM 扫描整篇文档，识别文档级主实体（领域无关）。

        Ontology 模式下仅保留 label 属于 Ontology 实体类型的主实体，
        避免注入与 Ontology 无关的实体名导致分块抽取校验失败。
        """
        self.validate_options()
        document_text = (text or "")[:MAX_DOCUMENT_SCAN_CHARS]
        if not document_text.strip():
            return []
        response = await self._call_model_with_retry(
            self._build_document_scan_messages(document_text),
            label="文档级主实体扫描",
        )
        result = json_repair.loads(response.content if response else "")
        return self._normalize_document_entities(result)

    async def _call_model_with_retry(self, messages: list[dict[str, str]], *, label: str) -> Any:
        """带指数退避重试的模型调用，供分块抽取与文档级主实体扫描复用。"""
        model = select_model(
            model_spec=self.options["model_spec"],
            timeout=MODEL_TIMEOUT_SECONDS,
            model_params=self.options.get("model_params") or {},
        )
        for attempt in range(1, MODEL_MAX_ATTEMPTS + 1):
            try:
                return await model.call(messages, stream=False)
            except Exception as exc:
                if attempt == MODEL_MAX_ATTEMPTS or not _is_retryable_model_error(exc):
                    raise
                delay = 2 ** (attempt - 1)
                logger.warning(
                    f"{label}, attempt={attempt}/{MODEL_MAX_ATTEMPTS}, delay={delay}s, "
                    f"error_type={_root_cause(exc).__class__.__name__}"
                )
                await asyncio.sleep(delay)
        raise RuntimeError("模型调用重试状态异常")

    def normalize_result(self, result: dict[str, Any]) -> dict[str, Any]:
        # 先做 ontology 别名归一化，再做实体去重：
        # 同一实体在文档里可能被 LLM 抽成不同叫法（如"对讲机"与"Motorola 对讲机"），
        # 若先去重（key 基于原始 text）会生成多个节点，别名归一需在去重前执行。
        if self.ontology is not None:
            normalize_ontology_aliases(result, self.ontology)
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
            located = locate_evidence_quote(source_text, quote) if quote else None
            evidence["start_char"] = located[0] if located else None
            evidence["end_char"] = located[1] if located else None
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

    def _build_messages(
        self,
        text: str,
        *,
        document_entities: list[dict[str, str]] | None = None,
    ) -> list[dict[str, str]]:
        content = f"文本：\n{text}"
        document_context = self._build_document_context_section(document_entities)
        if document_context:
            content = f"{document_context}\n\n{content}"
        return [
            {"role": "system", "content": self._build_system_prompt()},
            {"role": "user", "content": content},
        ]

    def _build_document_scan_messages(self, text: str) -> list[dict[str, str]]:
        system_prompt = DOCUMENT_ENTITY_EXTRACTION_PROMPT
        if self.ontology is not None:
            allowed_labels = "、".join(self.ontology.entities)
            system_prompt = (
                f"{system_prompt}\n本知识库已配置 Ontology，实体类型 label 必须从以下类型中选择：{allowed_labels}。"
            )
        return [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"文档内容（若超长仅展示开头部分）：\n{text}"},
        ]

    def _normalize_document_entities(self, result: Any) -> list[dict[str, str]]:
        if not isinstance(result, dict):
            raise ValueError("文档级主实体扫描结果必须是对象")
        main_entities = result.get("main_entities")
        if not isinstance(main_entities, list):
            raise ValueError("main_entities 必须是数组")

        allowed_labels = set(self.ontology.entities) if self.ontology is not None else None
        normalized: list[dict[str, str]] = []
        seen_names: set[str] = set()
        for item in main_entities:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if not name or name in seen_names:
                continue
            label = str(item.get("label") or "").strip() or None
            if allowed_labels is not None and (label is None or label not in allowed_labels):
                continue
            seen_names.add(name)
            normalized.append({"name": name, "label": label})
        return normalized

    @staticmethod
    def _build_document_context_section(document_entities: list[dict[str, str]] | None) -> str:
        names = [item["name"] for item in document_entities or [] if isinstance(item, dict) and item.get("name")]
        if not names:
            return ""
        lines = "\n".join(f"- {name}" for name in names)
        return (
            "文档级主实体（这份文档主要讨论的对象；抽取时若某个实体与下列主实体是同一对象，"
            f"请统一使用下列规范名，而不是局部叫法）：\n{lines}"
        )

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
