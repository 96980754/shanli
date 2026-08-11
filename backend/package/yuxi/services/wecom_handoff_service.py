"""Create human-handoff records and return the Enterprise WeChat customer-service entry."""

from __future__ import annotations

import hashlib
import os
from datetime import timedelta
from collections.abc import Callable
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import select

from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import KnowledgeHandoff
from yuxi.utils.datetime_utils import utc_now_naive


class WeComCustomerService:
    """The URL is generated in Enterprise WeChat's Customer Service console.

    Opening this URL hands the user to the configured customer-service account.
    No self-built-app message is sent to an internal employee.
    """

    def __init__(self, getenv: Callable[[str, str], str] = os.getenv):
        self.service_url = getenv("WECOM_CUSTOMER_SERVICE_URL", "").strip()

    @property
    def is_configured(self) -> bool:
        parsed = urlparse(self.service_url)
        return parsed.scheme == "https" and bool(parsed.netloc)


class KnowledgeHandoffService:
    async def create_and_open(self, user: Any, query: str) -> dict:
        normalized_query = query.strip()
        query_hash = hashlib.sha256(normalized_query.encode()).hexdigest()
        customer_service = WeComCustomerService()
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
                    "customer_service_url": customer_service.service_url if customer_service.is_configured else None,
                    "deduplicated": True,
                }

            ticket = KnowledgeHandoff(
                uid=user.uid,
                query=normalized_query,
                query_hash=query_hash,
                status=(
                    "customer_service_ready"
                    if customer_service.is_configured
                    else "customer_service_not_configured"
                ),
            )
            session.add(ticket)
            await session.flush()
            if not customer_service.is_configured:
                ticket.notification_error = "WECOM_CUSTOMER_SERVICE_URL is not configured"
            return {
                "id": ticket.id,
                "status": ticket.status,
                "customer_service_url": customer_service.service_url if customer_service.is_configured else None,
                "deduplicated": False,
            }
