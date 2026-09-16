"""初始账号种子脚本：凭据只能来自环境变量，且不得内置。"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "seed_initial_users.py"


def load_seed_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("seed_initial_users", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_module_defines_no_builtin_credential_constants():
    module = load_seed_module()

    hardcoded = [name for name in vars(module) if name.endswith(("PASSWORD", "PHONE_NUMBER"))]

    assert hardcoded == []


def test_read_superadmin_credentials_requires_every_env(monkeypatch):
    module = load_seed_module()
    for env_name in (module.SUPERADMIN_NAME_ENV, module.SUPERADMIN_UID_ENV, module.SUPERADMIN_PASSWORD_ENV):
        monkeypatch.delenv(env_name, raising=False)

    with pytest.raises(module.SeedError):
        module.read_superadmin_credentials()


def test_read_superadmin_credentials_reports_every_missing_key(monkeypatch):
    module = load_seed_module()
    monkeypatch.setenv(module.SUPERADMIN_NAME_ENV, "系统管理员")
    monkeypatch.delenv(module.SUPERADMIN_UID_ENV, raising=False)
    monkeypatch.delenv(module.SUPERADMIN_PASSWORD_ENV, raising=False)

    with pytest.raises(module.SeedError) as excinfo:
        module.read_superadmin_credentials()

    assert module.SUPERADMIN_UID_ENV in str(excinfo.value)
    assert module.SUPERADMIN_PASSWORD_ENV in str(excinfo.value)


def test_read_superadmin_credentials_returns_configured_values(monkeypatch):
    module = load_seed_module()
    monkeypatch.setenv(module.SUPERADMIN_NAME_ENV, "系统管理员")
    monkeypatch.setenv(module.SUPERADMIN_UID_ENV, "admin")
    monkeypatch.setenv(module.SUPERADMIN_PASSWORD_ENV, "password-from-env")

    assert module.read_superadmin_credentials() == ("系统管理员", "admin", "password-from-env")


def test_read_demo_password_requires_env(monkeypatch):
    module = load_seed_module()
    monkeypatch.delenv(module.DEMO_USER_PASSWORD_ENV, raising=False)

    with pytest.raises(module.SeedError):
        module.read_demo_password()
