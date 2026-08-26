"""人工问答对的语义召回。

精确匹配（question_hash）未命中时，用 embedding 余弦相似度找表述相近的问答对；
命中后由 curated_qa_run_service 把人工答案作为参考材料交给大模型组织回答
（不是直接顶出原答案，见该服务的 ``_compose_answer_from_reference``）。

首次遇到某个 agent 的问答对时，对其问题做向量化并落库（懒回填），
之后直接读库比较；全局默认向量模型若日后更换，存量向量会与新问题向量不可比，
需重新生成（当前规模小、默认模型稳定，暂不做模型版本标记）。
"""

from __future__ import annotations

import numpy as np

from yuxi.config.app import resolve_embedding_model
from yuxi.models.embed import select_embedding_model
from yuxi.repositories.curated_qa_repository import CuratedQARepository
from yuxi.storage.postgres.models_curated_qa import CuratedQAPair
from yuxi.utils import logger

# 余弦相似度阈值。命中后大模型以该问答对为参考组织回答（提示词要求参考与问题
# 不相关时就如实拒答），因此阈值取能覆盖"同义改述"的均衡值，不必像直接顶答案那样严。
CURATED_QA_SEMANTIC_THRESHOLD = 0.70


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    va, vb = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    denominator = float(np.linalg.norm(va) * np.linalg.norm(vb))
    if denominator == 0.0:
        return 0.0
    return float(np.dot(va, vb) / denominator)


class CuratedQASemanticMatcher:
    """对某个 agent 的人工问答对做语义召回，缺失向量懒回填。"""

    def __init__(self, repository: CuratedQARepository):
        self.repository = repository

    async def find_match(self, *, agent_slug: str, question: str) -> CuratedQAPair | None:
        """返回与问题语义最相近且过阈值的问答对；无匹配返回 None。"""
        pairs = await self.repository.list_enabled_for_agent(agent_slug)
        if not pairs:
            return None

        await self._backfill_embeddings(pairs)

        model = select_embedding_model(resolve_embedding_model())
        query_vector = (await model.abatch_encode([question]))[0]

        best: CuratedQAPair | None = None
        best_score = CURATED_QA_SEMANTIC_THRESHOLD
        for pair in pairs:
            score = _cosine_similarity(query_vector, pair.question_embedding or [])
            if score >= best_score:
                best, best_score = pair, score
        return best

    async def _backfill_embeddings(self, pairs: list[CuratedQAPair]) -> None:
        missing = [pair for pair in pairs if not pair.question_embedding]
        if not missing:
            return
        model = select_embedding_model(resolve_embedding_model())
        vectors = await model.abatch_encode([pair.question for pair in missing])
        for pair, vector in zip(missing, vectors):
            pair.question_embedding = vector
        await self.repository.session.flush()
        await self.repository.session.commit()
        logger.info("已为 agent 的 %d 条人工问答对补全问题向量", len(missing))
