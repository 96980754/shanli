"""问答明细产品线解析与过滤的单元测试。"""

from __future__ import annotations

import pytest

from server.routers.dashboard_router import _filter_qa_records, _resolve_qa_domain
from yuxi.config.app import BusinessLine, config as app_config

pytestmark = [pytest.mark.unit]

LINES = [
    BusinessLine(code="dispatch", name="调度台", keywords=["调度台", "dispatch"]),
    BusinessLine(code="mdm", name="终端管理", keywords=["mdm", "终端管理"]),
]


@pytest.fixture(autouse=True)
def _patch_business_lines(monkeypatch):
    # domain 合法性以全局配置清单为准（sanitize_business_domain 读运行时配置），
    # 与关键词分类注入的 lines 保持同一份，测试行为才与线上一致。
    monkeypatch.setattr(app_config, "business_lines", [line.model_dump() for line in LINES])


def test_resolve_qa_domain_prefers_judged_domain():
    # 拒答由 LLM judge 判定的 domain 优先，不做关键词覆盖
    assert _resolve_qa_domain({"type": "knowledge_refusal", "domain": "mdm"}, "调度台怎么登录", LINES) == "mdm"


def test_resolve_qa_domain_falls_back_to_keywords_for_unknown():
    # answered/unknown 的存量行按问题文本关键词分类
    assert _resolve_qa_domain({"type": "answered", "domain": "unknown"}, "MDM 平台密码怎么改", LINES) == "mdm"
    assert _resolve_qa_domain({"type": "answered"}, "调度台支持哪些制式", LINES) == "dispatch"


def test_resolve_qa_domain_unmatched_stays_unknown():
    assert _resolve_qa_domain({"type": "answered"}, "今天天气怎么样", LINES) == "unknown"
    assert _resolve_qa_domain(None, "你好", LINES) == "unknown"


def test_resolve_qa_domain_drops_invalid_domain_values():
    # 游离 domain 值（不在配置清单内）回退关键词分类
    assert _resolve_qa_domain({"type": "answered", "domain": "ghost_line"}, "调度台怎么登录", LINES) == "dispatch"


def _record(question: str, domain: str) -> dict:
    return {"id": 1, "question": question, "domain": domain, "answer_type": "answered"}


def test_filter_qa_records_by_domain_and_keyword():
    records = [
        _record("调度台支持哪些制式", "dispatch"),
        _record("MDM 密码修改", "mdm"),
        _record("你好", "unknown"),
    ]
    assert [r["question"] for r in _filter_qa_records(records, "mdm", None)] == ["MDM 密码修改"]
    # 关键词大小写不敏感
    assert [r["question"] for r in _filter_qa_records(records, None, "mdm")] == ["MDM 密码修改"]
    assert [r["question"] for r in _filter_qa_records(records, None, "密码")] == ["MDM 密码修改"]
    # 空 keyword 不过滤
    assert len(_filter_qa_records(records, None, "  ")) == 3
    assert _filter_qa_records(records, None, "不存在的词") == []
