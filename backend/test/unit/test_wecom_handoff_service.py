import json
from collections.abc import Callable

import pytest

from yuxi.config.app import config
from yuxi.services.wecom_handoff_service import WeComCustomerService

DOMAIN_URL = "https://work.weixin.qq.com/kf/diaodutai"
GLOBAL_URL = "https://work.weixin.qq.com/kf/default"


def _env(overrides: dict) -> Callable[[str, str], str]:
    def getenv(key: str, default: str = "") -> str:
        return overrides.get(key, default)

    return getenv


def _domain_service(domain_urls: dict | None = None, global_url: str = ""):
    overrides = {"WECOM_CUSTOMER_SERVICE_URL": global_url}
    if domain_urls is not None:
        overrides["WECOM_CUSTOMER_SERVICE_URLS"] = json.dumps(domain_urls)
    return WeComCustomerService(_env(overrides))


def test_customer_service_requires_https_url():
    assert WeComCustomerService(lambda _key, _default: "").is_configured is False
    assert WeComCustomerService(lambda _key, _default: "http://example.com").is_configured is False


def test_customer_service_accepts_wecom_customer_service_url():
    service = _domain_service(global_url=GLOBAL_URL)
    assert service.is_configured is True


def test_get_url_uses_domain_url_when_configured():
    service = _domain_service({"diaodutai": DOMAIN_URL})
    assert service.get_url("diaodutai") == DOMAIN_URL


def test_get_url_falls_back_to_global_for_unmapped_domain():
    service = _domain_service({"diaodutai": DOMAIN_URL}, global_url=GLOBAL_URL)
    assert service.get_url("terminal") == GLOBAL_URL


def test_get_url_empty_when_nothing_configured():
    service = _domain_service()
    assert service.get_url("diaodutai") == ""


def test_is_configured_true_with_only_domain_urls():
    service = _domain_service({"ops": "https://work.weixin.qq.com/kf/ops"})
    assert service.is_configured is True


def test_invalid_domain_urls_json_ignored():
    service = WeComCustomerService(_env({"WECOM_CUSTOMER_SERVICE_URLS": "not json at all"}))
    assert service.is_configured is False
    assert service.get_url("diaodutai") == ""


def test_non_https_domain_url_ignored_and_falls_back():
    service = _domain_service({"diaodutai": "http://insecure.example.com"}, global_url=GLOBAL_URL)
    assert service.get_url("diaodutai") == GLOBAL_URL


def test_default_constructor_reads_app_config(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(config, "wecom_customer_service_url", GLOBAL_URL)
    monkeypatch.setattr(config, "wecom_customer_service_urls", {"diaodutai": DOMAIN_URL})
    service = WeComCustomerService()
    assert service.is_configured is True
    assert service.get_url("diaodutai") == DOMAIN_URL
    assert service.get_url("terminal") == GLOBAL_URL


def test_default_constructor_empty_when_config_unset(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(config, "wecom_customer_service_url", "")
    monkeypatch.setattr(config, "wecom_customer_service_urls", {})
    assert WeComCustomerService().is_configured is False
