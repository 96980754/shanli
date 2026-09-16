"""Udesk 对接配置。

非密参数优先取系统设置（设置页保存、Redis 热同步生效，甲方无技术人员也能自助改），
环境变量作为默认值兜底；`open_api_token` 是永久凭证（规范 A9：禁止写入配置文件、
日志、数据库），**只从环境变量读取**。未配置时 `enabled` 为 False，任何 Udesk 调用
都不会发起（E3/E5）。
"""

from __future__ import annotations

import os
from typing import Any

# 配置项来源：设置页 / 服务器环境变量 / 未配置（沿用内置默认值）
SOURCE_SETTINGS = "settings"
SOURCE_ENV = "env"
SOURCE_UNSET = "unset"

# 对话记录接口只提供一个月内的数据（2026-09-15 实测）：既决定首次回灌的上限，
# 也决定单窗最大跨度与日志窗口起点夹取值，故放在配置层作为唯一来源
API_HISTORY_DAYS = 30


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


def _runtime() -> object | None:
    """运行时配置快照（import 期不可用，调用期惰性取）。"""
    try:
        from yuxi.config.app import config

        return config
    except Exception:  # pragma: no cover - 配置子系统未就绪时退回纯环境变量
        return None


def _resolve_enabled() -> tuple[bool, str]:
    """总开关：设置页显式表态（True/False）优先；未设置过（None）回退 UDESK_ENABLED。"""
    runtime = _runtime()
    runtime_enabled = getattr(runtime, "udesk_enabled", None) if runtime is not None else None
    if runtime_enabled is not None:
        return bool(runtime_enabled), SOURCE_SETTINGS
    raw = _env("UDESK_ENABLED")
    return (raw.lower() in ("1", "true", "yes"), SOURCE_ENV) if raw else (False, SOURCE_UNSET)


def _resolve_str(key: str, env_name: str) -> tuple[str, str]:
    """字符串项：设置页有值即用，否则环境变量，都没有则空值。"""
    runtime = _runtime()
    value = str(getattr(runtime, key, "") or "").strip() if runtime is not None else ""
    if value:
        return value, SOURCE_SETTINGS
    env_value = _env(env_name)
    return (env_value, SOURCE_ENV) if env_value else ("", SOURCE_UNSET)


def _resolve_int(key: str, env_name: str, default: int) -> tuple[int, str]:
    """整数项：设置页值非法时逐级回退环境变量与内置默认值。"""
    runtime = _runtime()
    raw = str(getattr(runtime, key, "") or "").strip() if runtime is not None else ""
    if raw:
        try:
            return int(raw), SOURCE_SETTINGS
        except ValueError:
            pass
    env_raw = _env(env_name)
    if not env_raw:
        return default, SOURCE_UNSET
    try:
        return int(env_raw), SOURCE_ENV
    except ValueError:
        return default, SOURCE_UNSET


class UdeskConfig:
    """Udesk 接入参数快照（运行时配置 + 环境变量合并值，并记录每项来源）。"""

    def __init__(self) -> None:
        self.sources: dict[str, str] = {}
        self.enabled, self.sources["enabled"] = _resolve_enabled()
        self.subdomain, self.sources["subdomain"] = _resolve_str("udesk_subdomain", "UDESK_SUBDOMAIN")
        self.email, self.sources["email"] = _resolve_str("udesk_email", "UDESK_EMAIL")
        # 永久凭证：只从环境变量读取（A9），绝不经设置页/配置文件流转
        self.open_api_token = _env("UDESK_OPEN_API_TOKEN")
        self.sources["open_api_token"] = SOURCE_ENV if self.open_api_token else SOURCE_UNSET
        # 限流/超时/重试为部署级环境变量项，未迁设置页。默认 24 次/分（间隔 2.5s）：
        # 官方对 im/sessions/search 与 im/sessions/log 各限 1 次/2 秒，全局匀速取最严间隔
        self.rate_limit_per_min = _env_int("UDESK_RATE_LIMIT_PER_MIN", 24)
        self.request_timeout_seconds = _env_int("UDESK_REQUEST_TIMEOUT_SECONDS", 15)
        self.max_retries = _env_int("UDESK_MAX_RETRIES", 2)
        # 增量拉取参数（D11/D14）：重叠窗防边界漏读，重复由唯一键吸收；
        # 首次拉取回灌起点（无游标时从 now - backfill_start_days 开始，单窗再按 30 天切分）
        overlap, self.sources["sync_overlap_minutes"] = _resolve_int(
            "udesk_sync_overlap_minutes", "UDESK_SYNC_OVERLAP_MINUTES", 10
        )
        backfill, self.sources["backfill_start_days"] = _resolve_int(
            "udesk_backfill_start_days", "UDESK_BACKFILL_START_DAYS", API_HISTORY_DAYS
        )
        self.sync_overlap_minutes = max(1, overlap)
        # 接口只提供一个月内的数据（实测 31 天窗口可用、39 天报 status=2000「暂只提供一个月内
        # 的数据」），配置值必须夹取：否则窗口一超限就整轮 UdeskPullError 中断
        self.backfill_start_days = min(max(1, backfill), API_HISTORY_DAYS)

    def describe(self) -> dict[str, Any]:
        """生效配置概览（含每项来源），供设置页只读展示。

        .env 与设置页存在合并关系，页面表单只反映设置页自己的快照；管理员需要看到
        实际生效值才知道是否已配置。令牌只报告是否已配置，绝不返回值（A9）。
        """
        return {
            "enabled": self.enabled,
            "subdomain": self.subdomain,
            "email": self.email,
            "sync_overlap_minutes": self.sync_overlap_minutes,
            "backfill_start_days": self.backfill_start_days,
            "token_configured": bool(self.open_api_token),
            "ready": self.ready,
            "missing_fields": self.missing_fields,
            "sources": dict(self.sources),
        }

    @property
    def base_url(self) -> str:
        """v2 开放接口（/open_api_v1/*）的服务地址。"""
        return f"https://{self.subdomain}.udesk.cn"

    @property
    def ready(self) -> bool:
        """凭证齐备（enabled 且 subdomain/email/token 三项齐全）。"""
        return bool(self.enabled and self.subdomain and self.email and self.open_api_token)

    @property
    def missing_fields(self) -> list[str]:
        """拉取还缺哪些配置项（字段名）。

        调用方据此给出可操作的原因，而不是排一个必然空跑的拉取任务。对话记录接口
        与其它接口同域同鉴权，故门槛只剩这四项凭证。
        """
        required = {
            "enabled": self.enabled,
            "subdomain": self.subdomain,
            "email": self.email,
            "open_api_token": self.open_api_token,
        }
        return [key for key, value in required.items() if not value]


def load_udesk_config() -> UdeskConfig:
    return UdeskConfig()
