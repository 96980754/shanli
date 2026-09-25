"""问答明细产品线解析与过滤的单元测试。"""

from __future__ import annotations

import pytest

from server.routers.dashboard_router import (
    _aggregate_qa_domain_trend,
    _aggregate_qa_stats_by_domain,
    _filter_qa_records,
    _resolve_qa_domain,
)
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


def _qa_record(domain: str, answer_type: str = "answered", created_at: str = "2026-09-24T02:00:00") -> dict:
    # created_at 为 naive UTC（同 _load_qa_record_rows 输出的 isoformat）
    return {"created_at": created_at, "domain": domain, "answer_type": answer_type}


def test_aggregate_qa_stats_counts_rates_and_coverage():
    records = [
        _qa_record("dispatch"),
        _qa_record("dispatch", "knowledge_refusal"),
        _qa_record("dispatch", "scope_refusal"),
        _qa_record("mdm", "policy_refusal"),
        _qa_record("unknown"),
    ]
    summary = _aggregate_qa_stats_by_domain(records, LINES)

    assert [row["domain"] for row in summary["lines"]] == ["dispatch", "mdm", "unknown"]
    by_domain = {row["domain"]: row for row in summary["lines"]}
    assert by_domain["dispatch"]["total"] == 3
    assert by_domain["dispatch"]["refusal_count"] == 2
    assert by_domain["dispatch"]["refusal_rate"] == 66.67
    assert by_domain["dispatch"]["answer_types"] == {
        "answered": 1,
        "knowledge_refusal": 1,
        "scope_refusal": 1,
    }
    assert by_domain["mdm"]["refusal_rate"] == 100.0
    # coverage 与逐线合计自洽
    assert summary["coverage"] == {"total": 5, "classified": 4, "unclassified": 1, "classified_rate": 80.0}
    assert sum(row["total"] for row in summary["lines"]) == summary["coverage"]["total"]


def test_aggregate_qa_stats_keeps_zero_lines_and_orders_stray_before_unknown():
    summary = _aggregate_qa_stats_by_domain([_qa_record("ghost"), _qa_record("unknown")], LINES)

    # 零量业务线保留（看板 x 轴稳定）；游离 code（业务线被删后的历史行）排在配置线之后、unknown 之前
    assert [row["domain"] for row in summary["lines"]] == ["dispatch", "mdm", "ghost", "unknown"]
    assert all(row["total"] == 0 and row["refusal_rate"] == 0.0 for row in summary["lines"][:2])
    assert summary["coverage"] == {"total": 2, "classified": 1, "unclassified": 1, "classified_rate": 50.0}


def test_aggregate_qa_stats_empty_records():
    summary = _aggregate_qa_stats_by_domain([], LINES)

    assert summary["coverage"] == {"total": 0, "classified": 0, "unclassified": 0, "classified_rate": 0.0}
    # 零量配置线保留；unknown 无记录时不出现（未分类为 0 由 coverage 卡片表达）
    assert [row["domain"] for row in summary["lines"]] == ["dispatch", "mdm"]


def test_aggregate_qa_domain_trend_beijing_day_and_fill_zero():
    records = [
        _qa_record("dispatch", created_at="2026-09-23T02:00:00"),  # 北京 23 日 10:00
        _qa_record("mdm", created_at="2026-09-24T18:00:00"),  # 北京 25 日 02:00（跨日界）
        _qa_record("mdm", created_at="2026-09-24T18:30:00"),
    ]
    trend = _aggregate_qa_domain_trend(records, "2026-09-23", "2026-09-25", LINES)

    assert trend["categories"] == ["dispatch", "mdm"]
    assert [point["date"] for point in trend["data"]] == ["2026-09-23", "2026-09-24", "2026-09-25"]
    assert trend["data"][0]["data"] == {"dispatch": 1, "mdm": 0}
    assert trend["data"][1]["data"] == {"dispatch": 0, "mdm": 0}
    assert trend["data"][1]["total"] == 0
    assert trend["data"][2]["data"] == {"dispatch": 0, "mdm": 2}
    assert trend["data"][2]["total"] == 2


def test_aggregate_qa_domain_trend_defaults_to_record_day_range():
    records = [
        _qa_record("dispatch", created_at="2026-09-23T02:00:00"),
        _qa_record("mdm", created_at="2026-09-25T02:00:00"),
    ]
    trend = _aggregate_qa_domain_trend(records, None, None, LINES)

    assert [point["date"] for point in trend["data"]] == ["2026-09-23", "2026-09-24", "2026-09-25"]
    assert trend["data"][1]["total"] == 0


def test_aggregate_qa_domain_trend_orders_unknown_last():
    records = [_qa_record("unknown"), _qa_record("dispatch")]
    trend = _aggregate_qa_domain_trend(records, None, None, LINES)

    assert trend["categories"] == ["dispatch", "unknown"]


def test_aggregate_qa_domain_trend_empty_records():
    assert _aggregate_qa_domain_trend([], None, None, LINES) == {"categories": [], "data": []}
