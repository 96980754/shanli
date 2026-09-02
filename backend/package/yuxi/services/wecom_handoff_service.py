"""Create human-handoff records and return the Enterprise WeChat customer-service entry."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import timedelta
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import select
from yuxi.config.app import config
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import KnowledgeHandoff
from yuxi.utils.datetime_utils import utc_now_naive


class WeComCustomerService:
    """按业务域返回企微客服入口 URL。

    默认读系统配置（管理界面可改、多进程热同步）：wecom_customer_service_urls 按域优先，
    该域未配置时回退全局 wecom_customer_service_url。两者都未配置则返回空串。
    getenv 参数仅供测试注入（保持原环境变量语义）。
    """

    def __init__(self, getenv: Callable[[str, str], str] | None = None):
        if getenv is not None:
            self.global_url = getenv("WECOM_CUSTOMER_SERVICE_URL", "").strip()
            self._domain_urls = self._parse_domain_urls(getenv("WECOM_CUSTOMER_SERVICE_URLS", ""))
        else:
            self.global_url = (config.wecom_customer_service_url or "").strip()
            self._domain_urls = dict(config.wecom_customer_service_urls or {})

    @staticmethod
    def _parse_domain_urls(raw: str) -> dict[str, str]:
        if not raw.strip():
            return {}
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        if not isinstance(payload, dict):
            return {}
        return {str(key).strip(): str(value).strip() for key, value in payload.items() if str(value).strip()}

    @staticmethod
    def _is_valid_url(url: str) -> bool:
        parsed = urlparse(url)
        return parsed.scheme == "https" and bool(parsed.netloc)

    def get_url(self, domain: str | None = None) -> str:
        domain_url = self._domain_urls.get(domain, "") if domain else ""
        if self._is_valid_url(domain_url):
            return domain_url
        return self.global_url if self._is_valid_url(self.global_url) else ""

    @property
    def is_configured(self) -> bool:
        return bool(self.get_url() or any(self._is_valid_url(url) for url in self._domain_urls.values()))


class KnowledgeHandoffService:
    async def create_and_open(self, user: Any, query: str, disposition: dict[str, Any] | None = None) -> dict:
        normalized_query = query.strip()
        query_hash = hashlib.sha256(normalized_query.encode()).hexdigest()
        disposition = disposition or {}
        # 拒答分类透传：域决定转给哪个人工组，类型/原因入库供运营统计。
        domain = str(disposition.get("domain") or "").strip() or "unknown"
        refusal_type = str(disposition.get("type") or "").strip() or None
        refusal_reason = str(disposition.get("reason") or "").strip() or None
        customer_service = WeComCustomerService()
        service_url = customer_service.get_url(domain)
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
                ticket.notification_error = "No WECOM customer service URL configured for domain: " + domain
            return {
                "id": ticket.id,
                "status": ticket.status,
                "customer_service_url": service_url,
                "deduplicated": False,
            }
