"""Create human-handoff records and return the Enterprise WeChat customer-service entry."""

from __future__ import annotations

import hashlib
import threading
from datetime import timedelta
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import select
from yuxi.config.app import config
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import KnowledgeHandoff
from yuxi.utils.datetime_utils import utc_now_naive

_ROUND_ROBIN_LOCK = threading.Lock()
_ROUND_ROBIN_COUNTER = 0


def _round_robin_pick(urls: list[str]) -> str:
    """在客服 URL 池内轮替取一个（进程内计数）。

    转人工入口（knowledge_handoffs 写入）只发生在单个 api 进程；若未来多实例
    部署，需把计数改放 Redis 等共享存储。
    """
    global _ROUND_ROBIN_COUNTER
    with _ROUND_ROBIN_LOCK:
        index = _ROUND_ROBIN_COUNTER % len(urls)
        _ROUND_ROBIN_COUNTER += 1
    return urls[index]


class WeComCustomerService:
    """从配置的客服 URL 池中轮替返回转人工入口。

    P1 反馈：客服入口支持 1..N，转人工轮替转接，避免单个客服扛下全部。
    与业务域无关（决策③ 拆域路由维持移除，Agent「业务域」标签不影响去向）。
    默认读系统配置 `wecom_customer_service_urls`（管理界面可改、多进程热同步）。
    """

    def __init__(self, urls: list[str] | None = None):
        raw_urls = config.wecom_customer_service_urls if urls is None else urls
        self.urls = [u.strip() for u in raw_urls if u and u.strip()]

    @staticmethod
    def _is_valid_url(url: str) -> bool:
        parsed = urlparse(url)
        return parsed.scheme == "https" and bool(parsed.netloc)

    def get_url(self, domain: str | None = None) -> str:
        """转人工入口 URL：在有效客服池内轮替取一个（domain 仅保留兼容，不影响去向）。"""
        valid_urls = [u for u in self.urls if self._is_valid_url(u)]
        if not valid_urls:
            return ""
        return _round_robin_pick(valid_urls)

    @property
    def is_configured(self) -> bool:
        return any(self._is_valid_url(u) for u in self.urls)


class KnowledgeHandoffService:
    async def create_and_open(self, user: Any, query: str, disposition: dict[str, Any] | None = None) -> dict:
        normalized_query = query.strip()
        query_hash = hashlib.sha256(normalized_query.encode()).hexdigest()
        disposition = disposition or {}
        # 拒答分类透传：域仅作统计标签入库（不决定转接去向）；URL 在客服池内轮替。
        domain = str(disposition.get("domain") or "").strip() or "unknown"
        refusal_type = str(disposition.get("type") or "").strip() or None
        refusal_reason = str(disposition.get("reason") or "").strip() or None
        customer_service = WeComCustomerService()
        service_url = customer_service.get_url()
        cutoff = utc_now_naive() - timedelta(minutes=5)

        async with pg_manager.get_async_session_context() as session:
            existing = await session.scalar(
                select(KnowledgeHandoff)
                .where(
                    KnowledgeHandoff.uid == user.uid,
                    KnowledgeHandoff.query_hash == query_hash,
                    KnowledgeHandoff.created_at >= cutoff,
                )
                .order_by(KnowledgeHandoff.id.desc())
            )
            if existing:
                return {
                    "id": existing.id,
                    "status": existing.status,
                    "customer_service_url": service_url,
                    "deduplicated": True,
                }

            ticket = KnowledgeHandoff(
                uid=user.uid,
                query=normalized_query,
                query_hash=query_hash,
                domain=domain,
                refusal_type=refusal_type,
                refusal_reason=refusal_reason,
                status="customer_service_ready" if service_url else "customer_service_not_configured",
            )
            session.add(ticket)
            await session.flush()
            if not service_url:
                ticket.notification_error = "No WECOM customer service URL configured"
            return {
                "id": ticket.id,
                "status": ticket.status,
                "customer_service_url": service_url,
                "deduplicated": False,
            }
