"""人工确认问答调优管理接口。"""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import exists, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from server.utils.auth_middleware import get_db, get_superadmin_user
from yuxi.repositories.curated_qa_candidate_repository import CuratedQACandidateRepository
from yuxi.repositories.curated_qa_repository import CuratedQARepository
from yuxi.services.curated_qa_service import CuratedQAService
from yuxi.services.udesk.summarize_service import has_substantive_qa
from yuxi.storage.postgres.models_business import MessageFeedback, User
from yuxi.storage.postgres.models_udesk import (
    CuratedQACandidate,
    UdeskConversation,
    UdeskMessage,
    UdeskSyncState,
)
from yuxi.utils.datetime_utils import format_utc_datetime, utc_now


curated_qa_dashboard = APIRouter(prefix="/dashboard", tags=["Dashboard"])


class SaveCuratedQARequest(BaseModel):
    answer: str = Field(..., min_length=1, max_length=20_000)


class QaPairsEnabledRequest(BaseModel):
    ids: list[int] = Field(..., min_length=1, max_length=200)
    enabled: bool


class QaPairsDeleteRequest(BaseModel):
    ids: list[int] = Field(..., min_length=1, max_length=200)


class QaPairUpdateRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=2000)
    answer: str = Field(..., min_length=1, max_length=20_000)


class QaCandidateAcceptRequest(BaseModel):
    agent_slug: str = Field(..., min_length=1, max_length=80)
    question: str | None = Field(default=None, max_length=2000)
    answer: str | None = Field(default=None, max_length=20_000)


@curated_qa_dashboard.get("/feedbacks/{feedback_id}/tuning-context")
async def get_feedback_tuning_context(
    feedback_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_superadmin_user),
):
    del current_user
    try:
        context = await CuratedQAService().get_feedback_context(db, feedback_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if context is None:
        raise HTTPException(status_code=404, detail="反馈记录不存在")
    return {"item": context}


@curated_qa_dashboard.put("/feedbacks/{feedback_id}/qa-pair")
async def save_feedback_qa_pair(
    feedback_id: int,
    payload: SaveCuratedQARequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_superadmin_user),
):
    try:
        item = await CuratedQAService().save_from_feedback(
            db,
            feedback_id=feedback_id,
            answer=payload.answer,
            operator_uid=str(current_user.uid),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if item is None:
        raise HTTPException(status_code=404, detail="反馈记录不存在")
    # 从反馈调优落库即视为已处理，幂等；行内手动标记走 PATCH status 单独管理
    await db.execute(update(MessageFeedback).where(MessageFeedback.id == feedback_id).values(status="processed"))
    await db.commit()
    return {"item": item}


@curated_qa_dashboard.get("/qa-pairs")
async def list_qa_pairs(
    agent_slug: str | None = None,
    source_type: str | None = None,
    enabled: bool | None = None,
    keyword: str | None = None,
    limit: int = 20,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_superadmin_user),
):
    """分页查询全部人工问答对（超级管理员权限），支持智能体/来源/启用态/关键词筛选。"""
    del current_user
    total, items = await CuratedQARepository(db).list_all(
        agent_slug=agent_slug,
        source_type=source_type,
        enabled=enabled,
        keyword=keyword,
        limit=limit,
        offset=offset,
    )
    return {"total": total, "items": [item.to_dict() for item in items]}


@curated_qa_dashboard.patch("/qa-pairs/enabled")
async def set_qa_pairs_enabled(
    payload: QaPairsEnabledRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_superadmin_user),
):
    """批量启用/停用问答对；停用后不再参与精确命中与语义召回。"""
    updated = await CuratedQARepository(db).set_enabled(
        payload.ids, enabled=payload.enabled, operator_uid=str(current_user.uid)
    )
    await db.commit()
    return {"updated": updated}


