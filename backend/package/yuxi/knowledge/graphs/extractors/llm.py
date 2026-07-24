from __future__ import annotations

from typing import Any

import json_repair

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
      "label": "关系类型"
    }
  ]
}
"""

SCHEMA_INSTRUCTION = """抽取 Schema 约束：
{schema}
"""


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
        concurrency_count = self.options.get("concurrency_count", 1)
        try:
            concurrency_count = int(concurrency_count)
        except (TypeError, ValueError) as exc:
            raise ValueError("LLM 抽取器 concurrency_count 必须是整数") from exc
        if concurrency_count < 1 or concurrency_count > 1000:
            raise ValueError("LLM 抽取器 concurrency_count 必须在 1 到 1000 之间")
        if self.options.get("model_params") is not None and not isinstance(self.options["model_params"], dict):
            raise ValueError("LLM 抽取器 model_params 必须是对象")
        self._prepare_ontology()
        self._validated = True

    async def extract(self, text: str, *, chunk_metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        del chunk_metadata
        self.validate_options()
        model = select_model(
            model_spec=self.options["model_spec"],
            timeout=60.0,
            model_params=self.options.get("model_params") or {},
        )
        response = await model.call(self._build_messages(text), stream=False)
        return json_repair.loads(response.content if response else "")

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
            raise ValueError("抽取结果使用了不同的 Core Ontology，请先清空抽取结果后重试")

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
