"""Udesk 客服对话记录增量拉取与入库（知识回流链路二第①步）。

三件套（D11）：游标只负责「不漏」，重复由唯一键兜底；写入一律
INSERT ... ON CONFLICT（D17，禁止先查后插）；执行不并发靠「cron unique=True +
DB 租约」两道闸，租约带 TTL 防进程崩溃后永久堵塞。

接口契约（`/open_api_v1/im/*`，2026-09-15 用真实凭证实测，非文档推断）：
- 列表 GET /open_api_v1/im/sessions/search?start_time&end_time&page&page_size
- 详情 GET /open_api_v1/im/sessions/log?session_id&start_time&end_time&page&page_size
- 与客户/工单等接口**同域同鉴权**（`https://{subdomain}.udesk.cn` + SHA256 v2），
  不需要 KM 模块、独立域名或另一套凭证。
- 信封 {status, message, item, size, total, total_pages}，**没有 code 字段**，status=0 才是成功。
- **接口只提供一个月内的数据**：31 天窗口可用，39 天报 status=2000「暂只提供一个月内的
  数据」。故单窗跨度、首次回灌上限、日志窗口起点一律以 API_HISTORY_DAYS 夹取。
- ⚠ **列表只按「会话创建时刻」归属**：窄窗实测（创建±1min 返回 / 会话中段不返回 /
  关闭±1min 不返回）证明一个会话只在窗口覆盖其创建时刻那一次被带回，之后永不再现。
  两条推论决定了本模块的骨架：① 发现会话的那一次必须把日志拉全（窗口 created_at→now）；
  ② 会话若在发现之后继续产生消息，其尾巴**不会再被列表带回**，必须另行回补。
- 列表带 `status=close` 时改按**结束时间**过滤（官方文档，实测同窗 47 条 vs 按创建 42 条），
  这是唯一能回填 closed_at 的入口；它也会带回创建于一个月前的会话，故日志窗口必须夹取。
- Udesk 接口时间为北京时间字符串（`yyyy-MM-dd HH:mm:ss`，实测 created_at/closed_at 均符合）；
  游标与库内时间统一存 UTC aware，出入参在 to_beijing_string / parse_beijing_string 换算。
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from yuxi.services.udesk.client import UdeskClient
from yuxi.services.udesk.config import API_HISTORY_DAYS
from yuxi.storage.postgres.models_udesk import UdeskMessage
from yuxi.utils import logger
from yuxi.utils.datetime_utils import SHANGHAI_TZ, UTC, utc_now

# Udesk 接口时间为北京时间（即东八区，与项目 SHANGHAI_TZ 同一时刻）
BEIJING = SHANGHAI_TZ

SESSIONS_PATH = "/open_api_v1/im/sessions/search"
LOGS_PATH = "/open_api_v1/im/sessions/log"
PAGE_SIZE = 1000  # 实测单页上限（官方文档亦为 1000，默认 30）
# 翻页保护：total 在翻页期间可能增长，页数封顶防止死循环
MAX_PAGES_PER_WINDOW = 1000
LEASE_MINUTES = 10  # 运行租约时长；一轮增量通常分钟级，超时视为进程已崩溃

# stats_json 白名单（C1：严禁整包落库）。按**处理方式**分三组，不按猜测的字段类型写死：
# 实测 `queue_seconds` 返回字符串而 `sustain_seconds` 返回整数，按类型过滤会静默丢字段。
CONVERSATION_SCALAR_FIELDS = (
    # 消息计数
    "customer_msg_num",
    "agent_msg_num",
    "queue_customer_msg_num",
    "alert_num",
    "ticket_num",
    "conversations_num_today",
    # 时长（queue_seconds 实测为字符串，原样保留）
    "sustain_seconds",
    "resp_seconds",
    "avg_resp_seconds",
    "avg_response_time",
    "queue_seconds",
    # 转人工（实测为 bool）
    "transfer_to_agent",
    # 机器人侧计数：本租户 42 条样本全为纯人工会话、该组字段均为 null，保留位
    "robot_msg_count",
    "robot_customer_msg_count",
    "robot_host_msg_num",
    # 评价与解决态：同上，本租户暂无数据，保留位
    "survey_option_id",
    "survey_option_name",
    "resolved_state",
    "resolved_state_name",
)
CONVERSATION_TEXT_FIELDS = (
    # 客服归属与渠道来源
    "agent_nick_name",
    "agent_duty",
    "belong_queue",
    "source",
    "platform",
    "way_of_open_session",
    "close_method",
    "im_web_plugin_name",
    # 手工标注与知识缺口信号：自由文本、可能含客户 PII，入库前必须过 desensitize_text；
    # 本租户暂无数据（保留位）。旧契约的 unknownQuestionCount 已无等价字段
    "manual_summary",
    "manual_key_words",
    "search_keyword",
)
CONVERSATION_LIST_FIELDS = ("menu_names", "session_tags")

# 明确排除（客户 PII / 非必需），永不入 stats：customer_name / customer_cell_phone /
# customer_email / customer_desc / customer_tags / customer_custom_fields / customer_weixin_* /
# customer_mini_* / customer_org_* / customer_owner_* / customer_token / ip_loc /
# customer_province / customer_city / customer_lang / customer_level / customer_is_block /
# web_info / source_url / note_content / note_custom_fields / last_response / active_guest /
# agent_email / agent_work_id / agent_deps / organization_id / session_suspend_record /
# baidu_* / custom_channel / sub_merchant_name / share_status / survey_*（除 option_id/name）/
# ticket_ids / note_id / im_web_plugin_id / robot_id|name|channel_*|session_id /
# associated_work_order_number / avg_suspend_seconds / total_suspend_seconds / suspend_num

# 聊天记录 content 是一层 JSON 信封（实测 101/101 条），正文在内层 data.content。
# 信封形状并不唯一（外层键组合实测 5 种，只有 data/type 恒在），故一律 .get() 逐键取。
# type 区分真实往来（message/rich）与控制报文（close=会话结束系统报文）；
# is_welcome 标记系统欢迎语。二者都没有说话人，role 记 system——
# 否则「会话已结束」会被下游当成客服答复。
_SYSTEM_MESSAGE_TYPES = {"close"}

# C3 脱敏规则：手机号 / 邮箱 / IPv4。注意**仅覆盖这三类**：姓名、地址、订单号、身份证
# 不在规则内，故白名单里的自由文本（manual_summary 等）仍属「已脱敏但不等于无风险」，
# 客户侧对象字段一律靠白名单排除，不依赖脱敏兜底。
# 手机号必须允许可选的 `+86`/`86-` 前缀：客服常写「add my whatsapp +8617712340018」
# 这种紧贴号码的写法，前缀算在前一个字符里会让 `(?<!\d)` 直接失配、整串逃逸。
# 前缀与号码分两组，脱敏只作用于号码本身，否则「前 3 后 2」会取到 +86 上去。
# 仍不覆盖非大陆号码（如 +60 马来号）与带空格分段的写法，见文档「已知边界」。
_PHONE_RE = re.compile(r"(?<!\d)(\+?86[-\s]?)?(1[3-9]\d{9})(?!\d)")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_IPV4_RE = re.compile(r"(?<!\d)(?:\d{1,3}\.){3}\d{1,3}(?!\d)")


class UdeskPullError(Exception):
    """拉取/入库失败。错误信息只含路径与原因，不含凭证（A8）。"""


# --------------------------------------------------------------- C3 脱敏
def _mask_phone(match: re.Match) -> str:
    prefix, digits = match.group(1) or "", match.group(2)
    return f"{prefix}{digits[:3]}****{digits[-2:]}"


def _mask_email(match: re.Match) -> str:
    local, _, domain = match.group(0).partition("@")
    return f"{local[:1]}***@{domain}"


def _mask_ipv4(match: re.Match) -> str:
    first, second, _, _ = match.group(0).split(".")
    return f"{first}.{second}.*.*"


def desensitize_text(value: str | None) -> str | None:
    """自由文本脱敏：手机号留前 3 后 2、邮箱留首字符与域名、IPv4 留前两段。"""
    if not value:
        return value
    masked = _PHONE_RE.sub(_mask_phone, value)
    masked = _EMAIL_RE.sub(_mask_email, masked)
    return _IPV4_RE.sub(_mask_ipv4, masked)


# ------------------------------------------------------- 北京时间 ↔ UTC
def to_beijing_string(moment: datetime) -> str:
    """UTC 时刻 → Udesk 接口的北京时间字符串（yyyy-MM-dd HH:mm:ss）。"""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(BEIJING).strftime("%Y-%m-%d %H:%M:%S")


def parse_beijing_string(raw: str | None) -> datetime | None:
    """Udesk 北京时间字符串 → UTC aware 时刻。"""
    if not raw:
        return None
    parsed = datetime.strptime(str(raw).strip()[:19], "%Y-%m-%d %H:%M:%S")
    return parsed.replace(tzinfo=BEIJING).astimezone(UTC)


# ------------------------------------------------- 信封解析（契约集中处）
def extract_page(payload: dict[str, Any], *, what: str) -> tuple[list[dict[str, Any]], int, int]:
    """从对话记录接口的响应体取 (条目, total, total_pages)。

    search 与 log 共用同一信封，故只有这一处解析。翻页判停以服务端给出的
    total_pages 为准而不自算（page × page_size），避免 page_size 被服务端静默截断时
    提前退出漏数据。契约若与实测有出入，只改此函数。
    """
    items = payload.get("item")
    total = payload.get("total")
    total_pages = payload.get("total_pages")
    if not isinstance(items, list):
        raise UdeskPullError(f"{what}响应缺少 item 数组")
    if not isinstance(total, int) or total < 0:
        raise UdeskPullError(f"{what}响应缺少 total")
    if not isinstance(total_pages, int) or total_pages < 0:
        raise UdeskPullError(f"{what}响应缺少 total_pages")
    return items, total, total_pages


# --------------------------------------------------- 白名单行构造（C1/C3）
def to_conversation_row(session: dict[str, Any]) -> dict[str, Any]:
    """列表项 → udesk_conversations 行：指标白名单 + 自由文本脱敏 + 客户标识只存哈希。"""
    stats: dict[str, Any] = {}
    for field in CONVERSATION_SCALAR_FIELDS:
        value = session.get(field)
        if value is not None:
            stats[field] = value
    for field in CONVERSATION_TEXT_FIELDS:
        value = session.get(field)
        if value:
            stats[field] = desensitize_text(str(value))
    for field in CONVERSATION_LIST_FIELDS:
        value = session.get(field)
        if isinstance(value, list) and value:
            stats[field] = desensitize_text("、".join(str(item) for item in value))

    # 客户标识只存哈希（C3）：customer_token 本租户实测为空，回退到 customer_id
    token = session.get("customer_token") or session.get("customer_id")
    return {
        "conversation_id": str(session["session_id"]),
        "customer_token_hash": (hashlib.sha256(str(token).encode("utf-8")).hexdigest() if token is not None else None),
        "started_at": parse_beijing_string(session.get("created_at")),
        "ended_at": parse_beijing_string(session.get("closed_at")),
        "stats_json": json.dumps(stats, ensure_ascii=False),
    }


def _parse_message_envelope(raw: Any) -> dict[str, Any] | None:
    """聊天记录 content 的信封解析。非 JSON 时返回 None，调用方按纯文本处理。"""
    if not isinstance(raw, str) or raw.lstrip()[:1] not in "{[":
        return None
    try:
        parsed = json.loads(raw)
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


def to_message_rows(conversation_id: str, logs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """聊天记录 → udesk_messages 行。

    正文取内层 `data.content`（外层 content 是 JSON 信封，直接落库会把信封当正文）。
    说话人取日志外层的 `sender`——它与 content 是兄弟字段，信封解析失败也不该丢掉话路身份。
    无 message_id 时按 会话+时间+序号 合成稳定 ID（同序重拉得到同一 ID，幂等成立）。
    """
    rows: list[dict[str, Any]] = []
    for seq, log in enumerate(logs):
        envelope = _parse_message_envelope(log.get("content"))
        inner = envelope.get("data") if envelope else None
        text = inner.get("content") if isinstance(inner, dict) else log.get("content")
        kind = str(envelope.get("type") or "") if envelope else ""
        sender = str(log.get("sender") or "")
        is_system = kind in _SYSTEM_MESSAGE_TYPES or bool(envelope and envelope.get("is_welcome"))
        role = sender if sender in ("customer", "agent") and not is_system else "system"
        message_id = log.get("message_id") or log.get("id")
        if message_id is None:
            message_id = f"{conversation_id}-{log.get('created_at') or ''}-{seq}"
        rows.append(
            {
                "message_id": str(message_id),
                "conversation_id": conversation_id,
                "role": role,
                "content": desensitize_text(str(text or "")) or "",
                "content_type": kind[:32] or None,
                "sent_at": parse_beijing_string(log.get("created_at")),
            }
        )
    return rows


class UdeskPullService:
    """增量拉取：抢租约 → 起点（含回看）→ 逐窗拉取（按创建 + 按结束）→ 幂等入库 → 推进游标。"""

    def __init__(
        self,
        client: UdeskClient,
        session_factory: Callable[[], Any],
        *,
        overlap_minutes: int = 10,
        backfill_start_days: int = API_HISTORY_DAYS,
        now: Callable[[], datetime] | None = None,
    ):
        self._client = client
        self._session_factory = session_factory
        self._overlap_minutes = overlap_minutes
        self._backfill_start_days = backfill_start_days
        self._now = now or (lambda: utc_now())

    async def run_once(self) -> dict[str, Any]:
        """执行一轮增量拉取；租约被占时返回 {"status": "skipped_lease"}。"""
        async with self._session_factory() as session:
            if not await self._acquire_lease(session):
                # 记一笔：模型把 skipped_lease 列为合法状态，不写就等于「什么都没发生」。
                # 但**只在没人持租约时才写**——抢不到租约恰恰说明正有一轮在跑，那行此刻
                # 归它所有：覆盖成 skipped_lease 会让页面在拉取进行中显示成「跳过」，
                # 极端时序下（被挡的语句晚于对方的收尾写入）还会把它写下的成功结论盖掉。
                await session.execute(
                    text(
                        """
                        UPDATE udesk_sync_state
                        SET last_run_status = 'skipped_lease', updated_at = :now
                        WHERE id = 1 AND (lease_expires_at IS NULL OR lease_expires_at <= :now)
                        """
                    ),
                    {"now": self._now()},
                )
                await session.commit()
                return {"status": "skipped_lease"}
            # 租约必须先提交：未提交的 UPDATE 对并发事务不可见，第二份执行会照样抢到
            await session.commit()
            try:
                summary = await self._pull_all_windows(session)
                await self._record_outcome(session, "succeeded", summary)
                await session.commit()
                return {"status": "succeeded", **summary}
            except Exception as exc:  # noqa: BLE001 - 任何失败都不推进游标（D8/D13）
                await session.rollback()
                error = f"{type(exc).__name__}: {exc}"[:500]
                await self._record_outcome(session, "failed", {"error": error})
                await session.commit()
                logger.warning(f"udesk pull failed reason={error}")
                return {"status": "failed", "error": error}
            finally:
                await self._release_lease(session)
                await session.commit()

    async def reconcile_recent(self, *, days: int = 7) -> dict[str, Any]:
        """对账（D16）：近 N 天逐日比对接口 total 与本地会话数，缺口窗口回补重拉。

        不抢租约、不动游标：写入全部幂等（ON CONFLICT），与增量拉取并发亦安全。
        列表按创建时刻归属，天界即创建时刻口径，故按天比对成立。"""
        now = self._now()
        gaps: list[tuple[datetime, datetime, int, int]] = []
        async with self._session_factory() as session:
            for offset in range(days):
                # 按北京日切界，与接口按日聚合的口径一致
                day_start = (
                    (now - timedelta(days=offset))
                    .astimezone(BEIJING)
                    .replace(hour=0, minute=0, second=0, microsecond=0)
                )
                day_end = day_start + timedelta(days=1)
                response = await self._list_sessions(day_start.astimezone(UTC), day_end.astimezone(UTC), page=1)
                if not response.ok:
                    logger.warning("udesk reconcile day skipped: 列表接口失败")
                    continue
                try:
                    _, total, _ = extract_page(response.data or {}, what="会话列表")
                except UdeskPullError:
                    continue
                local = (
                    await session.execute(
                        text("SELECT count(*) FROM udesk_conversations WHERE started_at >= :s AND started_at < :e"),
                        {"s": day_start.astimezone(UTC), "e": day_end.astimezone(UTC)},
                    )
                ).scalar() or 0
                if local < total:
                    gaps.append((day_start.astimezone(UTC), day_end.astimezone(UTC), total, int(local)))
            for start, end, total, local in gaps:
                logger.warning(f"udesk 对账发现缺口：{to_beijing_string(start)} 本地 {local} < 远端 {total}，回补重拉")
                await self._pull_window(session, start, end)
            await session.commit()
        return {"checked_days": days, "gaps": len(gaps)}

    # --------------------------------------------------------- 内部实现
    async def _list_sessions(
        self, window_start: datetime, window_end: datetime, *, page: int, closed_in_window: bool = False
    ):
        query: dict[str, Any] = {
            "start_time": to_beijing_string(window_start),
            "end_time": to_beijing_string(window_end),
            "page": page,
            "page_size": PAGE_SIZE,
        }
        if closed_in_window:
            query["status"] = "close"
        return await self._client.get(SESSIONS_PATH, query=query)

    async def _pull_all_windows(self, session: AsyncSession) -> dict[str, int]:
        now = self._now()
        watermark = await self._load_watermark(session)
        if watermark is None:
            # 首次拉取：回灌起点由配置决定，上限受「接口只提供一个月数据」夹取（D6）
            start = now - timedelta(days=self._backfill_start_days)
        else:
            # 重叠窗（D14）：边界数据可能延迟写入，重叠拉回的重复由唯一键吸收（D11）
            start = watermark - timedelta(minutes=self._overlap_minutes)

        totals = {"conversations": 0, "messages": 0}
        window_start = start
        while window_start < now:
            window_end = min(window_start + timedelta(days=API_HISTORY_DAYS), now)
            # 每窗两遍：先按创建时刻带走新会话，再按结束时刻带走本窗内结束的会话。
            # 后者是 closed_at 的唯一来源，顺带把它们的尾巴补齐（会话一关闭，日志就完整了）
            for closed_in_window in (False, True):
                convs, msgs = await self._pull_window(
                    session, window_start, window_end, closed_in_window=closed_in_window
                )
                totals["conversations"] += convs
                totals["messages"] += msgs
            # 每窗成功即推进并提交（D13）：中断后已完成的窗口无需重拉（重拉亦无害，幂等）
            await self._advance_watermark(session, window_end)
            await session.commit()
            window_start = window_end
        totals["messages"] += await self._refresh_open_conversations(session, now)
        return totals

    async def _pull_window(
        self, session: AsyncSession, window_start: datetime, window_end: datetime, *, closed_in_window: bool = False
    ) -> tuple[int, int]:
        """返回 (新增会话数, 新增消息数)。

        这里计的是**新增**而非处理次数：同一会话会在「按创建时刻」「按结束时刻」两遍
        遍历里各出现一次（窗口内创建且窗口内结束的会话），按次数计会让页面上的本轮数
        大于累计行数，看起来像数据对不上。进度另走 `progress_done/total`，那才是次数口径。
        """
        conversations = 0
        messages = 0
        page = 1
        while page <= MAX_PAGES_PER_WINDOW:
            response = await self._list_sessions(window_start, window_end, page=page, closed_in_window=closed_in_window)
            if not response.ok:
                raise UdeskPullError(f"拉取会话列表失败 {SESSIONS_PATH}: {response.message}")
            sessions, _, total_pages = extract_page(response.data or {}, what="会话列表")
            for raw in sessions:
                row = to_conversation_row(raw)
                if await self._upsert_conversation(session, row):
                    conversations += 1
                messages += await self._pull_messages(session, row["conversation_id"], row["started_at"])
                await self._report_progress(session, total_delta=1, done_delta=1)
            if page >= total_pages:
                break
            page += 1
        else:
            raise UdeskPullError(f"翻页超出保护上限（{MAX_PAGES_PER_WINDOW} 页），疑似接口异常")
        return conversations, messages

    async def _refresh_open_conversations(self, session: AsyncSession, now: datetime) -> int:
        """尾巴回补：列表只按创建时刻归属，已发现的会话不会再被带回，未结束的会话在发现
        之后仍会产生消息，其尾巴只能靠重拉日志补齐（幂等由 message_id 唯一键吸收）。

        只追踪近期未结束的会话，会话静默后自然停止。**我们学不到它们的 closed_at，
        故 ended_at 会长期为 NULL——不伪造结束时间**；若该会话在此后关闭，那一轮的
        「按结束时刻」遍历会把它带回并补上 closed_at。
        """
        cutoff = now - timedelta(days=API_HISTORY_DAYS)
        rows = (
            await session.execute(
                text(
                    """
                    SELECT conversation_id, started_at FROM udesk_conversations
                    WHERE ended_at IS NULL AND started_at >= :cutoff
                    """
                ),
                {"cutoff": cutoff},
            )
        ).all()
        messages = 0
        if rows:
            # 尾巴回补是本轮唯一「先拿到全部会话、再逐个拉日志」的阶段，
            # 分母在循环前已知，故一次性把总数记上，此后再逐个累加完成数
            await self._report_progress(session, total_delta=len(rows))
        for conversation_id, started_at in rows:
            messages += await self._pull_messages(session, str(conversation_id), started_at)
            await self._report_progress(session, done_delta=1)
        if rows:
            logger.debug(f"udesk 尾巴回补：{len(rows)} 条未结束会话，新增 {messages} 条消息")
        return messages

    async def _pull_messages(self, session: AsyncSession, conversation_id: str, started_at: datetime | None) -> int:
        now = self._now()
        # 日志接口的时间窗必填（缺窗会被静默过滤成空，不报错），且与列表同样只提供一个月内
        # 的数据：「按结束时刻」遍历会带回创建于一个月前的会话，其 created_at→now 跨度超限
        # 会让请求报 status=2000 并中断整轮，故起点夹取到接口视野内
        window_start = (
            max(started_at, now - timedelta(days=API_HISTORY_DAYS))
            if started_at
            else now - timedelta(days=API_HISTORY_DAYS)
        )
        rows: list[dict[str, Any]] = []
        page = 1
        while page <= MAX_PAGES_PER_WINDOW:
            response = await self._client.get(
                LOGS_PATH,
                query={
                    "session_id": conversation_id,
                    "start_time": to_beijing_string(window_start),
                    "end_time": to_beijing_string(now),
                    "page": page,
                    "page_size": PAGE_SIZE,
                },
            )
            if not response.ok:
                raise UdeskPullError(f"拉取会话消息失败 {LOGS_PATH}: {response.message}")
            items, _, total_pages = extract_page(response.data or {}, what="会话消息")
            rows.extend(to_message_rows(conversation_id, items))
            if page >= total_pages:
                break
            page += 1
        else:
            raise UdeskPullError(f"会话消息翻页超出保护上限（{MAX_PAGES_PER_WINDOW} 页），疑似接口异常")
        if not rows:
            return 0
        # D17：ON CONFLICT DO NOTHING，重复拉取/重叠窗/尾巴回补的重复全部被唯一键吸收。
        # 返回值是**真实新增**数而非尝试写入数，这也正是页面上「新增消息」的口径：
        # 一轮全是重复的拉取应显示 0，而不是把重复条数也报成新增。
        # 为此必须 RETURNING——asyncpg 对 executemany 的 rowcount 一律给 -1，
        # 而 SQLAlchemy 从裸 text() 里认不出 RETURNING（会拿到不支持取行的结果对象），
        # 故这里用 Core 构造（与 summarize_service 写候选同款）。
        result = await session.execute(
            pg_insert(UdeskMessage)
            .values([{**row, "created_at": now} for row in rows])
            .on_conflict_do_nothing(index_elements=["message_id"])
            .returning(UdeskMessage.message_id)
        )
        return len(result.all())

    async def _upsert_conversation(self, session: AsyncSession, row: dict[str, Any]) -> bool:
        """写入会话行，返回本次是否**真新增**。

        D17：会话行可刷新（ended_at/stats 随对话继续而变化），消息行 DO NOTHING。
        消息侧用 `ON CONFLICT DO NOTHING` 的 rowcount 直接得到新增数，会话侧不行——
        `DO UPDATE` 的 rowcount 把新增与更新都算 1，分不出来。故用 `xmax = 0` 判新增：
        新插入的元组没被本事务更新过，系统列 xmax 为 0；走 DO UPDATE 分支时
        xmax 记的是当前事务号，非 0。这一点必须和「新增消息」同口径，否则一轮里
        两遍遍历重叠的会话会被计两次，页面上「处理会话 89 条」与累计「会话 58」对不上。
        """
        result = await session.execute(
            text(
                """
                INSERT INTO udesk_conversations
                    (conversation_id, customer_token_hash, started_at, ended_at, stats_json, synced_at)
                VALUES (:conversation_id, :customer_token_hash, :started_at, :ended_at,
                        CAST(:stats_json AS jsonb), :now)
                ON CONFLICT (conversation_id) DO UPDATE SET
                    customer_token_hash = EXCLUDED.customer_token_hash,
                    ended_at = EXCLUDED.ended_at,
                    stats_json = EXCLUDED.stats_json,
                    synced_at = EXCLUDED.synced_at
                RETURNING (xmax = 0) AS inserted
                """
            ),
            {**row, "now": self._now()},
        )
        return bool(result.scalar())

    async def _acquire_lease(self, session: AsyncSession) -> bool:
        result = await session.execute(
            text(
                """
                UPDATE udesk_sync_state
                SET lease_expires_at = :lease_until, last_run_at = :now, last_run_status = 'running',
                    progress_done = 0, progress_total = 0
                WHERE id = 1 AND (lease_expires_at IS NULL OR lease_expires_at <= :now)
                """
            ),
            {"lease_until": self._now() + timedelta(minutes=LEASE_MINUTES), "now": self._now()},
        )
        return (result.rowcount or 0) > 0

    async def _report_progress(self, session: AsyncSession, *, total_delta: int = 0, done_delta: int = 0) -> None:
        """把本轮进度落进单行状态表并提交，让数分钟长的一轮在页面上有可见进展。

        这里必须提交：未提交的 UPDATE 对并发读不可见，状态接口照样只见 running。
        中途提交不影响水位——水位仍只在整窗成功后推进，且所有写入都是幂等的
        （重复由唯一键吸收），故中断后重拉安全。
        """
        await session.execute(
            text(
                """
                UPDATE udesk_sync_state
                SET progress_done = progress_done + :done_delta,
                    progress_total = progress_total + :total_delta,
                    updated_at = :now
                WHERE id = 1
                """
            ),
            {"done_delta": done_delta, "total_delta": total_delta, "now": self._now()},
        )
        await session.commit()

    async def _release_lease(self, session: AsyncSession) -> None:
        await session.execute(
            text("UPDATE udesk_sync_state SET lease_expires_at = NULL WHERE id = 1"),
        )

    async def _load_watermark(self, session: AsyncSession) -> datetime | None:
        row = (await session.execute(text("SELECT watermark FROM udesk_sync_state WHERE id = 1"))).scalar()
        return row if isinstance(row, datetime) else None

    async def _advance_watermark(self, session: AsyncSession, until: datetime) -> None:
        # 游标永不倒退：仅当新值更大才写入；配合「成功才推进」即 D8/D12/D13。
        # 同步续租：长回灌远超单次租约 TTL，逐窗提交时把租约顺延到窗口完成时刻
        await session.execute(
            text(
                """
                UPDATE udesk_sync_state
                SET watermark = :until, lease_expires_at = :lease_until, updated_at = :now
                WHERE id = 1 AND (watermark IS NULL OR watermark < :until)
                """
            ),
            {"until": until, "lease_until": self._now() + timedelta(minutes=LEASE_MINUTES), "now": self._now()},
        )

    async def _record_outcome(self, session: AsyncSession, status: str, summary: dict[str, Any]) -> None:
        """记录本轮结果。摘要里没给的计数保持原值：失败/被租约挡下时进度是 0，
        若照写就会把上一轮已提交的成果抹成「会话 0 条 / 消息 0 条」，
        页面反而在出错时显示得比成功时更「干净」。
        """
        await session.execute(
            text(
                """
                UPDATE udesk_sync_state
                SET last_run_status = :status,
                    last_error = :error,
                    last_run_conversations = COALESCE(CAST(:conversations AS INTEGER), last_run_conversations),
                    last_run_messages = COALESCE(CAST(:messages AS INTEGER), last_run_messages),
                    updated_at = :now
                WHERE id = 1
                """
            ),
            {
                "status": status,
                "error": summary.get("error"),
                "conversations": summary.get("conversations"),
                "messages": summary.get("messages"),
                "now": self._now(),
            },
        )


async def _build_service() -> UdeskPullService | None:
    from yuxi.services.udesk.config import UdeskConfig
    from yuxi.storage.postgres.manager import pg_manager

    config = UdeskConfig()
    if not config.ready:
        logger.debug("udesk pull skipped: UDESK_ENABLED/凭证未配置")
        return None
    return UdeskPullService(
        UdeskClient.from_config(config),
        pg_manager.get_async_session_context,
        overlap_minutes=config.sync_overlap_minutes,
        backfill_start_days=config.backfill_start_days,
    )


async def run_scheduled_pull(ctx) -> dict[str, Any] | None:
    """arq 任务入口（每日 cron + 手动按钮）：未配置时为空操作。

    arq 派发（含 cron）会把 job context 作为第一个位置参数传入，函数必须接住 ctx，
    否则任务一执行就 TypeError——拉取根本没发起，页面自然看不到任何进度。
    """
    del ctx
    service = await _build_service()
    if service is None:
        return None
    return await service.run_once()


async def run_scheduled_reconcile(ctx, days: int = 7) -> dict[str, Any] | None:
    """cron 入口（每日）：对账近 N 天并回补缺口。"""
    del ctx
    service = await _build_service()
    if service is None:
        return None
    return await service.reconcile_recent(days=days)
