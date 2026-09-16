"""Udesk 开放接口客户端：鉴权签名、限流、重试与多套响应信封解析。

实现规范见 docs/vibe/udesk/2026-09-14-udesk-implementation-spec.md，要点：
- 签名集中在此处（A4），每次请求新生成 nonce（A2）与 timestamp（A3）；
- 全平台一套算法 SHA256(v2)、一个主机（`https://{subdomain}.udesk.cn`）：客服
  对话记录已确认走 `/open_api_v1/im/*`，与客户/工单等接口同域同鉴权；
- 限流 ≤ rate_limit_per_min，超限排队而非丢弃（A5）；
- 凭证（token/sign/nonce）不进日志、不进异常文本（A8）；
- Udesk 用 HTTP 200 + 业务码表达错误，禁止按 HTTP status 判定成败（B1/B2）；
- **三套响应信封、三套成功码语义**，按响应形状分流解析（B3/B4）：
  v2 开放接口 `{code:1000}`、IM 工作台 `{succeed,code,bizCode}`、
  IM 对话记录 `{status:0,...}`（**无 code 字段**，2026-09-15 实测）；
- IM 工作台的 2001/2002 是业务态（排队/无客服在线），不算错误（B5）；
- 网络错误/5xx/429 按指数退避重试，重试同样消耗限流额度（D9）。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx

from yuxi.services.udesk.config import UdeskConfig
from yuxi.utils import logger

# v2 开放接口业务码（HTTP 也可能是 200，必须看 code）
OPEN_API_CODE_MESSAGES: dict[int, str] = {
    1000: "成功",
    2002: "子域名无效",
    2059: "open api 签名不对",
}

# IM 接口业务码（bizCode）；2001/2002 是正常业务态，不是配置错误
IM_CODE_MESSAGES: dict[int, str] = {
    1000: "成功",
    2001: "排队中/客服繁忙",
    2002: "当前没有客服在线",
    2062: "IM 接口未通过鉴权",
}

# IM 家族中属「业务态」的 bizCode：调用方应按排队/无人在线处理，不当错误上报（B5）
IM_BUSINESS_STATE_CODES = {2001, 2002}

# IM 对话记录接口业务码（字段名为 status，**响应里没有 code**）；status=0 才是成功
IM_SESSIONS_CODE_MESSAGES: dict[int, str] = {
    0: "成功",
    2000: "参数错误",
}

# 重试判定的 HTTP 状态（网络异常另行判断）
RETRYABLE_STATUSES = {429}


@dataclass
class UdeskResponse:
    """解析后的 Udesk 响应。ok 由所属信封的成功码决定（B2）：v2/工作台为 code==1000，
    对话记录为 status==0。code 统一承载成功码字段，便于调用方按 family 解释。"""

    ok: bool
    code: int | None
    message: str
    family: str  # "open_api" | "im" | "im_sessions"
    business_state: str | None  # IM 工作台业务态（排队/无客服在线）时非空
    data: dict[str, Any] | None  # 原始响应体；调用方必须按字段白名单取值，严禁整包落库（C1）


class UdeskTransportError(Exception):
    """重试耗尽后的传输层错误。信息只含方法/路径/原因，不含任何凭证。"""

    def __init__(self, method: str, path: str, reason: str):
        super().__init__(f"udesk transport failed: {method} {path}: {reason}")
        self.method = method
        self.path = path
        self.reason = reason


def build_sign(email: str, token: str, timestamp: str, nonce: str) -> str:
    """sign = SHA256(email & open_api_token & timestamp & nonce & sign_version)。"""
    raw = "&".join([email, token, timestamp, nonce, "v2"])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def auth_query(email: str, token: str) -> dict[str, str]:
    """鉴权 Query 参数。每次调用新生成 timestamp 与 nonce（A2/A3，nonce 15 分钟内仅可用一次）。"""
    timestamp = str(int(time.time()))
    nonce = str(uuid.uuid4())
    return {
        "email": email,
        "timestamp": timestamp,
        "sign": build_sign(email, token, timestamp, nonce),
        "nonce": nonce,
        "sign_version": "v2",
    }


def parse_response(status: int, body: str) -> UdeskResponse:
    """按响应形状分流解析三套信封。

    v2 开放接口 {code, code_message, exception}；IM 工作台 {succeed, code, bizCode, message}；
    IM 对话记录 {status, message, item, size, total, total_pages}。判定顺序（缺一不可）：

    1. 含 bizCode → IM 工作台；
    2. **不含 code 且含 status** → IM 对话记录。前置的 `code not in parsed` 是关键：
       防住任何同时带 status 字段的 v2 响应被误判（v2 成功响应必带 code）；
    3. 其余 → v2 开放接口。
    """
    try:
        parsed = json.loads(body) if body else None
    except (TypeError, ValueError):
        parsed = None
    if not isinstance(parsed, dict):
        return UdeskResponse(
            ok=False,
            code=None,
            message=f"非 JSON 响应（HTTP {status}）",
            family="open_api",
            business_state=None,
            data=None,
        )

    if "bizCode" in parsed:
        family = "im"
    elif "code" not in parsed and "status" in parsed:
        family = "im_sessions"
    else:
        family = "open_api"

    code_tables = {
        "im": IM_CODE_MESSAGES,
        "im_sessions": IM_SESSIONS_CODE_MESSAGES,
        "open_api": OPEN_API_CODE_MESSAGES,
    }
    success_code = 0 if family == "im_sessions" else 1000
    code_table = code_tables[family]
    raw_code = parsed.get("status" if family == "im_sessions" else "code")
    try:
        code = int(raw_code) if raw_code is not None else None
    except (TypeError, ValueError):
        code = None
    message = str(
        parsed.get("code_message")
        or parsed.get("message")
        or (code_table.get(code) if code is not None else "")
        or (f"业务码 {raw_code}" if raw_code is not None else f"HTTP {status}")
    )
    business_state = None
    if family == "im" and code in IM_BUSINESS_STATE_CODES:
        business_state = code_table.get(code, message)
    return UdeskResponse(
        ok=code == success_code,
        code=code,
        message=message,
        family=family,
        business_state=business_state,
        data=parsed,
    )


class AsyncRateLimiter:
    """最小间隔限流：稳态速率 ≤ limit_per_min，超限调用排队等待而非丢弃。

    语义上等价于容量为 1 的令牌桶。默认 24 次/分（间隔 2.5s）：官方对
    `im/sessions/search` 与 `im/sessions/log` 各限 1 次/2 秒，全局匀速取各端点中最严
    的间隔即可同时满足，留 0.5s 余量避免贴着上限被拒。clock/sleep 可注入以便测试。
    """

    def __init__(self, limit_per_min: int, *, clock=time.monotonic, sleep=asyncio.sleep):
        if limit_per_min <= 0:
            raise ValueError("limit_per_min 必须为正整数")
        self._interval = 60.0 / limit_per_min
        self._clock = clock
        self._sleep = sleep
        self._next_allowed_at: float | None = None
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = self._clock()
            if self._next_allowed_at is None:
                self._next_allowed_at = now
            wait = max(0.0, self._next_allowed_at - now)
            self._next_allowed_at = max(self._next_allowed_at, now) + self._interval
        if wait > 0:
            await self._sleep(wait)


async def httpx_transport(method: str, url: str, body: dict[str, Any] | None, timeout_seconds: int) -> tuple[int, str]:
    """默认传输层：每次请求短连接，返回 (HTTP status, 响应文本)。"""
    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        response = await client.request(method, url, json=body)
        return response.status_code, response.text


class UdeskClient:
    """Udesk 开放接口客户端（仅服务端调用，A6）。"""

    def __init__(
        self,
        *,
        base_url: str,
        email: str,
        open_api_token: str,
        rate_limit_per_min: int = 24,
        request_timeout_seconds: int = 15,
        max_retries: int = 2,
        transport=httpx_transport,
        clock=time.monotonic,
        sleep=asyncio.sleep,
    ):
        self._base_url = base_url.rstrip("/")
        self._email = email
        self._token = open_api_token
        self._timeout = request_timeout_seconds
        self._max_retries = max(0, max_retries)
        self._transport = transport
        self._limiter = AsyncRateLimiter(rate_limit_per_min, clock=clock, sleep=sleep)
        self._sleep = sleep

    @classmethod
    def from_config(cls, config: UdeskConfig) -> UdeskClient:
        return cls(
            base_url=config.base_url,
            email=config.email,
            open_api_token=config.open_api_token,
            rate_limit_per_min=config.rate_limit_per_min,
            request_timeout_seconds=config.request_timeout_seconds,
            max_retries=config.max_retries,
        )

    async def get(self, path: str, *, query: dict[str, Any] | None = None) -> UdeskResponse:
        return await self.request("GET", path, query=query)

    async def post(self, path: str, *, body: dict[str, Any] | None = None) -> UdeskResponse:
        return await self.request("POST", path, body=body)

    async def request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        query: dict[str, Any] | None = None,
    ) -> UdeskResponse:
        """发起一次带鉴权的调用（主机与签名算法全平台统一，无按调用覆盖）。"""
        extra = "" if not query else ("&" + urlencode(query))
        last_reason = ""
        for attempt in range(self._max_retries + 1):
            # 每次尝试（含重试）都消耗限流额度（D9）
            await self._limiter.acquire()
            signed = auth_query(self._email, self._token)
            url = f"{self._base_url}{path}?{urlencode(signed)}{extra}"
            try:
                status, text = await self._transport(method.upper(), url, body, self._timeout)
            except Exception as exc:  # noqa: BLE001 - 传输层任何异常按可重试处理
                last_reason = f"{type(exc).__name__}"
                logger.warning(
                    f"udesk call transport error method={method} path={path} attempt={attempt} reason={last_reason}"
                )
                if attempt < self._max_retries:
                    await self._sleep(0.5 * (2**attempt))
                    continue
                raise UdeskTransportError(method, path, last_reason) from exc
            if status in RETRYABLE_STATUSES or status >= 500:
                last_reason = f"HTTP {status}"
                logger.warning(
                    f"udesk call retryable status method={method} path={path} status={status} attempt={attempt}"
                )
                if attempt < self._max_retries:
                    await self._sleep(0.5 * (2**attempt))
                    continue
                break
            response = parse_response(status, text)
            logger.debug(
                f"udesk call method={method} path={path} status={status} code={response.code} ok={response.ok}"
            )
            return response
        return UdeskResponse(
            ok=False,
            code=None,
            message=f"重试耗尽（{last_reason}）",
            family="open_api",
            business_state=None,
            data=None,
        )
