from __future__ import annotations

import pytest

from yuxi.config.app import (
    BusinessLine,
    Config,
    config as runtime_config,
    _normalize_business_lines,
    resolve_business_lines,
    sanitize_business_domain,
)

_DEFAULT_CODES = ["diaodutai", "terminal", "ops", "mno", "kefu"]


def _set_business_lines(monkeypatch: pytest.MonkeyPatch, rows: list[dict]) -> None:
    monkeypatch.setattr(runtime_config, "business_lines", rows)


# ---- Config 默认清单 ----


def test_config_has_five_default_business_lines(tmp_path):
    cfg = Config(save_dir=str(tmp_path))
    rows = cfg.business_lines
    assert [row["code"] for row in rows] == _DEFAULT_CODES
    assert all({"code", "name", "keywords"} <= set(row) for row in rows)


def test_default_rows_deep_copied_per_instance(tmp_path):
    cfg_a = Config(save_dir=str(tmp_path))
    cfg_b = Config(save_dir=str(tmp_path))
    assert cfg_a.business_lines is not cfg_b.business_lines
    cfg_a.business_lines[0]["keywords"].append("独有词")
    assert "独有词" not in cfg_b.business_lines[0]["keywords"]


# ---- _normalize_business_lines ----


def test_normalize_accepts_single_dict():
    assert _normalize_business_lines({"code": "diaodutai", "name": "调度台", "keywords": ["调度台", "mcx"]}) == [
        {"code": "diaodutai", "name": "调度台", "keywords": ["调度台", "mcx"]}
    ]


def test_normalize_accepts_business_line_instance():
    assert _normalize_business_lines([BusinessLine(code="terminal", name="终端", keywords=["cat1"])]) == [
        {"code": "terminal", "name": "终端", "keywords": ["cat1"]}
    ]


def test_normalize_lowercases_and_strips_code_and_name():
    rows = _normalize_business_lines([{"code": "  Terminal ", "name": "  终端 "}])
    assert rows == [{"code": "terminal", "name": "终端", "keywords": []}]


def test_normalize_dedupes_and_trims_blank_keywords():
    rows = _normalize_business_lines(
        [{"code": "terminal", "name": "终端", "keywords": ["cat1", "cat1", "", "  cat1模组  ", "cat1"]}]
    )
    assert rows[0]["keywords"] == ["cat1", "cat1模组"]


def test_normalize_splits_string_keywords_on_separators():
    rows = _normalize_business_lines([{"code": "terminal", "name": "终端", "keywords": "cat1, cat1模组 / 安卓"}])
    assert rows[0]["keywords"] == ["cat1", "cat1模组", "安卓"]


def test_normalize_none_and_empty_clear_list():
    assert _normalize_business_lines(None) == []
    assert _normalize_business_lines([]) == []


def test_normalize_rejects_duplicate_codes():
    rows = [{"code": "terminal", "name": "终端"}, {"code": "terminal", "name": "终端B"}]
    with pytest.raises(ValueError, match="重复"):
        _normalize_business_lines(rows)


def test_normalize_rejects_reserved_unknown_code():
    with pytest.raises(ValueError, match="unknown"):
        _normalize_business_lines([{"code": "unknown", "name": "未知"}])


def test_normalize_rejects_invalid_code_format():
    with pytest.raises(ValueError):
        _normalize_business_lines([{"code": "my line", "name": "x"}])


def test_normalize_rejects_empty_name():
    with pytest.raises(ValueError):
        _normalize_business_lines([{"code": "terminal", "name": "   "}])


# ---- Config.set_value / toml 持久化 ----


def test_config_set_value_normalizes_business_lines(tmp_path):
    cfg = Config(save_dir=str(tmp_path))
    cfg.set_value("business_lines", [{"code": "diaodutai", "name": "调度台", "keywords": "mcx, 调度台"}])
    assert cfg.business_lines == [{"code": "diaodutai", "name": "调度台", "keywords": ["mcx", "调度台"]}]


def test_config_set_value_rejects_invalid_business_lines(tmp_path):
    cfg = Config(save_dir=str(tmp_path))
    rows = [{"code": "terminal", "name": "终端"}, {"code": "terminal", "name": "终端B"}]
    with pytest.raises(ValueError):
        cfg.set_value("business_lines", rows)


def test_valid_business_lines_in_toml_loads_as_is(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "base.toml").write_text(
        'business_lines = [{ code = "diaodutai", name = "调度台", keywords = ["调度台", "mcx"] }]\n',
        encoding="utf-8",
    )
    cfg = Config(save_dir=str(tmp_path))
    assert cfg.business_lines == [{"code": "diaodutai", "name": "调度台", "keywords": ["调度台", "mcx"]}]