@curated_qa_dashboard.patch("/qa-pairs/{qa_id}")
async def update_qa_pair(
    qa_id: int,
    payload: QaPairUpdateRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_superadmin_user),
):
    """修改问答对内容；问题变更会重算精确匹配键并使语义向量失效（下次召回懒回填）。"""
    try:
        item = await CuratedQARepository(db).update_content(
            qa_id, question=payload.question, answer=payload.answer, operator_uid=str(current_user.uid)
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if item is None:
        raise HTTPException(status_code=404, detail="问答对不存在")
    await db.commit()
    return {"item": item.to_dict()}


@curated_qa_dashboard.post("/qa-pairs/batch-delete")
async def delete_qa_pairs(
    payload: QaPairsDeleteRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_superadmin_user),
):
    """批量删除问答对；不可恢复，前端须二次确认。"""
    del current_user
    deleted = await CuratedQARepository(db).delete(payload.ids)
    await db.commit()
    return {"deleted": deleted}


@curated_qa_dashboard.get("/qa-candidates")
async def list_qa_candidates(
    review_status: str | None = None,
    dedup_status: str | None = None,
    domain: str | None = None,
    keyword: str | None = None,
    limit: int = 20,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_superadmin_user),
):
    """客服记录候选知识审核页：按审核态/查重态/业务域/关键词筛选。"""
    del current_user
    total, items = await CuratedQACandidateRepository(db).list_all(
        review_status=review_status,
        dedup_status=dedup_status,
        domain=domain,
        keyword=keyword,
        limit=limit,
        offset=offset,
    )
    return {"total": total, "items": [item.to_dict() for item in items]}


@curated_qa_dashboard.get("/qa-candidates/{candidate_id}/context")
async def get_qa_candidate_context(
    candidate_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_superadmin_user),
):
    """候选来源会话的脱敏消息（C7 审核核对 evidence_quote 用）；会话按 TTL 清理后 404。"""
    del current_user
    candidate = await CuratedQACandidateRepository(db).get(candidate_id)
    if candidate is None:
        raise HTTPException(status_code=404, detail="候选记录不存在")
    conversation, messages = await _load_conversation_detail(db, candidate.source_conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="原始会话已按保留期清理，无法查看上下文")
    return {"conversation": conversation.to_dict(), "messages": [m.to_dict() for m in messages]}


@curated_qa_dashboard.post("/qa-candidates/{candidate_id}/accept")
async def accept_qa_candidate(
    candidate_id: int,
    payload: QaCandidateAcceptRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_superadmin_user),
):
    """采纳入库（C6）：写入 curated_qa_pairs 但 enabled=False，需在问答对管理页启用后生效。"""
    candidate_repo = CuratedQACandidateRepository(db)
    candidate = await candidate_repo.get(candidate_id)
    if candidate is None:
        raise HTTPException(status_code=404, detail="候选记录不存在")
    try:
        qa_pair = await CuratedQARepository(db).upsert_from_candidate(
            agent_slug=payload.agent_slug,
            question=payload.question or candidate.question,
            answer=payload.answer or candidate.answer,
            operator_uid=str(current_user.uid),
            source_conversation_id=candidate.source_conversation_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await candidate_repo.set_review([candidate.id], review_status="accepted", reviewed_by=str(current_user.uid))
    await db.commit()
    return {"item": qa_pair.to_dict()}


@curated_qa_dashboard.delete("/qa-candidates/{candidate_id}")
async def delete_qa_candidate(
    candidate_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_superadmin_user),
):
    """删除候选：不可恢复，前端须二次确认（与问答对管理的删除同一语义）。"""
    del current_user
    deleted = await CuratedQACandidateRepository(db).delete([candidate_id])
    await db.commit()
    if not deleted:
        raise HTTPException(status_code=404, detail="候选记录不存在")
    return {"deleted": deleted}


