"""
Integration tests for dashboard router endpoints.
"""

from __future__ import annotations

import csv
import io

import pytest

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def test_dashboard_requires_authentication(test_client):
    response = await test_client.get("/api/dashboard/conversations")
    assert response.status_code == 401


async def test_standard_user_is_forbidden(test_client, standard_user):
    response = await test_client.get("/api/dashboard/conversations", headers=standard_user["headers"])
    assert response.status_code == 403


async def test_admin_can_fetch_conversations(test_client, admin_headers):
    response = await test_client.get("/api/dashboard/conversations", headers=admin_headers)
    assert response.status_code == 200, response.text
    assert isinstance(response.json(), list)


async def test_admin_can_fetch_stats(test_client, admin_headers):
    """Test that all stats endpoints return 200 and don't crash on DB queries."""

    # Test call timeseries stats for all types
    types = ["models", "agents", "tokens", "tools"]
    for stats_type in types:
        response = await test_client.get(
            f"/api/dashboard/stats/calls/timeseries?type={stats_type}&time_range=14days", headers=admin_headers
        )
        assert response.status_code == 200, f"{stats_type} stats failed: {response.text}"
        data = response.json()
        assert "data" in data
        assert "categories" in data

    # Test user activity stats
    response = await test_client.get("/api/dashboard/stats/users", headers=admin_headers)
    assert response.status_code == 200, f"user stats failed: {response.text}"
    assert "total_users" in response.json()

    # Test tool call stats
    response = await test_client.get("/api/dashboard/stats/tools", headers=admin_headers)
    assert response.status_code == 200, f"tool stats failed: {response.text}"
    assert "total_calls" in response.json()

    # 满意度统计（未反馈默认计满意口径）返回原始计数与可评价基数
    response = await test_client.get("/api/dashboard/stats", headers=admin_headers)
    assert response.status_code == 200, f"stats failed: {response.text}"
    feedback_stats = response.json()["feedback_stats"]
    for key in (
        "total_feedbacks",
        "like_count",
        "dislike_count",
        "evaluable_count",
        "silent_count",
        "satisfaction_rate",
        "participation_rate",
        "refusal_count",
        "refusal_rate",
    ):
        assert key in feedback_stats, f"feedback_stats missing {key}"
    assert 0 <= feedback_stats["satisfaction_rate"] <= 100
    assert 0 <= feedback_stats["refusal_count"] <= feedback_stats["evaluable_count"]
    assert 0 <= feedback_stats["refusal_rate"] <= 100


async def test_admin_can_fetch_feedback_summary_with_satisfaction_breakdown(test_client, admin_headers):
    response = await test_client.get("/api/dashboard/feedback-summary", headers=admin_headers)
    assert response.status_code == 200, f"feedback summary failed: {response.text}"
    data = response.json()
    for key in (
        "total_feedbacks",
        "like_count",
        "dislike_count",
        "evaluable_count",
        "silent_count",
        "satisfaction_rate",
        "participation_rate",
        "refusal_count",
        "refusal_rate",
        "reason_stats",
        "legacy_unclassified_count",
    ):
        assert key in data, f"feedback-summary missing {key}"
    # 未反馈 = 可评价基数 − 显式反馈；满意率 = (好评 + 未反馈) / 可评价基数
    assert data["silent_count"] == data["evaluable_count"] - data["like_count"] - data["dislike_count"]
    assert 0 <= data["satisfaction_rate"] <= 100
    assert 0 <= data["refusal_count"] <= data["evaluable_count"]
    assert 0 <= data["refusal_rate"] <= 100


