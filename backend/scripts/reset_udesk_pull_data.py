"""运维脚本：重置 Udesk 拉取链路的全部数据，使下一次拉取从零回灌。

**为什么需要整体重置而不是只清水位**：修好中文化（`build_prompt`）与脱敏逃逸
（`_PHONE_RE`）之后，若只重置水位、让已总结会话保持原样，修复前产出的英文候选会一直留着;
而清 `summarized_at` 重跑也不行——中文译文对应新的 `question_hash`，唯一键
`uq_curated_qa_candidates_conv_question` 按 (source_conversation_id, question_hash) 判重，
拦不住「同一会话、同一问题的英文旧行 + 中文新行」，旧行还会成为指向已删会话的孤儿。
故整链丢弃重做。

删除范围：
- `curated_qa_candidates`（全部）
- `udesk_conversations`（`udesk_messages` 随 FK `ON DELETE CASCADE` 一起走）
- `curated_qa_pairs` 中 `source_type='udesk'` 的行——**含 enabled=true、正在参与应答的知识**
- `udesk_sync_state` 水位/进度/租约/运行状态归零：
  `watermark=NULL` 会被下一次拉取当作「首次拉取」，从 `now - backfill_start_days`
  起回灌（再受接口 30 天窗口夹取）

不动：feedback 来源的问答对、以及 Udesk 链路以外的任何数据。

**不可逆**，默认只做 dry-run，必须显式加 `--yes` 才真正删除。删除前请自行备份，例如：

    docker exec postgres sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --data-only \\
      --table=udesk_conversations --table=udesk_messages --table=curated_qa_candidates \\
      --table=curated_qa_pairs --table=udesk_sync_state' > ~/udesk-backup.sql
"""

from __future__ import annotations

import asyncio
import sys

from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.storage.postgres.manager import pg_manager
from yuxi.storage.postgres.models_curated_qa import CuratedQAPair
from yuxi.storage.postgres.models_udesk import (
    CuratedQACandidate,
    UdeskConversation,
    UdeskMessage,
    UdeskSyncState,
)

# 归零的运行状态列。分两组是因为同步表里这 4 个计数列是 NOT NULL DEFAULT 0，
# 置 NULL 会直接违反约束——它们只能归零。
NULLABLE_RESET_COLUMNS = (
    "watermark",
    "lease_expires_at",
    "last_run_at",
    "last_run_status",
    "last_error",
    "summarize_lease_expires_at",
    "summarize_status",
    "summarize_last_error",
    "summarize_last_run_at",
)
COUNTER_RESET_COLUMNS = (
    "last_run_conversations",
    "last_run_messages",
    "progress_done",
    "progress_total",
)


async def count_affected(session: AsyncSession) -> dict[str, int]:
    return {
        "会话": (await session.execute(select(func.count()).select_from(UdeskConversation))).scalar() or 0,
        "消息": (await session.execute(select(func.count()).select_from(UdeskMessage))).scalar() or 0,
        "候选": (await session.execute(select(func.count()).select_from(CuratedQACandidate))).scalar() or 0,
        "udesk 来源问答对": (
            await session.execute(
                select(func.count()).select_from(CuratedQAPair).where(CuratedQAPair.source_type == "udesk")
            )
        ).scalar()
        or 0,
    }


async def assert_no_active_run(session: AsyncSession) -> None:
    """拉取或总结正在跑时拒绝重置。

    两个 job 各自持有租约并会在结束时写回水位与运行状态；此时删数据会让运行中的
    job 继续往半空的库里插行、并把水位推到「已处理」——删完立刻又被污染，状态还看不出来。
    """
    state = (await session.execute(select(UdeskSyncState).where(UdeskSyncState.id == 1))).scalar_one_or_none()
    if state is None:
        return
    if await session.scalar(text("SELECT lease_expires_at > now() FROM udesk_sync_state WHERE id = 1")):
        raise SystemExit("拉取正在进行（租约未过期），拒绝重置。请等本轮结束或等租约过期后重试。")
    if await session.scalar(text("SELECT summarize_lease_expires_at > now() FROM udesk_sync_state WHERE id = 1")):
        raise SystemExit("总结正在进行（租约未过期），拒绝重置。请等本轮结束或等租约过期后重试。")


async def reset_pull_data(session: AsyncSession) -> dict[str, int]:
    """先归零状态行（这一步最容易写错，放在删除之前让它在无副作用时先失败），再按范围删除。"""
    assignments = ", ".join(
        [
            *(f"{column} = NULL" for column in NULLABLE_RESET_COLUMNS),
            *(f"{column} = 0" for column in COUNTER_RESET_COLUMNS),
        ]
    )
    await session.execute(text(f"UPDATE udesk_sync_state SET {assignments}, updated_at = now() WHERE id = 1"))

    qa_pairs = (await session.execute(delete(CuratedQAPair).where(CuratedQAPair.source_type == "udesk"))).rowcount or 0
    candidates = (await session.execute(delete(CuratedQACandidate))).rowcount or 0
    conversations = (await session.execute(delete(UdeskConversation))).rowcount or 0
    messages = (await session.execute(select(func.count()).select_from(UdeskMessage))).scalar() or 0
    return {"udesk 来源问答对": qa_pairs, "候选": candidates, "会话": conversations, "消息(级联后残留)": messages}


async def main(*, confirmed: bool) -> None:
    async with pg_manager.get_async_session_context() as session:
        await assert_no_active_run(session)
        before = await count_affected(session)
        print("将删除：" + "、".join(f"{name} {count} 行" for name, count in before.items()), flush=True)

        if not confirmed:
            print("dry-run：未做任何改动。确认无误后加 --yes 重新执行。", flush=True)
            return

        deleted = await reset_pull_data(session)
        await session.commit()
        print("已删除：" + "、".join(f"{name} {count} 行" for name, count in deleted.items()), flush=True)

        after = await count_affected(session)
        watermark = (await session.execute(text("SELECT watermark FROM udesk_sync_state WHERE id = 1"))).scalar()
        print("重置后：" + "、".join(f"{name} {count} 行" for name, count in after.items()), flush=True)
        print(f"水位 watermark = {watermark!r}（NULL 表示下一次拉取按首次拉取全量回灌）", flush=True)


if __name__ == "__main__":
    asyncio.run(main(confirmed="--yes" in sys.argv))
