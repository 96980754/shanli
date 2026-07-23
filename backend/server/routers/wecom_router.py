"""Enterprise WeChat self-built application callback entry for P0."""

from __future__ import annotations

import os
import time
import xml.etree.ElementTree as ET

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse, Response
from sqlalchemy import select

from yuxi.services.global_knowledge_search_service import GlobalKnowledgeSearchService
from yuxi.services.wecom_crypto import WeComCrypto
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_business import User

wecom = APIRouter(prefix="/wecom", tags=["wecom"])
search_service = GlobalKnowledgeSearchService()


def _crypto() -> WeComCrypto:
    try:
        return WeComCrypto(
            os.getenv("WECOM_TOKEN", ""),
            os.getenv("WECOM_ENCODING_AES_KEY", ""),
            os.getenv("WECOM_CORP_ID", ""),
        )
    except ValueError as exc:
        raise HTTPException(status_code=503, detail="Enterprise WeChat callback is not configured") from exc


def _xml_value(root: ET.Element, name: str) -> str:
    return (root.findtext(name) or "").strip()


def _reply_text(content: str) -> str:
    return f"<xml><MsgType><![CDATA[text]]></MsgType><Content><![CDATA[{content}]]></Content></xml>"


def _format_results(results: list[dict]) -> str:
    if not results:
        return "未找到可访问知识库中的相关内容。"
    lines = ["AI知识库检索结果："]
    for index, result in enumerate(results[:3], start=1):
        content = str(result.get("content") or result.get("text") or "").strip().replace("\n", " ")
        lines.append(f"{index}. [{result['kb_name']}] {content[:180]}")
    return "\n".join(lines)[:1900]


@wecom.get("/callback")
async def verify_callback(
    msg_signature: str = Query(...), timestamp: str = Query(...), nonce: str = Query(...), echostr: str = Query(...)
):
    crypto = _crypto()
    if not crypto.verify_signature(msg_signature, timestamp, nonce, echostr):
        raise HTTPException(status_code=403, detail="Invalid Enterprise WeChat signature")
    return PlainTextResponse(crypto.decrypt(echostr))


@wecom.post("/callback")
async def receive_message(
    request: Request,
    msg_signature: str = Query(...),
    timestamp: str = Query(...),
    nonce: str = Query(...),
):
    crypto = _crypto()
    root = ET.fromstring(await request.body())
    encrypted = _xml_value(root, "Encrypt")
    if not crypto.verify_signature(msg_signature, timestamp, nonce, encrypted):
        raise HTTPException(status_code=403, detail="Invalid Enterprise WeChat signature")

    message = ET.fromstring(crypto.decrypt(encrypted))
    if _xml_value(message, "MsgType") != "text":
        return Response(status_code=204)
    user_id = _xml_value(message, "FromUserName")
    query = _xml_value(message, "Content")
    async with pg_manager.get_async_session_context() as session:
        user = (await session.execute(select(User).where(User.uid == user_id, User.is_deleted == 0))).scalar_one_or_none()
    if user is None:
        reply = "你的企业微信 UserID 尚未绑定到系统账号，请联系管理员将本系统 UID 设置为该 UserID。"
    else:
        reply = _format_results(await search_service.search(user, query, limit=3))

    encrypted_reply = crypto.encrypt(_reply_text(reply))
    reply_nonce = nonce or str(int(time.time()))
    signature = crypto.signature(timestamp, reply_nonce, encrypted_reply)
    xml = f"<xml><Encrypt><![CDATA[{encrypted_reply}]]></Encrypt><MsgSignature><![CDATA[{signature}]]></MsgSignature><TimeStamp>{timestamp}</TimeStamp><Nonce><![CDATA[{reply_nonce}]]></Nonce></xml>"
    return Response(content=xml, media_type="application/xml")
