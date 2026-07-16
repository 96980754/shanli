from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from app.services.reranker import RuleReranker
from app.services.retrieval_policy import RetrievalPolicy


class KnowledgeTools:
    """Pure function style tools registered for LLM tool use."""

    def __init__(self) -> None:
        self.vector_chunks_by_kb: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self._bm25_chunks_by_kb: dict[str, list[dict[str, Any]]] = {}
        self._bm25_doc_freqs_by_kb: dict[str, Counter[str]] = {}
        self._bm25_avg_len_by_kb: dict[str, float] = {}
        self.reranker = RuleReranker()
        self._retrieval_policy_path: Path | None = None

    def set_retrieval_policy_path(self, path: Path | None) -> None:
        self._retrieval_policy_path = path

    def _retrieval_policy(self) -> RetrievalPolicy | None:
        if self._retrieval_policy_path is None:
            return None
        return RetrievalPolicy.load(self._retrieval_policy_path)

    def _apply_retrieval_policy(
        self,
        query: str,
        chunks: list[dict[str, Any]],
        top_k: int,
    ) -> list[dict[str, Any]]:
        policy = self._retrieval_policy()
        if policy is None:
            return chunks[:top_k]
        initial_candidates = chunks[: policy.top_k.initial]
        ranked = policy.rerank(initial_candidates, policy.detect_products(query))
        reranked_candidates = ranked[: policy.top_k.after_rerank]
        return reranked_candidates[: min(top_k, policy.top_k.final)]

    def build_bm25_index(self, kb_id: str, chunks: list[dict[str, Any]]) -> None:
        self._bm25_chunks_by_kb[kb_id] = list(chunks)
        doc_freqs: Counter[str] = Counter()
        lengths = []
        for chunk in chunks:
            tokens = set(self._tokenize(str(chunk.get("content", ""))))
            lengths.append(len(tokens))
            doc_freqs.update(tokens)
        self._bm25_doc_freqs_by_kb[kb_id] = doc_freqs
        self._bm25_avg_len_by_kb[kb_id] = sum(lengths) / max(len(lengths), 1)

    def select_embedding_model(self, query: str) -> str:
        ascii_letters = sum(1 for char in query if char.isascii() and char.isalpha())
        ratio = ascii_letters / max(len(query), 1)
        if ratio > 0.3:
            return "BAAI/m3e-base"
        return "BAAI/bge-large-zh-v1.5"

    async def retrieve(self, query: str, kb_id: str, top_k: int = 30) -> list[dict[str, Any]]:
        self.current_embedding_model = self.select_embedding_model(query)
        chunks = self.vector_chunks_by_kb.get(kb_id, [])
        query_tokens = set(self._tokenize(query))
        results = []
        for chunk in chunks:
            content_tokens = set(self._tokenize(str(chunk.get("content", ""))))
            overlap = len(query_tokens & content_tokens) / max(len(query_tokens), 1)
            item = dict(chunk)
            item["score"] = max(float(item.get("score", 0.0)), overlap)
            results.append(item)
        results.sort(key=lambda item: item.get("score", 0.0), reverse=True)
        return self._apply_retrieval_policy(query, results, top_k)

    async def bm25_search(self, query: str, kb_id: str, top_k: int = 20) -> list[dict[str, Any]]:
        chunks = self._bm25_chunks_by_kb.get(kb_id, [])
        if not chunks:
            return []

        query_terms = self._tokenize(query)
        doc_freqs = self._bm25_doc_freqs_by_kb[kb_id]
        avg_len = self._bm25_avg_len_by_kb[kb_id]
        total_docs = len(chunks)
        k1 = 1.5
        b = 0.75
        scored = []

        for chunk in chunks:
            tokens = self._tokenize(str(chunk.get("content", "")))
            term_counts = Counter(tokens)
            doc_len = len(tokens) or 1
            score = 0.0
            for term in query_terms:
                tf = term_counts[term]
                if tf == 0:
                    continue
                df = doc_freqs.get(term, 0)
                idf = math.log(1 + (total_docs - df + 0.5) / (df + 0.5))
                denom = tf + k1 * (1 - b + b * doc_len / max(avg_len, 1))
                score += idf * (tf * (k1 + 1)) / denom
            if score > 0:
                item = dict(chunk)
                item["score"] = score
                scored.append(item)

        scored.sort(key=lambda item: item["score"], reverse=True)
        return self._apply_retrieval_policy(query, scored, top_k)

    async def rerank(self, query: str, results: list[dict[str, Any]], top_k: int = 10) -> list[dict[str, Any]]:
        return self.reranker.rerank(query, results, top_k=top_k)

    async def graph_search(self, entities: list[str], relation_types: list[str] | None = None) -> list[dict[str, Any]]:
        return []

    def _tokenize(self, text: str) -> list[str]:
        normalized = text.lower()
        words = re.findall(r"[a-z0-9]+", normalized)
        chinese_chars = re.findall(r"[一-鿿]", normalized)
        return words + chinese_chars
