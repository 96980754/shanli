from __future__ import annotations

import pytest

from yuxi.config.app import Config, _normalize_wecom_customer_service_urls

URL_A = "https://work.weixin.qq.com/kf/a"
URL_B = "https://work.weixin.qq.com/kf/b"


# ---- _normalize_wecom_customer_service_urls ----

def test_normalize_accepts_empty_and_single_https():
    assert _normalize_wecom_customer_service_urls("") == []
    assert _normalize_wecom_customer_service_urls(None) == []
    assert _normalize_wecom_customer_service_urls(f" {URL_A} ") == [URL_A]


def test_normalize_rejects_non_https():
    with pytest.raises(ValueError):
        _normalize_wecom_customer_service_urls("http://insecure.example.com")


def test_normalize_splits_newline_and_comma_multiple_urls():
    assert _normalize_wecom_customer_service_urls(f"{URL_A}\n{URL_B}") == [URL_A, URL_B]
    assert _normalize_wecom_customer_service_urls(f"{URL_A},{URL_B}") == [URL_A, URL_B]


def test_normalize_accepts_list_and_filters_blank_entries():
    assert _normalize_wecom_customer_service_urls([URL_A, "", URL_B, "  "]) == [URL_A, URL_B]
    with pytest.raises(ValueError):
        _normalize_wecom_customer_service_urls([URL_A, "http://bad"])


def test_normalize_ignores_legacy_domain_url_map():
    """历史按业务域 URL 映射（dict）在拆域路由后忽略，不作为客服池。"""
    assert _normalize_wecom_customer_service_urls({"diaodutai": URL_A}) == []


# ---- 环境变量作为首次启动默认，持久化配置优先 ----

def test_config_seeds_wecom_from_env_when_unset(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("WECOM_CUSTOMER_SERVICE_URL", URL_A)
    cfg = Config(save_dir=str(tmp_path))
    assert cfg.wecom_customer_service_urls == [URL_A]


def test_config_keeps_empty_when_no_env(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("WECOM_CUSTOMER_SERVICE_URL", raising=False)
    cfg = Config(save_dir=str(tmp_path))
    assert cfg.wecom_customer_service_urls == []


def test_config_has_wecom_urls_list_field(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """客服入口是 URL 列表字段（支持 1..N），不再有单值/按域映射字段。"""
    monkeypatch.delenv("WECOM_CUSTOMER_SERVICE_URL", raising=False)
    cfg = Config(save_dir=str(tmp_path))
    assert cfg.wecom_customer_service_urls == []
    assert not hasattr(cfg, "wecom_customer_service_url")
    assert not hasattr(cfg, "wecom_customer_service_urls_map")


def test_legacy_singular_url_migrated_and_domain_map_ignored(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """升级后历史 base.toml：旧单值迁入列表首条；按域 URL 映射被忽略；env 不覆盖已持久化值。"""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "base.toml").write_text(
        'wecom_customer_service_url = "https://work.weixin.qq.com/kf/admin"\n'
        'wecom_customer_service_urls = { diaodutai = "https://work.weixin.qq.com/kf/admin-diaodutai" }\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("WECOM_CUSTOMER_SERVICE_URL", "https://work.weixin.qq.com/kf/env")
    cfg = Config(save_dir=str(tmp_path))
    assert cfg.wecom_customer_service_urls == ["https://work.weixin.qq.com/kf/admin"]


def test_valid_plural_list_in_toml_loads_as_is(tmp_path, monkeypatch: pytest.MonkeyPatch):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "base.toml").write_text(
        f'wecom_customer_service_urls = ["{URL_A}", "{URL_B}"]\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("WECOM_CUSTOMER_SERVICE_URL", "https://work.weixin.qq.com/kf/env")
    cfg = Config(save_dir=str(tmp_path))
    assert cfg.wecom_customer_service_urls == [URL_A, URL_B]
