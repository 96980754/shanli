import pytest

from yuxi.config.app import config
from yuxi.services.wecom_handoff_service import WeComCustomerService

URL_A = "https://work.weixin.qq.com/kf/a"
URL_B = "https://work.weixin.qq.com/kf/b"
INVALID_URL = "http://example.com"


def _reset_round_robin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("yuxi.services.wecom_handoff_service._ROUND_ROBIN_COUNTER", 0)


def test_customer_service_requires_https_url():
    assert WeComCustomerService(urls=[]).is_configured is False
    assert WeComCustomerService(urls=[INVALID_URL]).is_configured is False


def test_customer_service_accepts_https_urls():
    assert WeComCustomerService(urls=[URL_A]).is_configured is True


def test_get_url_round_robins_across_pool(monkeypatch: pytest.MonkeyPatch):
    """P1 反馈：多个客服入口时转人工轮替转接。"""
    _reset_round_robin(monkeypatch)
    service = WeComCustomerService(urls=[URL_A, URL_B])
    assert service.get_url() == URL_A
    assert service.get_url() == URL_B
    assert service.get_url() == URL_A


def test_get_url_ignores_invalid_pool_entries(monkeypatch: pytest.MonkeyPatch):
    _reset_round_robin(monkeypatch)
    service = WeComCustomerService(urls=[INVALID_URL, URL_A])
    assert service.get_url() == URL_A
    assert service.get_url() == URL_A


def test_get_url_domain_still_does_not_affect_routing(monkeypatch: pytest.MonkeyPatch):
    """domain 参数保留兼容：只做轮替，不按域挑 URL。"""
    _reset_round_robin(monkeypatch)
    service = WeComCustomerService(urls=[URL_A, URL_B])
    assert service.get_url("diaodutai") == URL_A
    assert service.get_url("terminal") == URL_B


def test_get_url_empty_when_nothing_configured():
    service = WeComCustomerService(urls=[])
    assert service.get_url() == ""
    assert service.get_url("diaodutai") == ""


def test_default_constructor_reads_app_config(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(config, "wecom_customer_service_urls", [URL_A])
    _reset_round_robin(monkeypatch)
    service = WeComCustomerService()
    assert service.is_configured is True
    assert service.get_url() == URL_A
    assert service.get_url("diaodutai") == URL_A


def test_default_constructor_empty_when_config_unset(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(config, "wecom_customer_service_urls", [])
    assert WeComCustomerService().is_configured is False
