"""Create and notify Enterprise WeChat human-handoff requests."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import timedelta
from typing import Any, Callable

import httpx
from sqlalchemy import select

from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import KnowledgeHandoff
from yuxi.utils import logger
from yuxi.utils.datetime_utils import utc_now_naive


class WeComHandoffNotifier:
    def __init__(self, getenv: Callable[[str, str], str] = os.getenv):
        self.corp_id = getenv("WECOM_CORP_ID", "").strip()
        self.agent_id = getenv("WECOM_AGENT_ID", "").strip()
        self.app_secret = getenv("WECOM_APP_SECRET", "").strip()
        self.routing = self._load_routing(getenv("WECOM_HANDOFF_ROUTING", ""), getenv("WECOM_HANDOFF_TO_USERS", ""))

    @staticmethod
    def _load_routing(raw_routing: str, fallback_users: str) -> dict[str, dict[str, Any]]:
        if raw_routing:
            try:
                routing = json.loads(raw_routing)
                if isinstance(routing, dict):
                    return routing
            except json.JSONDecodeError:
                logger.warning("WECOM_HANDOFF_ROUTING is not valid JSON")
        return {"default": {"to_users": fallback_users, "keywords": []}}

    @property
    def is_configured(self) -> bool:
        return bool(self.corp_id and self.agent_id.isdigit() and self.app_secret and self.routing)

    def resolve_recipient(self, query: str) -> tuple[str, str]:
        normalized = query.lower()
        for question_type, route in self.routing.items():
            if question_type == "default" or not isinstance(route, dict):
                continue
            if any(str(keyword).lower() in normalized for keyword in route.get("keywords", [])):
                return question_type, str(route.get("to_users") or "")
        default_route = self.routing.get("default", {})
        return "default", str(default_route.get("to_users") or "")

    @staticmethod
    def message_payload(ticket: KnowledgeHandoff, user: Any, to_users: str, agent_id: str) -> dict:
        return {
            "touser": to_users,
            "msgtype": "text",
            "agentid": int(agent_id),
            "text": {"content": f"【知识库转人工】\n工单 #{ticket.id}\n提问人：{user.username} ({user.uid})\n问题：{ticket.query}\n请登录知识库系统处理。"},
            "safe": 0,
        }

    async def notify(self, ticket: KnowledgeHandoff, user: Any) -> tuple[bool, str | None]:
        if not self.is_configured:
            return False, "企业微信转人工通知尚未配置"
        question_type, to_users = self.resolve_recipient(ticket.query)
        if not to_users:
            return False, f"问题类型 {question_type} 未配置企业微信接收人"
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                token_response = await client.get("https://qyapi.weixin.qq.com/cgi-bin/gettoken", params={"corpid": self.corp_id, "corpsecret": self.app_secret})
                token_response.raise_for_status()
                token = token_response.json().get("access_token")
                if not token:
                    return False, "获取企业微信 access_token 失败"
                response = await client.post("https://qyapi.weixin.qq.com/cgi-bin/message/send", params={"access_token": token}, json=self.message_payload(ticket, user, to_users, self.agent_id))
                response.raise_for_status()
                result = response.json()
                if result.get("errcode") != 0:
                    return False, str(result.get("errmsg") or "企业微信发送消息失败")
        except httpx.HTTPError as exc:
            logger.warning("Enterprise WeChat handoff notification failed: %s", exc)
            return False, str(exc)
        return True, None


class KnowledgeHandoffService:
    async def create_and_notify(self, user: Any, query: str) -> dict:
        normalized_query = query.strip()
        query_hash = hashlib.sha256(normalized_query.encode()).hexdigest()
        cutoff = utc_now_naive() - timedelta(minutes=5)
        async with pg_manager.get_async_session_context() as session:
            existing = await session.scalar(select(KnowledgeHandoff).where(KnowledgeHandoff.uid == user.uid, KnowledgeHandoff.query_hash == query_hash, KnowledgeHandoff.created_at >= cutoff).order_by(KnowledgeHandoff.id.desc()))
            if existing:
                return {"id": existing.id, "status": existing.status, "deduplicated": True}
            ticket = KnowledgeHandoff(uid=user.uid, query=normalized_query, query_hash=query_hash, status="pending")
            session.add(ticket)
            await session.flush()
            notifier = WeComHandoffNotifier()
            notified, error = await notifier.notify(ticket, user)
            if notified:
                ticket.status = "notified"
                ticket.notified_at = utc_now_naive()
            else:
                ticket.status = "pending_configuration" if not notifier.is_configured else "notification_failed"
                ticket.notification_error = error
            question_type, _ = notifier.resolve_recipient(normalized_query)
            return {"id": ticket.id, "status": ticket.status, "question_type": question_type, "deduplicated": False}
