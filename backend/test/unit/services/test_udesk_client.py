"""Udesk 接入层客户端单测（规范 G1/G2/G4/G6/G8：全部 Mock，不依赖真实凭证）。"""

from __future__ import annotations

import json
from urllib.parse import parse_qs, urlparse

import pytest

from yuxi.services.udesk.client import (
    AsyncRateLimiter,
    UdeskClient,
    UdeskTransportError,
    auth_query,
    build_sign,
    parse_response,
)

EMAIL = "admin@example.com"
TOKEN = "test-open-api-token"


# ---------------------------------------------------------------- G1 签名向量
def test_build_sign_sha256_is_64_hex_and_deterministic():
    sig = build_sign(EMAIL, TOKEN, "1494474404", "2d931510-d99f-494a-8c67-87feb05e1594")
    assert len(sig) == 64
    assert all(c in "0123456789abcdef" for c in sig)
    assert sig == build_sign(EMAIL, TOKEN, "1494474404", "2d931510-d99f-494a-8c67-87feb05e1594")


def test_build_sign_changes_with_any_segment():
    sig = build_sign(EMAIL, TOKEN, "1494474404", "nonce-a")
    assert sig != build_sign(EMAIL, "other", "1494474404", "nonce-a")
    assert sig != build_sign(EMAIL, TOKEN, "1494474405", "nonce-a")
    assert sig != build_sign(EMAIL, TOKEN, "1494474404", "nonce-b")


# ------------------------------------------------------------ G2 nonce 唯一性
def test_auth_query_fresh_nonce_and_sign_per_call():
    q1 = auth_query(EMAIL, TOKEN)
    q2 = auth_query(EMAIL, TOKEN)
    assert set(q1) == {"email", "timestamp", "sign", "nonce", "sign_version"}
    assert q1["sign_version"] == "v2"
    assert q1["email"] == EMAIL
    assert len(q1["timestamp"]) == 10
    assert q1["nonce"] != q2["nonce"]
    assert q1["sign"] != q2["sign"]


# ------------------------------------------------------ G4 业务码双网关解析
def test_parse_response_2002_means_invalid_subdomain_on_open_api_family():
    response = parse_response(200, json.dumps({"code": 2002, "code_message": "子域名无效"}))
    assert not response.ok
    assert response.family == "open_api"
    assert "子域名" in response.message
    assert response.business_state is None


def test_parse_response_2002_means_no_agent_online_on_im_family():
    response = parse_response(
        200, json.dumps({"succeed": False, "code": 2002, "bizCode": "2002", "message": "当前没有客服在线"})
    )
    assert not response.ok
    assert response.family == "im"
    # IM 的 2002 是业务态（无客服在线），不是配置错误（B3/B5）
    assert response.business_state is not None
    assert "客服在线" in response.business_state


def test_parse_response_im_2001_is_business_state_not_error():
    response = parse_response(200, json.dumps({"succeed": False, "code": 2001, "bizCode": "2001", "message": "排队中"}))
    assert not response.ok
    assert response.family == "im"
    assert "排队" in (response.business_state or "")


def test_parse_response_code_1000_is_success_in_both_families():
    open_api = parse_response(200, json.dumps({"code": 1000, "customers": []}))
    im = parse_response(200, json.dumps({"succeed": True, "code": 1000, "bizCode": None, "data": {}}))
    assert open_api.ok and open_api.data == {"code": 1000, "customers": []}
    assert im.ok and im.family == "im"


# ------------------------------------------- G4 对话记录族（im_sessions）信封
def test_parse_response_im_sessions_status_zero_is_success():
    """对话记录接口响应里**没有 code 字段**，成功码是 status=0。

    若沿用 ok = code == 1000，这里 code 取不到值，成功响应会被判成失败——
    拉取链路会静默变成「一条都拉不到」。
    """
    response = parse_response(
        200,
        json.dumps({"status": 0, "message": "成功", "item": [], "size": 0, "total": 0, "total_pages": 0}),
    )
    assert response.ok
    assert response.family == "im_sessions"
    assert response.code == 0
    assert response.message == "成功"
    assert response.business_state is None


def test_parse_response_im_sessions_non_zero_status_is_failure():
    """实测「窗口超一个月」报 status=2000，且同样没有 code 字段。"""
    response = parse_response(200, json.dumps({"status": 2000, "message": "暂只提供一个月内的数据"}))
    assert not response.ok
    assert response.family == "im_sessions"
    assert "一个月" in response.message


def test_parse_response_status_alongside_code_still_counts_as_open_api():
    """判定顺序的关键前置：只有**不含 code** 时才认 status。

    否则任何同时带 status 的 v2 响应都会被误判成对话记录族，成功码从 1000 变成 0。
    """
    response = parse_response(200, json.dumps({"code": 1000, "status": "open", "tickets": []}))
    assert response.ok
    assert response.family == "open_api"
    assert response.code == 1000

    failed = parse_response(200, json.dumps({"code": 2059, "status": "open", "code_message": "签名不对"}))
    assert not failed.ok and failed.family == "open_api"


def test_parse_response_non_json_body():
    response = parse_response(200, "<html>not json</html>")
    assert not response.ok
    assert response.code is None


def test_parse_response_signature_error_code_2059():
    response = parse_response(200, json.dumps({"code": 2059, "code_message": "open api签名不对"}))
    assert not response.ok
    assert response.code == 2059


