"""Udesk 客服记录 → LLM 结构化候选知识（知识回流链路二第③步）。

设计见 docs/vibe/udesk/2026-09-14-udesk-to-knowledge-pipeline-design.md §四③：
- **先筛再总结**（P5-1）：确定性规则过滤寒暄/纯转接/未答复会话，不值得送 LLM 的
  会话直接标记 summarized_at，成本只花在有实质问答的会话上。
- **LLM 结构化**（P5-2，C7）：一次会话可产出多条候选；answer 允许整理语言但
  禁止改动事实——数字/链接/型号必须原文存在于会话记录，evidence_quote 必须
  逐字摘自会话记录（核对时忽略空白差异）；违反即丢弃该条候选，不放行到审核页。
- **幂等**（D17）：候选写入 ON CONFLICT (source_conversation_id, question_hash)
  DO NOTHING；问题归一化口径与 curated_qa_repository 一致，采纳时可直接迁移。
- **断点续跑**：每会话处理完成即打 summarized_at 并提交。**瞬时**失败（网络/超时/DB）
  不打标，下一轮重试（重试产生的重复候选由唯一键吸收）；**确定性**失败
  （模型输出解析不了）重试同样的输入不会自愈，打标跳过，理由见
  UnparsableModelOutputError。
- 入库内容已在拉取侧脱敏（C3/C1），送模型的不含客户标识。
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import bindparam, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.repositories.curated_qa_repository import hash_qa_question, normalize_qa_question
from yuxi.storage.postgres.models_udesk import CuratedQACandidate, UdeskConversation, UdeskMessage
from yuxi.utils import logger
from yuxi.utils.datetime_utils import utc_now

# 长度口径与文档 QA 生成保持一致（config.document_qa_*_max_chars）
QUESTION_MAX_CHARS = 300
ANSWER_MAX_CHARS = 2000
# 实质性问答的最短内容：短于此视为寒暄/表情/纯转接（P5-1 筛选）
MIN_SUBSTANTIVE_CHARS = 5
# 总结租约时长：一轮要逐会话调 LLM，比拉取慢得多，故给足余量
SUMMARIZE_LEASE_MINUTES = 30

# C7 事实核验：答案里的数字/链接/型号必须能在会话记录中找到原文
# （口径同 knowledge/document_qa._assert_fact_subset，此处面向会话记录实现）
_NUMBER_RE = re.compile(r"\b\d+(?:[.,/_-]\d+)*\b")
_URL_RE = re.compile(r"https?://[^\s)>]+")
_MODEL_RE = re.compile(r"\b[A-Z][A-Z0-9]*(?:[-_/][A-Za-z0-9.]+)+\b")

GenerateFn = Callable[[list[dict[str, str]]], Awaitable[str]]


class UnparsableModelOutputError(ValueError):
    """模型输出解析不出候选数组——确定性失败，重试没有意义。

    与网络/超时/DB 这类瞬时故障分开：那类重试会自愈，这类不会。而重试的代价是
    真金白银——模型已经答完，token 已经花掉，结果整条丢弃。若照旧每小时重试，
    同一条会话会按 started_at 排在队首反复占用批次额度，占满后新会话再也进不来。
    """


def _without_whitespace(value: str) -> str:
    return re.sub(r"\s+", "", value)


def has_substantive_qa(messages: list[dict[str, Any]]) -> bool:
    """确定性筛选（P5-1）：至少一条实质客户提问 + 一条实质答复。

    消息为脱敏后的 {role, content}；客户侧与答复侧（机器人/人工/系统）分别
    要求 ≥MIN_SUBSTANTIVE_CHARS 字，过滤寒暄、致谢、纯转接与未答复会话。
    """
    asked = any(
        m.get("role") == "customer" and len(str(m.get("content") or "").strip()) >= MIN_SUBSTANTIVE_CHARS
        for m in messages
    )
    answered = any(
        m.get("role") != "customer" and len(str(m.get("content") or "").strip()) >= MIN_SUBSTANTIVE_CHARS
        for m in messages
    )
    return asked and answered


def build_prompt(
    conversation: dict[str, Any],
    messages: list[dict[str, Any]],
    *,
    known_domains: list[str],
    max_pairs: int,
) -> list[dict[str, str]]:
    """组装结构化提示词。messages 为脱敏后的 {role, content}。"""
    system = (
        "你是客服对话知识整理助手，从客服对话记录中提取可沉淀进知识库的问答对。规则：\n"
        "1. 只提取「客户提出具体问题、客服给出实质性解答」的内容；寒暄、致谢、投诉情绪、"
        "纯转接不提取。\n"
        "2. 一次对话可能包含多个独立问题，逐个提取，最多 "
        f"{max_pairs} 条。\n"
        "3. question 与 answer 一律用简体中文书写：会话原文是英文或其他语言时翻译成中文。"
        "禁止改动事实——翻译只改语言：数字、期限、型号、链接等必须与客服原话一致，不得把"
        "「大概/可能支持」改写成「支持」，不得补充对话中没有的信息。\n"
        "4. evidence_quote 必须逐字摘抄客服答复原话中支撑该答案的片段（保持原文语言，"
        "不得翻译），供人工核对——译文无法与原话比对，会被校验层判为不合规而整条丢弃。\n"
        f"5. domain 只能从给定代码中选择：{json.dumps(known_domains, ensure_ascii=False)}；"
        "无法判断则填 null。\n"
        "6. confidence 为 0-1 的小数；客服答复含糊、不完整或前后矛盾时在 ambiguity_note 说明。\n"
        '7. 只返回 JSON 对象 {"candidates": [...]},不要输出推理过程。\n'
        "每项字段：question、answer、domain、confidence、evidence_quote、ambiguity_note。"
    )
    user = json.dumps(
        {
            "conversation": {
                "conversation_id": conversation.get("conversation_id"),
                "started_at": conversation.get("started_at"),
                "ended_at": conversation.get("ended_at"),
            },
            "messages": [{"role": m.get("role"), "content": str(m.get("content") or "")} for m in messages],
        },
        ensure_ascii=False,
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def extract_candidates(raw_text: str) -> list[dict[str, Any]]:
    """从模型输出解析候选列表；容忍 ```json 围栏与外层包裹。"""
    payload = raw_text.strip()
    if payload.startswith("```"):
        payload = re.sub(r"^```(?:json)?\s*", "", payload, flags=re.IGNORECASE)
        payload = re.sub(r"\s*```$", "", payload)
    try:
        parsed = json.loads(payload)
    except (TypeError, ValueError) as exc:
        raise UnparsableModelOutputError("模型输出不是有效 JSON") from exc
    if isinstance(parsed, dict):
        parsed = parsed.get("candidates")
    if not isinstance(parsed, list):
        raise UnparsableModelOutputError("模型输出必须包含 candidates 数组")
    return [item for item in parsed if isinstance(item, dict)]


