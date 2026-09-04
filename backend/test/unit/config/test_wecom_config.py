"""企微客服「命名条目」配置单测：模型归一、存量迁移、环境变量首启、跨字段绑定校验。"""

from __future__ import annotations

import pytest

from yuxi.config.app import (
    Config,
    _customer_services_from_urls,
    _normalize_wecom_customer_services,
    _normalize_wecom_service_urls,
)

URL_A = "https://work.weixin.qq.com/kf/a"
URL_B = "https://work.weixin.qq.com/kf/b"


def _entry_row(**overrides: dict) -> dict:
    row = {"id": "cs-diaodutai-a", "name": "调度台客服A", "urls": [URL_A, URL_B]}
    row.update(overrides)
    return row


# ---- 字段形态 ----

def test_config_has_named_service_entries_field(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("WECOM_CUSTOMER_SERVICE_URL", raising=False)
    cfg = Config(save_dir=str(tmp_path))
    assert cfg.wecom_customer_services == []
    # 裸 URL 列表 / 单值 / 按域映射字段已不存在（拆域路由后并入命名条目语义）。
    assert not hasattr(cfg, "wecom_customer_service_urls")
    assert not hasattr(cfg, "wecom_customer_service_url")
    assert not hasattr(cfg, "wecom_customer_service_urls_map")


# ---- _normalize_wecom_customer_services ----

def test_normalize_backfills_id_and_validates():
    rows = _normalize_wecom_customer_services([{"name": "调度台客服A", "urls": f"{URL_A}\n{URL_B}"}])
    assert len(rows) == 1
    assert rows[0]["id"]  # 自动补发 id
    assert rows[0]["name"] == "调度台客服A"
    assert rows[0]["urls"] == [URL_A, URL_B]


def test_normalize_preserves_given_id_and_dedupes_urls():
    assert _normalize_wecom_customer_services([_entry_row(urls=[URL_A, URL_A, URL_B])]) == [
        {"id": "cs-diaodutai-a", "name": "调度台客服A", "urls": [URL_A, URL_B]}
    ]


def test_normalize_splits_urls_on_comma_and_fullwidth_comma():
    row = _normalize_wecom_customer_services([_entry_row(urls=f"{URL_A},{URL_B}，{URL_A}")])[0]
    assert row["urls"] == [URL_A, URL_B]


def test_normalize_rejects_non_https_url():
    with pytest.raises(ValueError, match="https"):
        _normalize_wecom_customer_services([_entry_row(urls=["http://insecure.example.com"])])


def test_normalize_rejects_entry_without_url():
    with pytest.raises(ValueError, match="至少需要一个"):
        _normalize_wecom_customer_services([{"id": "cs-x", "name": "空客服", "urls": []}])


def test_normalize_rejects_blank_name():
    with pytest.raises(ValueError):
        _normalize_wecom_customer_services([{"id": "cs-x", "name": "  ", "urls": [URL_A]}])


def test_normalize_rejects_duplicate_ids():
    rows = [_entry_row(id="cs-dup"), _entry_row(id="cs-dup", name="另一个")]
    with pytest.raises(ValueError, match="重复"):
        _normalize_wecom_customer_services(rows)


def test_normalize_accepts_single_dict_and_none():
    assert len(_normalize_wecom_customer_services(_entry_row())) == 1
    assert _normalize_wecom_customer_services(None) == []
    assert _normalize_wecom_customer_services([]) == []


# ---- 存量迁移 / 环境变量首启 ----

def test_legacy_plural_url_list_migrated_to_entries(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """升级后历史 base.toml：旧裸 URL 列表 → 每 URL 一条命名条目（保序）；env 不覆盖已持久化值。"""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "base.toml").write_text(
        f'wecom_customer_service_urls = ["{URL_A}", "{URL_B}"]\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("WECOM_CUSTOMER_SERVICE_URL", "https://work.weixin.qq.com/kf/env")
    cfg = Config(save_dir=str(tmp_path))
    assert cfg.wecom_customer_services == _customer_services_from_urls([URL_A, URL_B])
    assert cfg.wecom_customer_services[0]["name"] == "客服1"


def test_legacy_singular_url_and_domain_map_ignored(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """更早的单值迁入条目；按域 URL 映射（dict）在拆域路由后忽略不作为客服。"""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "base.toml").write_text(
        'wecom_customer_service_url = "https://work.weixin.qq.com/kf/admin"\n'
        'wecom_customer_service_urls = { diaodutai = "https://work.weixin.qq.com/kf/admin-diaodutai" }\n',
        encoding="utf-8",
    )
    monkeypatch.delenv("WECOM_CUSTOMER_SERVICE_URL", raising=False)
    cfg = Config(save_dir=str(tmp_path))
    assert cfg.wecom_customer_services == [
        {"id": "cs1", "name": "客服1", "urls": ["https://work.weixin.qq.com/kf/admin"]}
    ]


def test_env_seeds_entries_when_nothing_persisted(tmp_path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("WECOM_CUSTOMER_SERVICE_URL", f"{URL_A},{URL_B}")
    cfg = Config(save_dir=str(tmp_path))
    assert cfg.wecom_customer_services == _customer_services_from_urls([URL_A, URL_B])


def test_env_not_used_when_entries_persisted(tmp_path, monkeypatch: pytest.MonkeyPatch):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "base.toml").write_text(
        f'wecom_customer_services = [{{ id = "cs-a", name = "客服A", urls = ["{URL_A}"] }}]\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("WECOM_CUSTOMER_SERVICE_URL", URL_B)
    cfg = Config(save_dir=str(tmp_path))
    assert cfg.wecom_customer_services == [{"id": "cs-a", "name": "客服A", "urls": [URL_A]}]


def test_toml_with_business_line_binding_loads_without_cross_validation(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """加载不走跨字段校验：业务线绑定未知客服 id 也能启动（转接时按未绑定兜底），避免加载顺序脆弱。"""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "base.toml").write_text(
        f'wecom_customer_services = [{{ id = "cs-a", name = "客服A", urls = ["{URL_A}"] }}]\n'
        'business_lines = [{ code = "diaodutai", name = "调度台", customer_service_ids = ["cs-a", "cs-ghost"] }]\n',
        encoding="utf-8",
    )
    monkeypatch.delenv("WECOM_CUSTOMER_SERVICE_URL", raising=False)
    cfg = Config(save_dir=str(tmp_path))
    assert cfg.wecom_customer_services[0]["id"] == "cs-a"
    assert cfg.business_lines[0]["customer_service_ids"] == ["cs-a", "cs-ghost"]


def test_normalize_wecom_service_urls_helper():
    assert _normalize_wecom_service_urls(None) == []
    assert _normalize_wecom_service_urls(f"{URL_A} , {URL_B}") == [URL_A, URL_B]
    with pytest.raises(ValueError):
        _normalize_wecom_service_urls(["http://bad"])