@curated_qa_dashboard.get("/udesk-status")
async def get_udesk_status(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_superadmin_user),
):
    """Udesk 配置生效概览（含每项来源）+ 拉取/总结运行状态 + 累计量，供设置页/审核页只读展示。

    设置页表单只反映设置页自己的快照，而实际生效值是「设置页 + 服务器 .env」合并结果；
    不回传令牌值，只回传是否已配置。
    """
    del current_user
    from yuxi.services.udesk.config import UdeskConfig, next_pull_run_at

    config = UdeskConfig()
    counts = (
        await db.execute(
            select(
                select(func.count()).select_from(UdeskConversation).scalar_subquery(),
                select(func.count()).select_from(UdeskMessage).scalar_subquery(),
                select(func.count()).select_from(CuratedQACandidate).scalar_subquery(),
                select(func.count())
                .select_from(CuratedQACandidate)
                .where(CuratedQACandidate.review_status == "pending")
                .scalar_subquery(),
                select(func.count())
                .select_from(UdeskConversation)
                .where(UdeskConversation.summarized_at.is_(None))
                .scalar_subquery(),
            )
        )
    ).one()
    conversations, messages, candidates, candidates_pending, summarize_pending = (int(value or 0) for value in counts)
    return {
        **config.describe(),
        "counts": {
            "conversations": conversations,
            "messages": messages,
            "candidates": candidates,
            # 采纳只改 review_status、不删行，而审核列表默认只看 pending，故累计的
            # candidates 与列表条数天然不等——两个口径都给出，避免对不上。
            # （删除是真删行，已删的不会再计入累计。）
            "candidates_pending": candidates_pending,
        },
        "pull": {
            **await _load_pull_state(db),
            # 凭证不齐时 cron 是空操作，谈不上「下次拉取」，置 None 由前端隐藏
            "next_run_at": format_utc_datetime(next_pull_run_at()) if config.ready else None,
        },
        "summarize": {
            "done": conversations - summarize_pending,
            "total": conversations,
            "pending": summarize_pending,
            **await _load_summarize_state(db),
        },
    }


@curated_qa_dashboard.get("/udesk-conversations")
async def list_udesk_conversations(
    keyword: str | None = None,
    has_candidates: bool | None = None,
    limit: int = 20,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_superadmin_user),
):
    """客服会话只读浏览：含被确定性筛掉的寒暄/未答复会话。

    候选审核页的「查看上下文」只能从候选行进入，没产出候选的会话在界面上完全
    不可见——而那正是「这条为什么没出候选」最需要看的部分。消息已在拉取侧脱敏
    （C3），本页只读，不提供任何写操作。
    """
    del current_user
    total, items = await _load_conversation_page(
        db, keyword=keyword, has_candidates=has_candidates, limit=limit, offset=offset
    )
    return {"total": total, "items": items}


@curated_qa_dashboard.get("/udesk-conversations/{conversation_id}")
async def get_udesk_conversation(
    conversation_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_superadmin_user),
):
    """单个会话的脱敏消息（会话按保留期清理后 404）。"""
    del current_user
    conversation, messages = await _load_conversation_detail(db, conversation_id)
    if conversation is None:
        raise HTTPException(status_code=404, detail="会话不存在或已按保留期清理")
    return {"conversation": conversation.to_dict(), "messages": [m.to_dict() for m in messages]}


async def _load_pull_state(db: AsyncSession) -> dict[str, Any]:
    """拉取运行状态。running 由租约是否仍在有效期内判定（进程崩溃后自然过期）。

    progress_done/progress_total 是本轮进度，随时可在页面上长出来；`total` 是
    「列表搜索已发现的会话数」，分母会随翻页增长，故它是处理次数口径。
    """
    state = (await db.execute(select(UdeskSyncState).where(UdeskSyncState.id == 1))).scalar_one_or_none()
    if state is None:  # 单行表由建表语句初始化，缺失时按「从未拉取」展示
        return {
            "running": False,
            "done": 0,
            "total": 0,
            "last_run_at": None,
            "last_run_status": None,
            "last_error": None,
            "last_run_conversations": 0,
            "last_run_messages": 0,
        }
    return {
        # lease_expires_at 是 TIMESTAMPTZ，读回来是 aware；必须与 aware 的 utc_now()
        # 比较。用 utc_now_naive() 会抛 TypeError，而租约只在拉取进行中非空，
        # 于是状态接口恰好在「最需要看进度」的那几分钟里 500。
        "running": bool(state.lease_expires_at and state.lease_expires_at > utc_now()),
        "done": int(state.progress_done or 0),
        "total": int(state.progress_total or 0),
        "last_run_at": format_utc_datetime(state.last_run_at),
        "last_run_status": state.last_run_status,
        "last_error": state.last_error,
        "last_run_conversations": state.last_run_conversations,
        "last_run_messages": state.last_run_messages,
    }


