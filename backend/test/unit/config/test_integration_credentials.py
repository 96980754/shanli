from __future__ import annotations

import importlib
from contextlib import contextmanager

import pytest
import tomli
from yuxi.config.app import (
    SOURCE_ENV,
    SOURCE_SETTINGS,
    SOURCE_UNSET,
    Config,
    describe_integration_credentials,
    is_usable_api_token,
    resolve_paddleocr_api_token,
    resolve_paddleocr_api_url,
    resolve_tavily_api_key,
)

pytestmark = pytest.mark.unit
config_cache = importlib.import_module("yuxi.config.cache")

CREDENTIAL_FIELDS = ("tavily_api_key", "paddleocr_api_token", "paddleocr_api_url")
ENV_NAMES = {
    "tavily_api_key": "TAVILY_API_KEY",
    "paddleocr_api_token": "PADDLEOCR_API_TOKEN",
    "paddleocr_api_url": "PADDLEOCR_API_URL",
}


class _RecordingRedis:
    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    def set(self, key: str, value: str) -> bool:
        self.data[key] = value
        return True


def _patch_runtime_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    """save() 同时往 Redis 写运行时快照：不连真 Redis，只记录（同 test_runtime_config_sync.py 的打桩）。"""

    @contextmanager
    def fake_sync_redis_client(*args, **kwargs):
        del args, kwargs
        yield _RecordingRedis()

    monkeypatch.setattr(config_cache, "sync_redis_client", fake_sync_redis_client)


@pytest.fixture()
def runtime_config(monkeypatch: pytest.MonkeyPatch) -> Config:
    """清空模块单例的三个凭证字段与同名环境变量：本机 base.toml / .env 存过值时不会串味。"""
    from yuxi.config.app import config

    for field in CREDENTIAL_FIELDS:
        monkeypatch.setattr(config, field, "", raising=False)
    for env_name in ENV_NAMES.values():
        monkeypatch.delenv(env_name, raising=False)
    return config


# =============================================================================
# === 取值与来源 ===
# =============================================================================


def test_settings_value_wins_over_env(runtime_config: Config, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-from-env")
    runtime_config.tavily_api_key = "  tvly-from-settings  "

    assert resolve_tavily_api_key() == "tvly-from-settings"
    assert describe_integration_credentials()["tavily_api_key"] == {
        "value": "tvly-from-settings",
        "source": SOURCE_SETTINGS,
    }


def test_blank_settings_value_falls_back_to_env(runtime_config: Config, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PADDLEOCR_API_TOKEN", "token-from-env")
    runtime_config.paddleocr_api_token = "   "

    assert resolve_paddleocr_api_token() == "token-from-env"
    assert describe_integration_credentials()["paddleocr_api_token"] == {
        "value": "token-from-env",
        "source": SOURCE_ENV,
    }


def test_no_value_anywhere_reports_unset(runtime_config: Config) -> None:
    assert resolve_paddleocr_api_url() == ""
    assert describe_integration_credentials()["paddleocr_api_url"] == {"value": "", "source": SOURCE_UNSET}


def test_describe_covers_all_three_credentials(runtime_config: Config) -> None:
    assert set(describe_integration_credentials()) == set(CREDENTIAL_FIELDS)


def test_cleared_credential_falls_back_to_env(runtime_config: Config, monkeypatch: pytest.MonkeyPatch) -> None:
    """清空设置页的值即退回环境变量——凭证配错后唯一的自救路径。"""
    monkeypatch.setenv("PADDLEOCR_API_TOKEN", "token-from-env")
    runtime_config.paddleocr_api_token = "token-from-settings"
    assert resolve_paddleocr_api_token() == "token-from-settings"

    runtime_config.set_value("paddleocr_api_token", "")

    assert resolve_paddleocr_api_token() == "token-from-env"
    assert describe_integration_credentials()["paddleocr_api_token"]["source"] == SOURCE_ENV


# =============================================================================
# === 凭据可用性判定 ===
# =============================================================================


@pytest.mark.parametrize(
    "value",
    [
        "",
        "# 获取搜索服务的 api key 请访问 https://tavily.com",
        "tvly-中文占位",
        "tvly-abc def",
    ],
)
def test_is_usable_api_token_rejects_placeholder_and_non_ascii(value: str) -> None:
    assert is_usable_api_token(value) is False


def test_is_usable_api_token_accepts_real_key() -> None:
    assert is_usable_api_token("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") is True


# =============================================================================
# === 持久化 ===
# =============================================================================


def test_credentials_persist_to_base_toml_and_echo_in_dump(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_runtime_redis(monkeypatch)
    config = Config(save_dir=str(tmp_path))
    config.tavily_api_key = "tvly-persisted"
    config.paddleocr_api_token = "token-persisted"
    config.paddleocr_api_url = "https://paddleocr.example.test/api/v2/ocr/jobs"
    config.save()

    with open(tmp_path / "config" / "base.toml", "rb") as f:
        saved = tomli.load(f)
    assert saved["paddleocr_api_url"] == "https://paddleocr.example.test/api/v2/ocr/jobs"

    reloaded = Config(save_dir=str(tmp_path))
    assert reloaded.tavily_api_key == "tvly-persisted"
    # 前提：两个 token 不做只写保护，像普通配置项一样可读回显（未列入 SECRET_CONFIG_FIELDS）
    dumped = reloaded.dump_config()
    assert dumped["paddleocr_api_token"] == "token-persisted"
    assert dumped["paddleocr_api_url"] == "https://paddleocr.example.test/api/v2/ocr/jobs"


def test_empty_credentials_never_land_in_base_toml(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """只配 .env 的存量部署升级后 base.toml 不该凭空多出这三项——多出来就会盖掉 .env。"""
    _patch_runtime_redis(monkeypatch)
    config = Config(save_dir=str(tmp_path))
    config.save()

    with open(tmp_path / "config" / "base.toml", "rb") as f:
        saved = tomli.load(f)
    assert not set(CREDENTIAL_FIELDS) & set(saved)