# ------------------------------------------------------------- G6 限流排队
async def test_rate_limiter_queues_instead_of_dropping():
    now = {"t": 0.0}
    waits: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        waits.append(seconds)
        now["t"] += seconds

    limiter = AsyncRateLimiter(60, clock=lambda: now["t"], sleep=fake_sleep)
    for _ in range(5):
        await limiter.acquire()
    # 第 1 次立即可发；后续按 1s 间隔排队，无人被丢弃
    assert waits == [1.0, 1.0, 1.0, 1.0]


async def test_rate_limiter_rejects_non_positive_limit():
    with pytest.raises(ValueError):
        AsyncRateLimiter(0)


# --------------------------------------------------------- G8 Mock 请求链路
def _make_client(transport, **kwargs) -> UdeskClient:
    return UdeskClient(
        base_url="https://demo.s2.udesk.cn",
        email=EMAIL,
        open_api_token=TOKEN,
        rate_limit_per_min=60,
        max_retries=kwargs.pop("max_retries", 0),
        transport=transport,
        **kwargs,
    )


async def test_request_signs_url_and_passes_body():
    captured: dict = {}

    async def transport(method, url, body, timeout):
        captured.update(method=method, url=url, body=body)
        return 200, json.dumps({"code": 1000, "agents": []})

    client = _make_client(transport)
    response = await client.post("/open_api_v1/agents", body={"page": 1})

    assert response.ok
    query = parse_qs(urlparse(captured["url"]).query)
    assert captured["method"] == "POST"
    assert urlparse(captured["url"]).path == "/open_api_v1/agents"
    assert set(query) == {"email", "timestamp", "sign", "nonce", "sign_version"}
    assert query["email"] == [EMAIL]
    assert query["sign_version"] == ["v2"]
    # 签名与凭证参数一致：sign = SHA256(email & token & timestamp & nonce & v2)
    assert query["sign"] == [build_sign(EMAIL, TOKEN, query["timestamp"][0], query["nonce"][0])]
    assert captured["body"] == {"page": 1}


async def test_request_get_appends_extra_query_params():
    captured: dict = {}

    async def transport(method, url, body, timeout):
        captured.update(url=url)
        return 200, json.dumps({"code": 1000})

    client = _make_client(transport)
    await client.get("/open_api_v1/customers", query={"page": 2})

    query = parse_qs(urlparse(captured["url"]).query)
    assert query["page"] == ["2"]
    assert "sign" in query


async def test_request_retries_on_5xx_then_succeeds():
    statuses = [500, 200]
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    async def transport(method, url, body, timeout):
        return statuses.pop(0), json.dumps({"code": 1000})

    client = _make_client(transport, max_retries=1, sleep=fake_sleep)
    response = await client.get("/open_api_v1/tickets")

    assert response.ok
    assert statuses == []
    # 首个等待为指数退避（0.5 * 2^0）；重试同时消耗限流额度（D9），
    # 因此可能还伴随一条限流间隔等待（同为 fake sleep，不耗时）
    assert sleeps[0] == 0.5


async def test_request_raises_transport_error_after_retries_exhausted():
    calls: list[str] = []

    async def transport(method, url, body, timeout):
        calls.append(url)
        raise TimeoutError("boom")

    client = _make_client(transport, max_retries=1)

    async def fake_sleep(seconds: float) -> None:
        return None

    client._sleep = fake_sleep
    with pytest.raises(UdeskTransportError) as exc_info:
        await client.get("/open_api_v1/tickets")

    assert len(calls) == 2  # 初次 + 1 次重试
    # A8：异常文本只含方法/路径/原因，不含凭证
    message = str(exc_info.value)
    assert "/open_api_v1/tickets" in message
    assert TOKEN not in message


async def test_request_does_not_retry_on_business_error():
    calls: list[str] = []

    async def transport(method, url, body, timeout):
        calls.append(url)
        return 200, json.dumps({"code": 2059, "code_message": "open api签名不对"})

    client = _make_client(transport, max_retries=3)
    response = await client.get("/open_api_v1/customers")

    assert not response.ok
    assert response.code == 2059
    assert len(calls) == 1


async def test_request_uses_configured_host_for_im_sessions_endpoints():
    """对话记录接口与客户/工单同域同鉴权，不需要另设主机或签名算法（配置项已删除）。"""
    captured: dict = {}

    async def transport(method, url, body, timeout):
        captured.update(method=method, url=url, body=body)
        return 200, json.dumps({"status": 0, "message": "成功", "item": [], "total": 0, "total_pages": 0})

    client = _make_client(transport)
    response = await client.get(
        "/open_api_v1/im/sessions/search",
        query={"start_time": "2026-09-01 00:00:00", "end_time": "2026-09-14 00:00:00", "page": 1},
    )

    assert response.ok and response.family == "im_sessions"
    parsed = urlparse(captured["url"])
    assert parsed.netloc == "demo.s2.udesk.cn"
    assert parsed.path == "/open_api_v1/im/sessions/search"
    query = parse_qs(parsed.query)
    assert query["start_time"] == ["2026-09-01 00:00:00"] and query["page"] == ["1"]
    assert len(query["sign"][0]) == 64  # SHA256 v2，全平台唯一一套
    assert captured["body"] is None
