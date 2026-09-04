"""Create human-handoff records and return the Enterprise WeChat customer-service entry."""

from __future__ import annotations

import hashlib
import threading
from datetime import timedelta
from typing import Any
from urllib.parse import urlparse

from pydantic import ValidationError
from sqlalchemy import select
from yuxi.config.app import BusinessLine, CustomerServiceEntry, config, sanitize_business_domain
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


def _validated_rows(raw_rows: Any, model: type) -> list[Any]:
    """把配置行（dict 或已校验模型）容错解析为模型列表——非法行跳过，坏行不阻断转接。"""
    rows: list[Any] = []
    for row in raw_rows or []:
        if isinstance(row, model):
            rows.append(row)
        elif isinstance(row, dict):
            try:
                rows.append(model.model_validate(row))
            except ValidationError:
                continue
    return rows


class WeComCustomerService:
    """按业务线解析候选客服条目、在其 URL 池内轮替返回转人工入口。

    去向规则（绑定即按线转接，见 docs/vibe/2026-09-03-客服接入设置.md）：
    1. domain 命中的业务线若绑定了客服条目 → 用该线绑定集合；
    2. 否则（unknown / 该线未绑定 / 绑定全部失效）→ 通用客服兜底链：
       a. code=kefu 的业务线若有绑定 → 用其集合；
       b. 否则 → 未被任何业务线绑定的客服条目（默认池）；
       兜底集合仍为空 → 转人工不可用（返回空串，与「池为空」旧行为一致）。

    默认读系统配置 wecom_customer_services + business_lines（管理界面可改、多进程热同步）。
    测试可注入 entries / business_lines 构造，避免触碰全局配置。
    """

    def __init__(self, *, entries: list[Any] | None = None, business_lines: list[Any] | None = None):
        self._entries = _validated_rows(
            config.wecom_customer_services if entries is None else entries, CustomerServiceEntry
        )
        self._lines = _validated_rows(
            config.business_lines if business_lines is None else business_lines, BusinessLine
        )

    def get_url(self, domain: str | None = None) -> str:
        """转人工入口 URL：按 domain 解析候选客服、在其 URL 并集内轮替取一个。"""
        urls = self._candidate_urls(self._resolve_candidate_ids(domain))
        if not urls:
            return ""
        return _round_robin_pick(urls)

    @property
    def is_configured(self) -> bool:
        """配置中是否存在任一可用客服入口 URL（粗粒度可用性标记，不区分去向）。"""
        return any(
            self._is_valid_url(url) for entry in self._entries for url in entry.urls
        )

    # ---- 兜底链解析（按线转接 + 通用客服兜底） ----

    def _resolve_candidate_ids(self, domain: str | None) -> list[str]:
        """按兜底链解析候选客服 id 集合：命中线的绑定 → kefu 绑定 → 未被认领的默认池。"""
        line = self._line_by_code(str(domain or "").strip())
        if line:
            bound = self._existing_bindings(line)
            if bound:
                return bound
        kefu = self._line_by_code("kefu")
        if kefu:
            bound = self._existing_bindings(kefu)
            if bound:
                return bound
        claimed = {cid for ln in self._lines for cid in ln.customer_service_ids}
        return [entry.id for entry in self._entries if entry.id not in claimed]

    def _line_by_code(self, code: str) -> BusinessLine | None:
        if not code:
            return None
        return next((ln for ln in self._lines if ln.code == code), None)

    def _existing_bindings(self, line: BusinessLine) -> list[str]:
        # 只保留真实存在的客服条目；手改配置引用已删/不存在 id 时按未绑定走兜底，不 500（验收边界 4）。
        known = {entry.id for entry in self._entries}
        return [cid for cid in line.customer_service_ids if cid in known]

    def _candidate_urls(self, ids: list[str]) -> list[str]:
        """把候选客服 id 摊平成保序去重的有效 URL 并集（一个客服多账号时轮替扛量）。"""
        by_id = {entry.id: entry for entry in self._entries}
        urls: list[str] = []
        for cid in ids:
            entry = by_id.get(cid)
            if entry is None:
                continue
            for url in entry.urls:
                if self._is_valid_url(url) and url not in urls:
                    urls.append(url)
        return urls

    @staticmethod
    def _is_valid_url(url: str) -> bool:
        parsed = urlparse(url)
        return parsed.scheme == "https" and bool(parsed.netloc)


class KnowledgeHandoffService:
    async def create_and_open(self, user: Any, query: str, disposition: dict[str, Any] | None = None) -> dict:
        normalized_query = query.strip()
        query_hash = hashlib.sha256(normalized_query.encode()).hexdigest()
        disposition = disposition or {}
        # domain 决定转接去向（按线转接 + 通用客服兜底），同时作统计标签入库；
        # 只认配置业务线清单内 code，非清单值回退 unknown（防御前端直传）。
        domain = sanitize_business_domain(disposition.get("domain"))
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
                ticket.notification_error = "No WECOM customer service URL configured"
            return {
                "id": ticket.id,
                "status": ticket.status,
                "customer_service_url": service_url,
                "deduplicated": False,
            }