async def test_admin_can_fetch_feedbacks(test_client, admin_headers):
    """反馈列表分页信封 + 行字段（含拒答来源/已补答派生列）。"""
    response = await test_client.get("/api/dashboard/feedbacks", headers=admin_headers)
    assert response.status_code == 200, f"feedbacks failed: {response.text}"
    data = response.json()
    assert isinstance(data, dict)
    assert "total" in data and "items" in data
    assert isinstance(data["items"], list)
    row_keys = (
        "id",
        "message_id",
        "conversation_thread_id",
        "uid",
        "username",
        "avatar",
        "rating",
        "status",
        "reason",
        "created_at",
        "message_content",
        "conversation_title",
        "agent_id",
        "is_refusal_source",
        "has_qa_pair",
    )
    for item in data["items"]:
        for key in row_keys:
            assert key in item, f"feedback item missing {key}"


async def test_admin_can_filter_feedbacks_by_rating_status_keyword(test_client, admin_headers):
    """反馈列表筛选参数可组合且不报错；过滤结果与入参一致。"""
    response = await test_client.get(
        "/api/dashboard/feedbacks?rating=dislike&status=pending&keyword=系统&limit=5&offset=0",
        headers=admin_headers,
    )
    assert response.status_code == 200, f"feedbacks failed: {response.text}"
    data = response.json()
    assert "total" in data
    for item in data["items"]:
        assert item["rating"] == "dislike"
        assert item["status"] == "pending"


async def test_update_feedback_status_missing_returns_404(test_client, admin_headers):
    response = await test_client.patch(
        "/api/dashboard/feedbacks/999999/status",
        headers=admin_headers,
        json={"status": "processed"},
    )
    assert response.status_code == 404


async def test_admin_stats_rejects_invalid_date_params(test_client, admin_headers):
    """时段参数格式错误或 start > end 返回 400。"""
    for query in ("start_date=2026/09/01", "end_date=20260901", "start_date=not-a-date"):
        response = await test_client.get(f"/api/dashboard/stats?{query}", headers=admin_headers)
        assert response.status_code == 400, f"{query} should be rejected: {response.text}"

    response = await test_client.get(
        "/api/dashboard/stats?start_date=2026-09-10&end_date=2026-09-01", headers=admin_headers
    )
    assert response.status_code == 400


async def test_admin_stats_with_date_range_filters_counts(test_client, admin_headers):
    """带时段时各计数为全量口径的子集，率值仍在合法区间。"""
    full_response = await test_client.get("/api/dashboard/stats", headers=admin_headers)
    assert full_response.status_code == 200, full_response.text
    ranged_response = await test_client.get(
        "/api/dashboard/stats?start_date=2026-09-01&end_date=2026-09-22", headers=admin_headers
    )
    assert ranged_response.status_code == 200, ranged_response.text

    full, ranged = full_response.json(), ranged_response.json()
    for key in ("total_conversations", "active_conversations", "total_messages", "total_users"):
        assert ranged[key] <= full[key], f"{key} with date range should be a subset of full"
    full_feedback, ranged_feedback = full["feedback_stats"], ranged["feedback_stats"]
    assert ranged_feedback["total_feedbacks"] <= full_feedback["total_feedbacks"]
    assert ranged_feedback["evaluable_count"] <= full_feedback["evaluable_count"]
    assert 0 <= ranged_feedback["satisfaction_rate"] <= 100
    assert 0 <= ranged_feedback["knowledge_gap_rate"] <= 100


async def test_admin_stats_includes_qa_counters(test_client, admin_headers):
    """stats 返回问答次数/人数，且时段口径为全量子集。"""
    full_response = await test_client.get("/api/dashboard/stats", headers=admin_headers)
    assert full_response.status_code == 200, full_response.text
    full = full_response.json()
    for key in ("qa_count", "qa_user_count"):
        assert key in full, f"stats missing {key}"
        assert full[key] >= 0

    ranged_response = await test_client.get(
        "/api/dashboard/stats?start_date=2026-09-01&end_date=2026-09-22", headers=admin_headers
    )
    assert ranged_response.status_code == 200, ranged_response.text
    ranged = ranged_response.json()
    assert ranged["qa_count"] <= full["qa_count"]
    assert ranged["qa_user_count"] <= full["qa_user_count"]


