"""Udesk 配置合并单测（规范 G5：纯 Mock，不依赖真实凭证/真实配置文件）。

覆盖：设置页值优先于环境变量、环境变量兜底、运行时配置子系统不可用时的
纯环境变量回退、int 合并与下限钳制、token 只从环境变量读取（A9）。
"""

from __future__ import annotations

import pytest

from yuxi.services.udesk.config import API_HISTORY_DAYS, UdeskConfig


@pytest.fixture()
def runtime_config(monkeypatch):
    """拿到真实运行时配置单例，并清空本用例涉及的字段。"""
    from yuxi.config.app import config

    for key in (
        "udesk_enabled",
        "udesk_subdomain",
        "udesk_email",
        "udesk_sync_overlap_minutes",
        "udesk_backfill_start_days",
    ):
        monkeypatch.setattr(config, key, None if key == "udesk_enabled" else "", raising=False)
    for name in (
        "UDESK_ENABLED",
        "UDESK_SUBDOMAIN",
        "UDESK_EMAIL",
        "UDESK_OPEN_API_TOKEN",
        "UDESK_SYNC_OVERLAP_MINUTES",
        "UDESK_BACKFILL_START_DAYS",
    ):
        monkeypatch.delenv(name, raising=False)
    return config


def test_settings_value_wins_over_env(runtime_config, monkeypatch):
    monkeypatch.setattr(runtime_config, "udesk_enabled", True, raising=False)
    monkeypatch.setattr(runtime_config, "udesk_subdomain", "acme", raising=False)
    monkeypatch.setattr(runtime_config, "udesk_email", "admin@example.com", raising=False)
    monkeypatch.setenv("UDESK_ENABLED", "false")
    monkeypatch.setenv("UDESK_SUBDOMAIN", "env-only")
    monkeypatch.setenv("UDESK_EMAIL", "env@example.com")

    cfg = UdeskConfig()
    assert cfg.enabled is True
    assert cfg.subdomain == "acme"
    assert cfg.email == "admin@example.com"


def test_settings_disabled_wins_over_env_enabled(runtime_config, monkeypatch):
    """设置页显式关闭必须压过环境变量：否则管理员关不掉开关。"""
    monkeypatch.setattr(runtime_config, "udesk_enabled", False, raising=False)
    monkeypatch.setenv("UDESK_ENABLED", "true")

    assert UdeskConfig().enabled is False


def test_env_fallback_when_settings_empty(runtime_config, monkeypatch):
    monkeypatch.setenv("UDESK_ENABLED", "true")
    monkeypatch.setenv("UDESK_SUBDOMAIN", "env-sub")
    monkeypatch.setenv("UDESK_EMAIL", "env@example.com")

    cfg = UdeskConfig()
    assert cfg.enabled is True
    assert cfg.subdomain == "env-sub"
    assert cfg.email == "env@example.com"


def test_runtime_unavailable_falls_back_to_pure_env(runtime_config, monkeypatch):
    monkeypatch.setattr("yuxi.services.udesk.config._runtime", lambda: None)
    monkeypatch.setenv("UDESK_ENABLED", "true")
    monkeypatch.setenv("UDESK_SUBDOMAIN", "env-sub")
    monkeypatch.setenv("UDESK_SYNC_OVERLAP_MINUTES", "30")
    monkeypatch.setenv("UDESK_BACKFILL_START_DAYS", "20")

    cfg = UdeskConfig()
    assert cfg.enabled is True
    assert cfg.subdomain == "env-sub"
    assert cfg.sync_overlap_minutes == 30
    assert cfg.backfill_start_days == 20


def test_token_comes_from_env_only(runtime_config, monkeypatch):
    monkeypatch.setenv("UDESK_OPEN_API_TOKEN", "tok-test")
    monkeypatch.setattr(runtime_config, "udesk_enabled", True, raising=False)
    monkeypatch.setattr(runtime_config, "udesk_subdomain", "acme", raising=False)
    monkeypatch.setattr(runtime_config, "udesk_email", "admin@example.com", raising=False)

    cfg = UdeskConfig()
    assert cfg.open_api_token == "tok-test"
    assert cfg.ready is True

    monkeypatch.setenv("UDESK_OPEN_API_TOKEN", "")
    assert UdeskConfig().ready is False


