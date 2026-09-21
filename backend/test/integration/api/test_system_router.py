"""
Integration tests for system router endpoints.
"""

from __future__ import annotations

import pytest

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def test_health_endpoint_is_public(test_client):
    response = await test_client.get("/api/system/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


async def test_info_endpoint_is_public(test_client):
    response = await test_client.get("/api/system/info")
    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert "data" in payload


async def test_config_get_requires_login_and_update_requires_admin(test_client, standard_user):
    assert (await test_client.get("/api/system/config")).status_code == 401
    user_config_response = await test_client.get("/api/system/config", headers=standard_user["headers"])
    assert user_config_response.status_code == 200, user_config_response.text

    update_response = await test_client.post(
        "/api/system/config/update",
        json={"default_ocr_engine": "rapid_ocr"},
        headers=standard_user["headers"],
    )
    assert update_response.status_code == 403


async def test_integrations_status_requires_admin(test_client, standard_user, admin_headers):
    """生效值面板与设置页签同一道门：普通用户看不到凭证从哪来、值是什么。"""
    assert (await test_client.get("/api/system/integrations/status")).status_code == 401
    assert (
        await test_client.get("/api/system/integrations/status", headers=standard_user["headers"])
    ).status_code == 403

    response = await test_client.get("/api/system/integrations/status", headers=admin_headers)

    assert response.status_code == 200, response.text
    items = response.json()["items"]
    assert set(items) == {"tavily_api_key", "paddleocr_api_token", "paddleocr_api_url"}
    for item in items.values():
        assert item["source"] in {"settings", "env", "unset"}
        assert isinstance(item["value"], str)
        # source=unset 即无值，不得出现「来源未配置却带着值」的自相矛盾
        assert (item["value"] == "") == (item["source"] == "unset")


async def test_admin_can_fetch_config_and_reload_info(test_client, admin_headers):
    config_response = await test_client.get("/api/system/config", headers=admin_headers)
    assert config_response.status_code == 200, config_response.text
    assert isinstance(config_response.json(), dict)

    reload_response = await test_client.post("/api/system/info/reload", headers=admin_headers)
    assert reload_response.status_code == 200, reload_response.text
    reload_payload = reload_response.json()
    assert reload_payload["success"] is True
    assert "data" in reload_payload


async def test_admin_can_fetch_tools_with_config_guide_field(test_client, admin_headers):
    response = await test_client.get("/api/system/tools", headers=admin_headers)
    assert response.status_code == 200, response.text

    payload = response.json()
    assert payload["success"] is True
    assert isinstance(payload["data"], list)
    assert payload["data"]
    assert "config_guide" in payload["data"][0]
