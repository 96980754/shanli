"""scripts/eval_datasets/score_kefu_facts.py 的 classify 归因单测（无网络、不调 LLM）。

重点是「评测失败不得伪装成知识库缺口」：judge 调用失败时既不是实质作答，
也不能计入缺口拒答，否则评测故障会被记成知识库问题。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "eval_datasets" / "score_kefu_facts.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("score_kefu_facts", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def scorer():
    return _load_module()


def _judge(gate: str, reason: str = "", error: str | None = None) -> dict:
    return {"gate": gate, "gate_reason": reason, "judge_error": error}


def _record(answer: str, error: str | None = None) -> dict:
    return {"agent_answer": answer, "error": error}


REFUSAL_ANSWER = "抱歉，在现有知识库中未找到相关依据。\n\n- 缺失的是该型号的技术参数。"
SUBSTANTIVE_ANSWER = "**结论**：在《终端APP用户手册》中，该问题对应两项排查点：网络未连通、账号未绑定。"


def test_judge_error_is_not_counted_as_knowledge_gap(scorer):
    """judge 调用失败：既不判实质作答，也不计入缺口拒答。"""
    assert scorer.classify(_record(SUBSTANTIVE_ANSWER), _judge("fail", "", "Connection error.")) == "judge_error"
    assert scorer.classify(_record(REFUSAL_ANSWER), _judge("fail", "", "Connection error.")) == "judge_error"


def test_judge_fail_with_refusal_reason_is_gap(scorer):
    """judge 正常判 fail 且归因是拒答 → 缺口。"""
    cls = scorer.classify(_record(REFUSAL_ANSWER), _judge("fail", "系统回答声明知识库中未找到相关依据"))
    assert cls == "refusal_gap"


def test_judge_fail_with_clarify_reason_is_clarify(scorer):
    """judge 正常判 fail 且归因是反问澄清 → 需澄清。"""
    assert scorer.classify(_record(REFUSAL_ANSWER), _judge("fail", "系统反问请用户补充信息")) == "clarify_missing"


def test_judge_pass_is_answered(scorer):
    """judge 判 pass → 实质作答，不受回答长度影响。"""
    assert scorer.classify(_record(SUBSTANTIVE_ANSWER), _judge("pass", "实质作答")) == "answered"


def test_short_refusal_without_judge_falls_back_to_gap(scorer):
    """无 judge 时的兜底：短回答含拒答标记 → 缺口。"""
    assert scorer.classify(_record("抱歉，未找到相关依据。"), None) == "refusal_gap"


def test_ask_user_question_error_is_clarify(scorer):
    """run 停在反问中断上 → 需澄清。"""
    rec = _record("", "ask_user_question_required")
    assert scorer.classify(rec, None) == "clarify_missing"


def test_other_run_error_is_e2e_error(scorer):
    """其它 run 失败 → 链路异常，不参与答案口径。"""
    assert scorer.classify(_record("", "boom"), None) == "e2e_error"