async def test_admin_can_fetch_qa_records(test_client, admin_headers):
    """问答明细返回行结构完整：问题/回答/产品线/回答类型/用户/会话。"""
    response = await test_client.get("/api/dashboard/qa-records?limit=5", headers=admin_headers)
    assert response.status_code == 200, response.text
    data = response.json()
    assert "total" in data and isinstance(data["items"], list)
    for item in data["items"][:5]:
        for key in (
            "id",
            "created_at",
            "question",
            "answer",
            "domain",
            "answer_type",
            "uid",
            "agent_id",
            "thread_id",
        ):
            assert key in item, f"qa record missing {key}"
        # 终答条件已排除空正文，回答正文不应为空
        assert item["answer"], "qa record answer should not be empty"


async def test_qa_records_filters_and_pagination(test_client, admin_headers):
    """domain/keyword 过滤与分页：子集关系与 offset 翻页不重叠。"""
    base = await test_client.get("/api/dashboard/qa-records", headers=admin_headers)
    assert base.status_code == 200, base.text
    total_all = base.json()["total"]
    if total_all == 0:
        pass  # 空库时仅验证 200 与结构，跳过子集断言

    filtered = await test_client.get("/api/dashboard/qa-records?domain=unknown&limit=200", headers=admin_headers)
    assert filtered.status_code == 200, filtered.text
    assert filtered.json()["total"] <= total_all
    for item in filtered.json()["items"]:
        assert item["domain"] == "unknown"

    keyworded = await test_client.get(
        "/api/dashboard/qa-records?keyword=%E4%B8%8D%E5%AD%98%E5%9C%A8", headers=admin_headers
    )
    assert keyworded.status_code == 200, keyworded.text
    assert keyworded.json()["total"] == 0

    page1 = await test_client.get("/api/dashboard/qa-records?limit=2&offset=0", headers=admin_headers)
    page2 = await test_client.get("/api/dashboard/qa-records?limit=2&offset=2", headers=admin_headers)
    ids1 = {item["id"] for item in page1.json()["items"]}
    ids2 = {item["id"] for item in page2.json()["items"]}
    assert not (ids1 & ids2), "分页 offset 之间不应重叠"


async def test_qa_records_export_csv(test_client, admin_headers):
    """导出返回带 BOM 的 CSV，表头与行数和明细接口一致。"""
    list_response = await test_client.get("/api/dashboard/qa-records", headers=admin_headers)
    assert list_response.status_code == 200, list_response.text

    export_response = await test_client.get("/api/dashboard/qa-records/export", headers=admin_headers)
    assert export_response.status_code == 200, export_response.text
    assert export_response.headers["content-type"].startswith("text/csv")
    assert "attachment" in export_response.headers.get("content-disposition", "")

    content = export_response.text
    assert content.startswith("﻿"), "CSV 应带 UTF-8 BOM 以便 Excel 直接打开"
    # 回答正文含换行（markdown），按行 split 会把一条记录拆成多行，须用 csv 解析
    rows = [row for row in csv.reader(io.StringIO(content.lstrip("﻿"))) if row]
    assert rows[0] == ["时间", "用户", "问题", "回答", "产品线", "回答类型", "智能体", "会话ID"]
    assert len(rows) - 1 == list_response.json()["total"]

    # 带筛选导出：行数与带筛选的明细一致
    filtered_list = await test_client.get("/api/dashboard/qa-records?domain=unknown", headers=admin_headers)
    filtered_export = await test_client.get("/api/dashboard/qa-records/export?domain=unknown", headers=admin_headers)
    assert filtered_export.status_code == 200
    filtered_rows = [row for row in csv.reader(io.StringIO(filtered_export.text.lstrip("﻿"))) if row]
    assert len(filtered_rows) - 1 == filtered_list.json()["total"]