def test_int_merge_clamps_to_minimum(runtime_config, monkeypatch):
    monkeypatch.setattr(runtime_config, "udesk_sync_overlap_minutes", "20", raising=False)
    monkeypatch.setenv("UDESK_BACKFILL_START_DAYS", "0")

    cfg = UdeskConfig()
    assert cfg.sync_overlap_minutes == 20
    # env 值低于下限时钳到 1，不静默沿用默认
    assert cfg.backfill_start_days == 1


def test_backfill_start_days_clamps_to_api_history_ceiling(runtime_config, monkeypatch):
    """接口只提供一个月内的数据：写再大的回灌天数也拿不到更早的历史，
    且窗口一超限就整轮报 status=2000 中断，故生效值必须夹取到一个月。"""
    monkeypatch.setenv("UDESK_BACKFILL_START_DAYS", "365")

    cfg = UdeskConfig()
    assert cfg.backfill_start_days == API_HISTORY_DAYS
    assert cfg.describe()["backfill_start_days"] == API_HISTORY_DAYS


def test_invalid_runtime_int_falls_back_to_env(runtime_config, monkeypatch):
    monkeypatch.setattr(runtime_config, "udesk_sync_overlap_minutes", "not-a-number", raising=False)
    monkeypatch.setenv("UDESK_SYNC_OVERLAP_MINUTES", "45")

    assert UdeskConfig().sync_overlap_minutes == 45


def test_describe_reports_effective_values_and_sources(runtime_config, monkeypatch):
    """设置页空、环境变量齐全时：生效值来自 env，令牌只报是否配置（A9）。"""
    monkeypatch.setenv("UDESK_ENABLED", "true")
    monkeypatch.setenv("UDESK_SUBDOMAIN", "env-sub")
    monkeypatch.setenv("UDESK_EMAIL", "env@example.com")
    monkeypatch.setenv("UDESK_OPEN_API_TOKEN", "tok-test")

    described = UdeskConfig().describe()
    assert described["enabled"] is True
    assert described["subdomain"] == "env-sub"
    assert described["email"] == "env@example.com"
    assert described["token_configured"] is True
    assert described["ready"] is True
    assert "tok-test" not in str(described)  # 令牌值绝不出现在概览里
    assert described["sources"]["enabled"] == "env"
    assert described["sources"]["subdomain"] == "env"
    assert described["sources"]["open_api_token"] == "env"
    assert described["sources"]["backfill_start_days"] == "unset"


def test_ready_and_missing_fields_are_decided_by_credentials_only(runtime_config, monkeypatch):
    """对话记录接口与客户/工单同域同鉴权，不需要额外的接口地址或签名算法配置项，
    故门槛只剩凭证：少一项就指出缺项，齐备即可拉取。"""
    monkeypatch.setenv("UDESK_ENABLED", "true")
    monkeypatch.setenv("UDESK_SUBDOMAIN", "env-sub")

    cfg = UdeskConfig()
    assert cfg.ready is False
    assert cfg.missing_fields == ["email", "open_api_token"]
    assert cfg.describe()["missing_fields"] == ["email", "open_api_token"]

    monkeypatch.setenv("UDESK_EMAIL", "env@example.com")
    monkeypatch.setenv("UDESK_OPEN_API_TOKEN", "tok-test")
    assert UdeskConfig().ready is True
    assert UdeskConfig().missing_fields == []


def test_describe_marks_settings_page_values(runtime_config, monkeypatch):
    """设置页有值时来源标为 settings，且优先于环境变量。"""
    monkeypatch.setattr(runtime_config, "udesk_enabled", False, raising=False)
    monkeypatch.setattr(runtime_config, "udesk_subdomain", "page-sub", raising=False)
    monkeypatch.setenv("UDESK_ENABLED", "true")
    monkeypatch.setenv("UDESK_SUBDOMAIN", "env-sub")

    described = UdeskConfig().describe()
    assert described["enabled"] is False
    assert described["subdomain"] == "page-sub"
    assert described["sources"]["enabled"] == "settings"
    assert described["sources"]["subdomain"] == "settings"
