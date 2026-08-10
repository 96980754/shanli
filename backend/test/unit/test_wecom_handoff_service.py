from yuxi.services.wecom_handoff_service import WeComCustomerService


def test_customer_service_requires_https_url():
    assert WeComCustomerService(lambda _key, _default: "").is_configured is False
    assert WeComCustomerService(lambda _key, _default: "http://example.com").is_configured is False


def test_customer_service_accepts_wecom_customer_service_url():
    service = WeComCustomerService(
        lambda key, default: "https://work.weixin.qq.com/kf/example" if key == "WECOM_CUSTOMER_SERVICE_URL" else default
    )
    assert service.is_configured is True
