from __future__ import annotations

import re
from typing import Any


class RuleReranker:
    """Rule-based reranker for phase-1 retrieval results."""

    def rerank(
        self,
        question: str,
        chunks: list[dict[str, Any]],
        top_k: int = 10,
    ) -> list[dict[str, Any]]:
        query_tokens = set(self._tokenize(question))
        ranked: list[dict[str, Any]] = []

        for chunk in chunks:
            content = str(chunk.get("content", ""))
            heading = str(chunk.get("heading", ""))
            content_tokens = set(self._tokenize(content))
            heading_tokens = set(self._tokenize(heading))

            overlap = len(query_tokens & content_tokens) / max(len(query_tokens), 1)
            heading_overlap = len(query_tokens & heading_tokens) / max(len(query_tokens), 1)
            base_score = float(chunk.get("score", 0.0))
            chunk_index = int(chunk.get("chunk_index", 0))
            position_boost = 1.0 + max(0.0, 1.0 - chunk_index / 100.0) * 0.1

            item = dict(chunk)
            item["rerank_score"] = (
                base_score * 0.5 + overlap * 0.35 + heading_overlap * 0.15
            ) * position_boost
            ranked.append(item)

        ranked.sort(key=lambda item: item["rerank_score"], reverse=True)
        return ranked[:top_k]

    def _tokenize(self, text: str) -> list[str]:
        normalized = text.lower()
        words = re.findall(r"[a-z0-9]+", normalized)
        chinese_chars = re.findall(r"[一-鿿]", normalized)
        return words + chinese_chars
