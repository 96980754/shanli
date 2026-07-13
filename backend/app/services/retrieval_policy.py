from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


_PRODUCT_ALIASES = {
    "MC": ("mcstars", "mc stars"),
    "MS": ("miniserver", "mini server"),
    "MD": ("mdm",),
    "MNO": ("pocstars-mno",),
    "PRO": ("pocstars-pro",),
    "UC": ("pocstars-uc",),
    "LOC": ("定位",),
}


@dataclass(frozen=True)
class TopKPolicy:
    initial: int
    after_rerank: int
    final: int


@dataclass(frozen=True)
class RetrievalPolicy:
    type_weights: dict[str, float]
    product_weights: dict[str, float]
    priority_boosts: dict[str, float]
    similarity_ratio: float
    type_ratio: float
    product_ratio: float
    priority_ratio: float
    top_k: TopKPolicy

    @classmethod
    def load(cls, path: Path) -> "RetrievalPolicy":
        try:
            payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise ValueError(f"Unable to read retrieval policy: {path}") from exc
        if not isinstance(payload, dict):
            raise ValueError("Retrieval policy must be a mapping")

        type_weights = cls._weight_mapping(payload, "type_weight")
        product_weights = cls._weight_mapping(payload, "product_weight")
        priority_boosts = cls._weight_mapping(payload, "priority_boost")
        formula = payload.get("formula")
        top_k = payload.get("top_k")
        if not isinstance(formula, dict) or not isinstance(top_k, dict):
            raise ValueError("Retrieval policy requires formula and top_k")

        try:
            ratios = [
                float(formula["similarity_ratio"]),
                float(formula["type_ratio"]),
                float(formula["product_ratio"]),
                float(formula["priority_ratio"]),
            ]
            top_k_policy = TopKPolicy(
                initial=int(top_k["initial"]),
                after_rerank=int(top_k["after_rerank"]),
                final=int(top_k["final"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Retrieval policy has missing or invalid formula/top_k values") from exc

        if any(ratio < 0 for ratio in ratios) or abs(sum(ratios) - 1.0) > 1e-9:
            raise ValueError("Retrieval policy formula ratios must be non-negative and sum to 1.0")
        if min(top_k_policy.initial, top_k_policy.after_rerank, top_k_policy.final) <= 0:
            raise ValueError("Retrieval policy top_k values must be positive")
        if top_k_policy.final > top_k_policy.after_rerank or top_k_policy.after_rerank > top_k_policy.initial:
            raise ValueError("Retrieval policy top_k values must be descending")

        return cls(
            type_weights=type_weights,
            product_weights=product_weights,
            priority_boosts=priority_boosts,
            similarity_ratio=ratios[0],
            type_ratio=ratios[1],
            product_ratio=ratios[2],
            priority_ratio=ratios[3],
            top_k=top_k_policy,
        )

    @staticmethod
    def _weight_mapping(payload: dict[str, Any], key: str) -> dict[str, float]:
        source = payload.get(key)
        if not isinstance(source, dict):
            raise ValueError(f"Retrieval policy requires {key}")
        try:
            result = {str(name): float(value) for name, value in source.items()}
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Retrieval policy {key} values must be numeric") from exc
        if any(value < 0 for value in result.values()):
            raise ValueError(f"Retrieval policy {key} values must be non-negative")
        return result

    def type_weight(self, document_type: str) -> float:
        return self.type_weights.get(document_type, self.type_weights.get("OTH", 0.0))

    def product_weight(self, product: str) -> float:
        return self.product_weights.get(product, self.product_weights.get("GEN", 0.0))

    def priority_boost(self, priority: str) -> float:
        return self.priority_boosts.get(priority, self.priority_boosts.get("P2", 0.0))

    def detect_products(self, question: str) -> set[str]:
        normalized = question.lower()
        return {
            product
            for product, aliases in _PRODUCT_ALIASES.items()
            if any(alias in normalized for alias in aliases)
        }

    def rerank(
        self,
        chunks: list[dict[str, Any]],
        matched_products: set[str],
    ) -> list[dict[str, Any]]:
        scores = [max(float(chunk.get("score", 0.0)), 0.0) for chunk in chunks]
        max_score = max(scores, default=0.0)
        normalized_scores = [score / max_score if max_score > 0 else 1.0 for score in scores]
        ranked: list[dict[str, Any]] = []

        for chunk, normalized_score in zip(chunks, normalized_scores):
            document_type = str(chunk.get("document_type", "OTH"))
            product = str(chunk.get("product", "GEN"))
            priority = str(chunk.get("priority", "P2"))
            product_component = self.product_weight(product) if product in matched_products else 0.0
            metadata_score = (
                self.similarity_ratio * normalized_score
                + self.type_ratio * self.type_weight(document_type)
                + self.product_ratio * product_component
                + self.priority_ratio * self.priority_boost(priority)
            )
            item = dict(chunk)
            item["normalized_similarity"] = normalized_score
            item["metadata_score"] = metadata_score
            ranked.append(item)

        return sorted(ranked, key=lambda item: item["metadata_score"], reverse=True)