def validate_candidate(
    item: dict[str, Any],
    messages: list[dict[str, Any]],
    *,
    known_domains: list[str],
    conversation_id: str,
) -> dict[str, Any] | None:
    """单条候选校验（C7）：不合规返回 None 直接丢弃，不阻塞其余候选。

    - 问题/答案非空且不超长；
    - evidence_quote 逐字存在于会话记录（忽略空白差异）；
    - 答案中的数字/链接/型号必须能在会话记录中找到（防改写事实）。

    conversation_id 是候选行的必填来源列（NOT NULL），校验层直接带上——它是
    「这条候选出自哪次会话」的唯一凭证，不该由调用方在校验之后再补。
    """
    question = re.sub(r"\s+", " ", str(item.get("question") or "")).strip()
    answer = str(item.get("answer") or "").strip()
    evidence = str(item.get("evidence_quote") or "").strip()
    if not question or len(question) > QUESTION_MAX_CHARS:
        return None
    if not answer or len(answer) > ANSWER_MAX_CHARS:
        return None

    transcript_ws = _without_whitespace("\n".join(str(m.get("content") or "") for m in messages))
    if not evidence or _without_whitespace(evidence) not in transcript_ws:
        return None
    normalized_transcript = transcript_ws.casefold()
    for pattern in (_URL_RE, _MODEL_RE, _NUMBER_RE):
        for fact in pattern.findall(answer):
            if _without_whitespace(fact).casefold() not in normalized_transcript:
                return None

    domain = item.get("domain")
    domain = domain if domain in known_domains else None
    try:
        confidence = float(item.get("confidence"))
    except (TypeError, ValueError):
        confidence = None
    if confidence is not None and not 0.0 <= confidence <= 1.0:
        confidence = None
    normalized = normalize_qa_question(question)
    return {
        "source_conversation_id": conversation_id,
        "question": question,
        "normalized_question": normalized,
        "question_hash": hash_qa_question(normalized),
        "answer": answer,
        "domain": domain,
        "confidence": confidence,
        "evidence_quote": evidence,
        "ambiguity_note": str(item.get("ambiguity_note") or "").strip() or None,
    }


async def default_generate(model_spec: str, *, temperature: float, timeout_seconds: float) -> GenerateFn:
    """默认模型适配：走既有 select_model 边界（与文档 QA 生成同一套配置模型）。"""
    from yuxi.models import select_model

    model = select_model(model_spec=model_spec, temperature=temperature)

    async def generate(messages: list[dict[str, str]]) -> str:
        response = await asyncio.wait_for(model.model.ainvoke(messages), timeout=timeout_seconds)
        return str(getattr(response, "text", None) or getattr(response, "content", None) or "")

    return generate