async def _load_summarize_state(db: AsyncSession) -> dict[str, Any]:
    """总结链路的运行状态；进度不另设计数器，由会话总数与待总结数现算。"""
    state = (await db.execute(select(UdeskSyncState).where(UdeskSyncState.id == 1))).scalar_one_or_none()
    if state is None:
        return {
            "running": False,
            "last_run_at": None,
            "last_run_status": None,
            "last_error": None,
            "last_candidates": None,
        }
    return {
        "running": bool(state.summarize_lease_expires_at and state.summarize_lease_expires_at > utc_now()),
        "last_run_at": format_utc_datetime(state.summarize_last_run_at),
        "last_run_status": state.summarize_status,
        "last_error": state.summarize_last_error,
        # None = 还没跑过任何一轮；0 是有效值（跑了但没产出新候选），前端要区分
        "last_candidates": state.summarize_last_candidates,
    }


async def _load_conversation_page(
    db: AsyncSession,
    *,
    keyword: str | None,
    has_candidates: bool | None,
    limit: int,
    offset: int,
) -> tuple[int, list[dict[str, Any]]]:
    """会话列表页，每行带上排查「为什么没出候选」的四个维度。

    `eligible` 直接调 `has_substantive_qa`（与总结链路同一个函数），不在 SQL 里
    重写筛人规则——规则一旦变成两处实现，页面显示的「过筛」就会和实际跑的对不上。
    度量按页算（页面 ≤200 行），故先取会话再回填，不做相关子查询。
    """
    conditions = []
    keyword = str(keyword or "").strip()
    if keyword:
        conditions.append(UdeskConversation.conversation_id.ilike(f"%{keyword}%"))
    if has_candidates is not None:
        candidate_exists = exists().where(
            CuratedQACandidate.source_conversation_id == UdeskConversation.conversation_id
        )
        conditions.append(candidate_exists if has_candidates else ~candidate_exists)

    base = select(UdeskConversation)
    if conditions:
        base = base.where(*conditions)
    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar() or 0
    conversations = (
        (
            await db.execute(
                base.order_by(UdeskConversation.started_at.desc().nullslast(), UdeskConversation.id.desc())
                .limit(max(1, min(int(limit), 200)))
                .offset(max(0, int(offset)))
            )
        )
        .scalars()
        .all()
    )
    if not conversations:
        return int(total), []

    conversation_ids = [item.conversation_id for item in conversations]
    messages: dict[str, list[dict[str, str]]] = {}
    for conversation_id, role, content in (
        await db.execute(
            select(UdeskMessage.conversation_id, UdeskMessage.role, UdeskMessage.content).where(
                UdeskMessage.conversation_id.in_(conversation_ids)
            )
        )
    ).all():
        messages.setdefault(conversation_id, []).append({"role": role, "content": content})
    candidate_counts = dict(
        (
            await db.execute(
                select(CuratedQACandidate.source_conversation_id, func.count())
                .where(CuratedQACandidate.source_conversation_id.in_(conversation_ids))
                .group_by(CuratedQACandidate.source_conversation_id)
            )
        ).all()
    )
    return int(total), [
        {
            **conversation.to_dict(),
            "message_count": len(messages.get(conversation.conversation_id, [])),
            "customer_message_count": sum(
                1 for item in messages.get(conversation.conversation_id, []) if item["role"] == "customer"
            ),
            "eligible": has_substantive_qa(messages.get(conversation.conversation_id, [])),
            "candidate_count": int(candidate_counts.get(conversation.conversation_id, 0)),
        }
        for conversation in conversations
    ]


