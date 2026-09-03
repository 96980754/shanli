from __future__ import annotations

import pytest

from yuxi.services.knowledge_scope_gate import ScopeCorpus, build_scope_corpus, evaluate_scope, judge_off_topic


def _corpus(terms=(), anchors=(), description: str | None = None) -> ScopeCorpus:
    anchor_lines = list(anchors)
    return ScopeCorpus(
        terms=frozenset(terms),
        anchors=anchor_lines,
        description=description if description is not None else "\n".join(anchor_lines),
    )


class _FakeEmbedder:
    """为文本返回可预测向量：命中前缀当业务锚向量，其余返回零向量（亲和为 0）。"""

    def __init__(self, anchor_text_prefix: str):
        self._prefix = anchor_text_prefix
        self._anchor = [1.0, 0.0]

    async def abatch_encode(self, texts):
        return [self._anchor if str(t).startswith(self._prefix) else [0.0, 0.0] for t in texts]


async def _should_not_call(messages):
    raise AssertionError("不应调用小模型确认")


# ---- 关键词命中（免费层，零模型开销）----

async def test_term_hit_returns_in_scope_without_model():
    corpus = _corpus(terms=("调度台",), anchors=("调度台开通权限流程",))
    # 关键词命中在 embedding / judge 之前返回；caller 抛错可证明未被调用。
    assert await evaluate_scope("调度台怎么开通权限？", corpus, caller=_should_not_call) == "in_scope"


# ---- embedding 亲和层 ----

async def test_high_affinity_returns_in_scope_without_judge():
    corpus = _corpus(anchors=("调度台开通权限流程",))
    embedder = _FakeEmbedder("调度台")
    verdict = await evaluate_scope(
        "调度台开通权限需要哪些材料？", corpus, caller=_should_not_call, embedder=embedder
    )
    assert verdict == "in_scope"


async def test_low_affinity_falls_back_to_judge_off_topic():
    corpus = _corpus(anchors=("调度台开通权限流程",))
    embedder = _FakeEmbedder("调度台")

    async def off_topic_judge(messages):
        return '{"off_topic": true}'

    verdict = await evaluate_scope("介绍一下linux的epoll", corpus, caller=off_topic_judge, embedder=embedder)
    assert verdict == "off_topic"


async def test_low_affinity_uncertain_keeps_in_scope():
    corpus = _corpus(anchors=("调度台开通权限流程",))
    embedder = _FakeEmbedder("调度台")

    async def uncertain_judge(messages):
        return '{"off_topic": false}'

    verdict = await evaluate_scope("linux 的 epoll 是什么", corpus, caller=uncertain_judge, embedder=embedder)
    assert verdict == "in_scope"


async def test_embedding_failure_degrades_to_judge():
    corpus = _corpus(anchors=("调度台开通权限流程",))

    class _BrokenEmbedder:
        async def abatch_encode(self, texts):
            raise RuntimeError("embedding down")

    async def off_topic_judge(messages):
        return '{"off_topic": true}'

    assert await evaluate_scope("聊点别的吧", corpus, caller=off_topic_judge, embedder=_BrokenEmbedder()) == "off_topic"


# ---- 无锚无词时完全依赖 judge ----

async def test_no_corpus_text_relies_on_judge():
    corpus = _corpus()

    async def off_topic_judge(messages):
        return '{"off_topic": true}'

    assert await evaluate_scope("介绍一下linux的epoll", corpus, caller=off_topic_judge) == "off_topic"

    async def keep_judge(messages):
        return '{"off_topic": false}'

    assert await evaluate_scope("介绍一下linux的epoll", corpus, caller=keep_judge) == "in_scope"


async def test_judge_failure_or_missing_keeps_in_scope(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("yuxi.services.knowledge_scope_gate.REFUSAL_JUDGE_MODEL", "")

    async def failing_judge(messages):
        raise RuntimeError("judge down")

    corpus = _corpus()
    assert await evaluate_scope("随便聊聊", corpus, caller=failing_judge) == "in_scope"
    assert await evaluate_scope("随便聊聊", corpus, caller=None) == "in_scope"


async def test_judge_off_topic_parses_json_wrapped_in_text():
    async def caller(messages):
        return '说明：\n```json\n{"off_topic": true}\n```'

    assert await judge_off_topic("天气怎么样", "业务范围：终端产品", caller=caller) is True


# ---- build_scope_corpus（语料构建 + 使能 KB 过滤）----

async def test_build_scope_corpus_combines_terms_and_enabled_kb_anchors():
    async def fake_list_databases(uid):
        return {
            "databases": [
                {"kb_id": "kb_1", "name": "调度台手册", "description": "调度台开通与运维"},
                {"kb_id": "kb_2", "name": "终端FAQ", "description": ""},
                {"kb_id": "kb_9", "name": "无关资料", "description": "公司行政"},
            ]
        }

    corpus = await build_scope_corpus(
        uid="u1",
        system_prompt="客服 Agent：解答调度台相关业务。",
        enabled_kb_ids=["kb_1", "kb_2"],
        list_databases=fake_list_databases,
    )
    assert "调度台" in corpus.terms  # 内置种子词
    assert any("调度台手册" in anchor for anchor in corpus.anchors)
    assert not any("无关资料" in anchor for anchor in corpus.anchors)
    assert "调度台开通与运维" in corpus.description
    assert "客服 Agent：解答调度台相关业务。" in corpus.description


async def test_build_scope_corpus_loader_failure_keeps_terms():
    async def failing_list(uid):
        raise RuntimeError("db down")

    corpus = await build_scope_corpus(uid="u1", list_databases=failing_list)
    assert "客服" in corpus.terms