class UdeskSummarizeService:
    """批量总结待处理会话：筛选 → LLM 结构化 → 校验 → 幂等入库 → 打标。"""

    def __init__(
        self,
        session_factory: Callable[[], Any],
        *,
        generate: GenerateFn,
        known_domains: list[str] | None = None,
        max_pairs: int = 5,
        now: Callable[[], datetime] | None = None,
    ):
        self._session_factory = session_factory
        self._generate = generate
        self._known_domains = list(known_domains or [])
        self._max_pairs = max(1, int(max_pairs))
        self._now = now or utc_now

    async def run_batch(self, *, limit: int = 20) -> dict[str, Any]:
        """处理一批待总结会话；返回 processed/candidates/skipped_filtered/failed 计数。

        整个批次持有总结租约：手动触发与每小时 cron 是同一入口，没有租约时两者
        撞车会把同一批会话重复送 LLM。租约被占时返回 {"status": "skipped_lease"}。
        """
        totals = {"processed": 0, "candidates": 0, "skipped_filtered": 0, "failed": 0}
        async with self._session_factory() as session:
            if not await self._acquire_lease(session):
                await self._record_outcome(session, last_error=None, skipped=True)
                await session.commit()
                return {"status": "skipped_lease", **totals}
            # 租约先提交：未提交的 UPDATE 对并发事务不可见，第二份执行会照样抢到
            await session.commit()
            last_error: str | None = None
            try:
                # 只取需要的列、拿普通元组，**不取 ORM 实体**：单会话失败时下面的
                # except 会 rollback，而 rollback 会让 session 里所有 ORM 对象过期，
                # 之后哪怕只是读 conversation.id 都会触发同步惰性加载并抛
                # MissingGreenlet——它会顶掉真正的失败原因，还让整批从下一个会话起
                # 全数死掉（候选一条都没生成的现场）。元组不在 identity map 里，
                # 也就没有过期这回事。
                conversations = (
                    await session.execute(
                        select(UdeskConversation.id, UdeskConversation.conversation_id)
                        .where(UdeskConversation.summarized_at.is_(None))
                        .order_by(UdeskConversation.started_at.asc().nullslast(), UdeskConversation.id.asc())
                        .limit(max(1, int(limit)))
                    )
                ).all()
                for conversation_pk, conversation_id in conversations:
                    messages = (
                        await session.execute(
                            select(UdeskMessage.role, UdeskMessage.content)
                            .where(UdeskMessage.conversation_id == conversation_id)
                            .order_by(UdeskMessage.sent_at.asc().nullslast(), UdeskMessage.id.asc())
                        )
                    ).all()
                    message_dicts = [{"role": role, "content": content} for role, content in messages]
                    totals["processed"] += 1
                    if not has_substantive_qa(message_dicts):
                        # 筛掉即打标：寒暄/纯转接/未答复不再反复送 LLM（P5-1）
                        await self._mark_summarized(session, conversation_pk)
                        totals["skipped_filtered"] += 1
                        continue
                    try:
                        inserted = await self._summarize_conversation(session, conversation_id, message_dicts)
                    except UnparsableModelOutputError as exc:
                        # 确定性失败：输出解析不了，同样的输入再送一次还是解析不了，
                        # 而 token 已经花掉了。打标跳过，否则每小时 cron 反复烧同一条。
                        await session.rollback()
                        totals["failed"] += 1
                        last_error = f"{conversation_id}: {type(exc).__name__}: {exc}"[:500]
                        logger.warning(f"udesk summarize unparsable conversation={conversation_id} reason={last_error}")
                        await self._mark_summarized(session, conversation_pk)
                        continue
                    except Exception as exc:  # noqa: BLE001 - 单会话失败不阻塞整批，下轮重试
                        await session.rollback()
                        totals["failed"] += 1
                        last_error = f"{conversation_id}: {type(exc).__name__}: {exc}"[:500]
                        logger.warning(f"udesk summarize failed conversation={conversation_id} reason={last_error}")
                        continue
                    await self._mark_summarized(session, conversation_pk)
                    totals["candidates"] += inserted
            except Exception as exc:  # noqa: BLE001 - 整批中断也要落状态，不能永久停在 running
                await session.rollback()
                last_error = f"{type(exc).__name__}: {exc}"[:500]
                logger.warning(f"udesk summarize aborted reason={last_error}")
            finally:
                await self._release_lease(session)
                await self._record_outcome(session, last_error=last_error)
                await session.commit()
        return {"status": "failed" if last_error else "succeeded", **totals}

    async def _summarize_conversation(
        self, session: AsyncSession, conversation_id: str, message_dicts: list[dict[str, Any]]
    ) -> int:
        prompt = build_prompt(
            {"conversation_id": conversation_id},
            message_dicts,
            known_domains=self._known_domains,
            max_pairs=self._max_pairs,
        )
        raw = await self._generate(prompt)
        items = extract_candidates(raw)
        validated = [
            row
            for row in (
                validate_candidate(
                    item, message_dicts, known_domains=self._known_domains, conversation_id=conversation_id
                )
                for item in items
            )
            if row is not None
        ][: self._max_pairs]
        if not validated:
            return 0
        # 去重标记（C12 精确口径）：问题哈希已存在于问答对 → duplicate
        existing = await self._existing_hashes(session, [row["question_hash"] for row in validated])
        rows = [
            {
                **row,
                "dedup_status": "duplicate" if row["question_hash"] in existing else "unique",
                "created_at": self._now(),
                "updated_at": self._now(),
            }
            for row in validated
        ]
        statement = (
            pg_insert(CuratedQACandidate)
            .values(rows)
            .on_conflict_do_nothing(index_elements=["source_conversation_id", "question_hash"])
        )
        result = await session.execute(statement)
        return result.rowcount if result.rowcount and result.rowcount > 0 else 0

    async def _mark_summarized(self, session: AsyncSession, conversation_pk: int) -> None:
        """打标并提交：本条不再进入后续批次（筛掉的、总结完的、确定性失败的共用）。"""
        await session.execute(
            update(UdeskConversation).where(UdeskConversation.id == conversation_pk).values(summarized_at=self._now())
        )
        await session.commit()

    async def _existing_hashes(self, session: AsyncSession, hashes: list[str]) -> set[str]:
        result = await session.execute(
            text("SELECT question_hash FROM curated_qa_pairs WHERE question_hash IN :hashes").bindparams(
                bindparam("hashes", expanding=True)
            ),
            {"hashes": hashes},
        )
        return {row[0] for row in result.all()}

    async def _acquire_lease(self, session: AsyncSession) -> bool:
        """抢总结租约；返回 False 表示已有一份在跑（进程崩溃后租约自然过期）。"""
        result = await session.execute(
            text(
                """
                UPDATE udesk_sync_state
                SET summarize_lease_expires_at = :lease_until, summarize_status = 'running',
                    summarize_last_run_at = :now, summarize_last_error = NULL, updated_at = :now
                WHERE id = 1
                  AND (summarize_lease_expires_at IS NULL OR summarize_lease_expires_at <= :now)
                """
            ),
            {"lease_until": self._now() + timedelta(minutes=SUMMARIZE_LEASE_MINUTES), "now": self._now()},
        )
        return (result.rowcount or 0) > 0

    async def _release_lease(self, session: AsyncSession) -> None:
        await session.execute(text("UPDATE udesk_sync_state SET summarize_lease_expires_at = NULL WHERE id = 1"))

    async def _record_outcome(self, session: AsyncSession, *, last_error: str | None, skipped: bool = False) -> None:
        """落本轮状态。有失败原因就记 failed——「整批跑完但每个会话都失败」对外
        不能显示成成功，否则页面看着一切正常而候选一条都没多。
        """
        if skipped:
            status = "skipped_lease"
        else:
            status = "failed" if last_error else "succeeded"
        await session.execute(
            text(
                """
                UPDATE udesk_sync_state
                SET summarize_status = :status, summarize_last_error = :error, updated_at = :now
                WHERE id = 1
                """
            ),
            {"status": status, "error": last_error, "now": self._now()},
        )


async def run_scheduled_summarize(ctx) -> dict[str, Any] | None:
    """cron 入口（每小时）：待总结会话为空或模型不可用时为空操作。

    arq cron 派发会把 job context 作为第一个位置参数传入，必须接住 ctx。
    """
    del ctx
    from yuxi.config.app import config
    from yuxi.storage.postgres.manager import pg_manager

    model_spec = config.udesk_summarize_model or config.default_model
    if not model_spec:
        logger.debug("udesk summarize skipped: 未配置总结模型")
        return None
    try:
        generate = await default_generate(
            model_spec,
            temperature=float(config.udesk_summarize_temperature),
            timeout_seconds=float(config.udesk_summarize_timeout_seconds),
        )
    except Exception as exc:  # noqa: BLE001 - 模型不可用时跳过本轮，不阻塞 worker
        logger.warning(f"udesk summarize skipped: 总结模型不可用 ({type(exc).__name__})")
        return None
    service = UdeskSummarizeService(
        pg_manager.get_async_session_context,
        generate=generate,
        known_domains=[line.get("code") for line in (config.business_lines or []) if line.get("code")],
        max_pairs=int(config.udesk_summarize_max_pairs),
    )
    return await service.run_batch(limit=int(config.udesk_summarize_batch_size))