async def _load_conversation_detail(
    db: AsyncSession, conversation_id: str
) -> tuple[UdeskConversation | None, list[UdeskMessage]]:
    """会话 + 其消息（按发送时间升序）；审核页「查看上下文」与只读浏览页共用。"""
    conversation = (
        await db.execute(select(UdeskConversation).where(UdeskConversation.conversation_id == conversation_id))
    ).scalar_one_or_none()
    if conversation is None:
        return None, []
    messages = (
        (
            await db.execute(
                select(UdeskMessage)
                .where(UdeskMessage.conversation_id == conversation_id)
                .order_by(UdeskMessage.sent_at.asc().nullslast(), UdeskMessage.id.asc())
            )
        )
        .scalars()
        .all()
    )
    return conversation, list(messages)


@curated_qa_dashboard.post("/udesk-pull")
async def trigger_udesk_pull(
    current_user: User = Depends(get_superadmin_user),
):
    """手动触发 Udesk 增量拉取（异步经 arq 队列执行，不在请求内等待）。

    凭证不齐时直接返回 422 并说明缺什么，不排空跑任务；已在跑（cron 或上次手动）由
    DB 租约兜底，该轮返回 skipped_lease，不产生重复数据。
    """
    del current_user
    from yuxi.services.run_queue_service import get_arq_pool
    from yuxi.services.udesk.config import UdeskConfig

    config = UdeskConfig()
    if not config.ready:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "udesk_not_ready",
                "message": "Udesk 配置不完整，无法拉取",
                "missing_fields": config.missing_fields,
            },
        )

    queue = await get_arq_pool()
    # 固定 job_id：连点在上一轮还在队列/执行中时直接 409。此前用随机 id，arq 从不去重，
    # 连点会排出一串无用任务——真正挡住重复拉取的是服务里的 DB 租约，那些任务只会各自
    # 空跑一遍（还都会去动状态行）。与「生成候选问答」的固定 job_id 保持一致。
    job = await queue.enqueue_job("run_scheduled_pull", _job_id="udesk-pull:manual")
    if job is None:
        raise HTTPException(status_code=409, detail="已有拉取任务在队列中，请稍候")
    return {"queued": True, "job_id": job.job_id}


@curated_qa_dashboard.post("/udesk-summarize")
async def trigger_udesk_summarize(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_superadmin_user),
):
    """手动触发已拉取会话的 LLM 总结（异步经 arq 队列执行，与每小时 cron 同一入口）。

    两步手动流程的第二步：先「立即拉取」入库，再点本接口生成候选问答。
    无待总结会话或总结模型未配置时返回 422，不排空跑任务；固定 job_id 使
    重复点击在上一轮还在队列/执行中时直接 409。
    """
    del current_user
    from yuxi.config.app import config as runtime_config
    from yuxi.services.run_queue_service import get_arq_pool

    model_spec = (runtime_config.udesk_summarize_model or runtime_config.default_model or "").strip()
    if not model_spec:
        raise HTTPException(status_code=422, detail="未配置总结模型（udesk_summarize_model / default_model），无法总结")

    pending = (
        await db.execute(
            select(func.count()).select_from(UdeskConversation).where(UdeskConversation.summarized_at.is_(None))
        )
    ).scalar() or 0
    if not pending:
        raise HTTPException(status_code=422, detail="没有待总结的会话，请先「立即拉取」客服记录")

    queue = await get_arq_pool()
    job = await queue.enqueue_job("run_scheduled_summarize", _job_id="udesk-summarize:manual")
    if job is None:
        raise HTTPException(status_code=409, detail="已有总结任务在队列中，请稍候")
    return {"queued": True, "job_id": job.job_id, "pending": int(pending)}
