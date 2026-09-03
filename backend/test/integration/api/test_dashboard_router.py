"""
Integration tests for dashboard router endpoints.
"""

from __future__ import annotations

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
    ):
        assert key in feedback_stats, f"feedback_stats missing {key}"
    assert 0 <= feedback_stats["satisfaction_rate"] <= 100


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
        "reason_stats",
        "legacy_unclassified_count",
    ):
        assert key in data, f"feedback-summary missing {key}"
    # 未反馈 = 可评价基数 − 显式反馈；满意率 = (好评 + 未反馈) / 可评价基数
    assert data["silent_count"] == data["evaluable_count"] - data["like_count"] - data["dislike_count"]
    assert 0 <= data["satisfaction_rate"] <= 100


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
