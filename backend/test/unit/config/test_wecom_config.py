from __future__ import annotations

import pytest

from yuxi.config.app import (
    Config,
    _normalize_wecom_customer_service_url,
    _normalize_wecom_customer_service_urls,
    _parse_wecom_customer_service_urls,
)

DOMAIN_URL = "https://work.weixin.qq.com/kf/diaodutai"


# ---- _parse_wecom_customer_service_urls（环境变量 JSON 解析）----

def test_parse_urls_valid_json():
    raw = '{"diaodutai": "https://work.weixin.qq.com/kf/diaodutai", "ops": "https://work.weixin.qq.com/kf/ops"}'
    assert _parse_wecom_customer_service_urls(raw) == {
        "diaodutai": "https://work.weixin.qq.com/kf/diaodutai",
        "ops": "https://work.weixin.qq.com/kf/ops",
    }


def test_parse_urls_drops_empty_values():
    assert _parse_wecom_customer_service_urls('{"diaodutai": "https://x.com/kf", "ops": ""}') == {
        "diaodutai": "https://x.com/kf"
    }


def test_parse_urls_invalid_json_returns_empty():
    assert _parse_wecom_customer_service_urls("not json") == {}
    assert _parse_wecom_customer_service_urls("") == {}


def test_parse_urls_non_dict_returns_empty():
    assert _parse_wecom_customer_service_urls("[1, 2]") == {}


# ---- _normalize_wecom_customer_service_url ----

def test_normalize_global_url_allows_empty_and_https():
    assert _normalize_wecom_customer_service_url("") == ""
    assert _normalize_wecom_customer_service_url(" https://work.weixin.qq.com/kf/x ") == "https://work.weixin.qq.com/kf/x"


def test_normalize_global_url_rejects_non_https():
    with pytest.raises(ValueError):
        _normalize_wecom_customer_service_url("http://insecure.example.com")


# ---- _normalize_wecom_customer_service_urls ----

def test_normalize_domain_urls_filters_empty_and_rejects_invalid():
    assert _normalize_wecom_customer_service_urls({"diaodutai": DOMAIN_URL, "ops": "", "": "https://x"}) == {
        "diaodutai": DOMAIN_URL
    }
    with pytest.raises(ValueError):
        _normalize_wecom_customer_service_urls({"diaodutai": "http://insecure.example.com"})
    with pytest.raises(ValueError):
        _normalize_wecom_customer_service_urls("not a dict")


# ---- 环境变量作为首次启动默认，持久化配置优先 ----

def test_config_seeds_wecom_from_env_when_unset(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("WECOM_CUSTOMER_SERVICE_URL", "https://work.weixin.qq.com/kf/default")
    monkeypatch.setenv("WECOM_CUSTOMER_SERVICE_URLS", '{"diaodutai": "https://work.weixin.qq.com/kf/diaodutai"}')
    cfg = Config(save_dir=str(tmp_path))
    assert cfg.wecom_customer_service_url == "https://work.weixin.qq.com/kf/default"
    assert cfg.wecom_customer_service_urls == {"diaodutai": "https://work.weixin.qq.com/kf/diaodutai"}


def test_config_keeps_empty_when_no_env(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("WECOM_CUSTOMER_SERVICE_URL", raising=False)
    monkeypatch.delenv("WECOM_CUSTOMER_SERVICE_URLS", raising=False)
    cfg = Config(save_dir=str(tmp_path))
    assert cfg.wecom_customer_service_url == ""
    assert cfg.wecom_customer_service_urls == {}


def test_persisted_config_wins_over_env(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """管理界面保存过的配置（base.toml）优先于环境变量，避免 admin 改动被重启回退。"""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "base.toml").write_text(
        'wecom_customer_service_url = "https://work.weixin.qq.com/kf/admin"\n'
        'wecom_customer_service_urls = { diaodutai = "https://work.weixin.qq.com/kf/admin-diaodutai" }\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("WECOM_CUSTOMER_SERVICE_URL", "https://work.weixin.qq.com/kf/env")
    monkeypatch.setenv("WECOM_CUSTOMER_SERVICE_URLS", '{"ops": "https://work.weixin.qq.com/kf/env-ops"}')
    cfg = Config(save_dir=str(tmp_path))
    assert cfg.wecom_customer_service_url == "https://work.weixin.qq.com/kf/admin"
    assert cfg.wecom_customer_service_urls == {"diaodutai": "https://work.weixin.qq.com/kf/admin-diaodutai"}
