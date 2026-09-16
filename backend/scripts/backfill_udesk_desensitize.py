"""一次性回填脚本：按修正后的脱敏规则重刷已入库的 Udesk 自由文本。

旧的 `_PHONE_RE` 只认裸的 11 位大陆号码，而客服常写「add my whatsapp +8617712340018」
这种 `+86` 紧贴号码的写法——前缀算在前一个字符里，`(?<!\\d)` 直接失配、整串逃逸。
号码于是原样落进 `udesk_messages`，被 LLM 抄进候选答案，再经采纳进入 `curated_qa_pairs`。

规则本身已在 `pull_service._PHONE_RE` 修正，但**已经落库的行不会自己变干净**，故重刷一遍。
这里刻意复用 `desensitize_text` 而不是在本脚本里再写一份替换逻辑：规则一旦有两处实现，
下次改一处就会出现「新数据干净、老数据不干净」或反过来的漂移。

只碰自由文本列，**不动审核态、启用态与任何计数**。对已脱敏的文本是幂等的
（`177****18` 不再匹配手机号），可重复执行。
"""

from __future__ import annotations

import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.repositories.curated_qa_repository import hash_qa_question, normalize_qa_question
from yuxi.services.udesk.pull_service import (
    CONVERSATION_LIST_FIELDS,
    CONVERSATION_TEXT_FIELDS,
    desensitize_text,
)
from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_curated_qa import CuratedQAPair
from yuxi.storage.postgres.models_udesk import (
    CuratedQACandidate,
    UdeskConversation,
    UdeskMessage,
)

# 候选里的自由文本列
CANDIDATE_TEXT_FIELDS = ("question", "answer", "evidence_quote", "ambiguity_note")
# stats_json 中入库前过脱敏的键：文本字段与列表字段（列表在写入时已 join 成字符串）
STATS_TEXT_KEYS = tuple(CONVERSATION_TEXT_FIELDS) + tuple(CONVERSATION_LIST_FIELDS)


async def redact_rows(session: AsyncSession, model, fields: tuple[str, ...]) -> int:
    """逐行重刷给定文本列，只写回真正变化的行。

    每个字段都必须走到——`any(... for ...)` 会短路，前一个字段返回 True 就再也不会
    处理后面的列，留下「同一行的部分列干净、部分列还带号码」的残局（引文校验会发现）。
    """
    updated = 0
    for row in (await session.execute(select(model))).scalars().all():
        changed = False
        for field in fields:
            if _apply(row, field):
                changed = True
        if changed:
            updated += 1
    return updated


def _apply(row, field: str) -> bool:
    original = getattr(row, field)
    if not original:
        return False
    masked = desensitize_text(original)
    if masked == original:
        return False
    setattr(row, field, masked)
    return True


async def redact_qa_pairs(session: AsyncSession) -> int:
    """问答对：改问题时必须同步重算精确匹配键并使语义向量失效，口径同仓储层 update_content。

    问题变了而哈希不重算，精确匹配会按旧键命中，问答对管理页改一次内容就能复现同一问题，
    这里不能因为是回填脚本就绕过。
    """
    updated = 0
    for row in (await session.execute(select(CuratedQAPair))).scalars().all():
        changed = _apply(row, "answer")
        if _apply(row, "question"):
            normalized = normalize_qa_question(row.question)
            row.normalized_question = normalized
            row.question_hash = hash_qa_question(normalized)
            row.question_embedding = None  # 旧向量对应旧问题，留着会语义召回到错答案
            changed = True
        if changed:
            updated += 1
    return updated


async def redact_conversation_stats(session: AsyncSession) -> int:
    """会话 stats_json 里的自由文本键（本租户暂无数据，其他部署可能有）。"""
    updated = 0
    for row in (await session.execute(select(UdeskConversation))).scalars().all():
        stats = row.stats_json
        if not isinstance(stats, dict):
            continue
        changed = False
        for key in STATS_TEXT_KEYS:
            value = stats.get(key)
            if not isinstance(value, str) or not value:
                continue
            masked = desensitize_text(value)
            if masked != value:
                stats[key] = masked
                changed = True
        if changed:
            row.stats_json = stats
            updated += 1
    return updated


async def verify_evidence_still_traceable(session: AsyncSession) -> None:
    """脱敏对会话与证据引文同源，重刷后引文仍应能在原会话里逐字找到。"""
    pairs = (
        await session.execute(select(CuratedQACandidate.source_conversation_id, CuratedQACandidate.evidence_quote))
    ).all()
    broken = 0
    for conversation_id, evidence in pairs:
        if not evidence:
            continue
        contents = (
            (await session.execute(select(UdeskMessage.content).where(UdeskMessage.conversation_id == conversation_id)))
            .scalars()
            .all()
        )
        if _squeeze(evidence) not in _squeeze("".join(contents)):
            broken += 1
            print(f"  引文已无法逐字核对：conversation_id={conversation_id}", flush=True)
    print(f"证据引文校验：{len(pairs) - broken}/{len(pairs)} 条仍可逐字核对", flush=True)


def _squeeze(value: str) -> str:
    return "".join(str(value).split())


async def main() -> None:
    async with pg_manager.get_async_session_context() as session:
        counts = {
            "udesk_messages.content": await redact_rows(session, UdeskMessage, ("content",)),
            "udesk_conversations.stats_json": await redact_conversation_stats(session),
            "curated_qa_candidates": await redact_rows(session, CuratedQACandidate, CANDIDATE_TEXT_FIELDS),
            "curated_qa_pairs": await redact_qa_pairs(session),
        }
        for table, count in counts.items():
            print(f"{table}: 更新 {count} 行", flush=True)
        await session.commit()
        await verify_evidence_still_traceable(session)


if __name__ == "__main__":
    asyncio.run(main())