def test_invalid_business_line_in_toml_falls_back_to_default(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "base.toml").write_text(
        'business_lines = [{ code = "unknown", name = "x" }]\n',
        encoding="utf-8",
    )
    cfg = Config(save_dir=str(tmp_path))
    assert [row["code"] for row in cfg.business_lines] == _DEFAULT_CODES


# ---- resolve_business_lines / sanitize_business_domain（读全局配置） ----


def test_resolve_and_sanitize_use_configured_list(monkeypatch: pytest.MonkeyPatch):
    _set_business_lines(
        monkeypatch,
        [
            {"code": "diaodutai", "name": "调度台", "keywords": []},
            {"code": "terminal", "name": "终端", "keywords": []},
        ],
    )
    assert [line.code for line in resolve_business_lines()] == ["diaodutai", "terminal"]
    assert sanitize_business_domain("terminal") == "terminal"
    assert sanitize_business_domain("diaodutai") == "diaodutai"


def test_sanitize_falls_back_to_unknown_for_unlisted_and_empty(monkeypatch: pytest.MonkeyPatch):
    _set_business_lines(monkeypatch, [{"code": "diaodutai", "name": "调度台", "keywords": []}])
    assert sanitize_business_domain("bogus") == "unknown"
    assert sanitize_business_domain("unknown") == "unknown"
    assert sanitize_business_domain(None) == "unknown"
    assert sanitize_business_domain("  ") == "unknown"


def test_sanitize_empty_config_returns_unknown(monkeypatch: pytest.MonkeyPatch):
    _set_business_lines(monkeypatch, [])
    assert sanitize_business_domain("terminal") == "unknown"
    assert resolve_business_lines() == []


# ---- customer_service_ids 绑定（跨字段引用） ----

URL_KF_A = "https://work.weixin.qq.com/kf/bind-a"


def test_default_rows_have_no_binding_key(tmp_path):
    cfg = Config(save_dir=str(tmp_path))
    assert all("customer_service_ids" not in row for row in cfg.business_lines)


def test_normalize_keeps_binding_only_when_nonempty():
    rows = _normalize_business_lines(
        [
            {"code": "diaodutai", "name": "调度台", "customer_service_ids": ["cs-a", "", "cs-a"]},
            {"code": "terminal", "name": "终端", "customer_service_ids": []},
        ]
    )
    diaodutai, terminal = rows
    assert diaodutai["customer_service_ids"] == ["cs-a"]
    assert "customer_service_ids" not in terminal


def test_set_value_binding_to_unknown_service_rejected(tmp_path):
    cfg = Config(save_dir=str(tmp_path))
    with pytest.raises(ValueError, match="不存在的客服"):
        cfg.set_value(
            "business_lines",
            [{"code": "diaodutai", "name": "调度台", "customer_service_ids": ["cs-ghost"]}],
        )
    # 失败后内存与之前一致（未写入）
    assert cfg.business_lines == Config(save_dir=str(tmp_path)).business_lines


def test_set_value_binding_ok_when_service_exists(tmp_path):
    cfg = Config(save_dir=str(tmp_path))
    cfg.set_value("wecom_customer_services", [{"id": "cs-a", "name": "客服A", "urls": [URL_KF_A]}])
    cfg.set_value(
        "business_lines",
        [{"code": "diaodutai", "name": "调度台", "customer_service_ids": ["cs-a"]}],
    )
    assert cfg.business_lines[0]["customer_service_ids"] == ["cs-a"]


def test_update_removing_bound_service_rolls_back(tmp_path):
    cfg = Config(save_dir=str(tmp_path))
    cfg.set_value("wecom_customer_services", [{"id": "cs-a", "name": "客服A", "urls": [URL_KF_A]}])
    cfg.set_value(
        "business_lines",
        [{"code": "diaodutai", "name": "调度台", "customer_service_ids": ["cs-a"]}],
    )
    snapshot = dict(cfg.business_lines[0])
    with pytest.raises(ValueError, match="不存在的客服"):
        cfg.update({"wecom_customer_services": []})
    assert cfg.wecom_customer_services == [{"id": "cs-a", "name": "客服A", "urls": [URL_KF_A]}]
    assert cfg.business_lines[0] == snapshot


def test_update_batch_adds_service_and_binding_order_independent(tmp_path):
    cfg = Config(save_dir=str(tmp_path))
    # 业务线先于客服提交：批处理末端统一跨字段校验，不依赖请求内键顺序。
    cfg.update(
        {
            "business_lines": [{"code": "diaodutai", "name": "调度台", "customer_service_ids": ["cs-b"]}],
            "wecom_customer_services": [{"id": "cs-b", "name": "客服B", "urls": [URL_KF_A]}],
        }
    )
    assert cfg.business_lines[0]["customer_service_ids"] == ["cs-b"]
