"""
Integration tests for department management API routes.
"""

from __future__ import annotations

import uuid

import pytest

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


async def test_superadmin_can_delete_department_with_users(test_client, admin_headers, hard_delete_test_users):
    suffix = uuid.uuid4().hex[:8]
    department_payload = {
        "name": f"pytest_department_{suffix}",
        "description": "integration test department",
        "admin_uid": f"pta_{suffix}",
        "admin_password": "RouterDept123!",
    }
    user_payload = {
        "username": f"dept_user_{suffix}",
        "password": "RouterUser123!",
        "role": "user",
    }

    department_id = None
    created_user_id = None
    department_admin_id = None

    try:
        create_department_response = await test_client.post(
            "/api/departments",
            json=department_payload,
            headers=admin_headers,
        )
        assert create_department_response.status_code == 201, create_department_response.text
        department_id = create_department_response.json()["id"]

        create_user_response = await test_client.post(
            "/api/auth/users",
            json={**user_payload, "department_id": department_id},
            headers=admin_headers,
        )
        assert create_user_response.status_code == 200, create_user_response.text
        created_user_id = create_user_response.json()["id"]

        list_users_response = await test_client.get("/api/auth/users", headers=admin_headers)
        assert list_users_response.status_code == 200, list_users_response.text
        users_before_delete = list_users_response.json()
        department_admin = next(
            (user for user in users_before_delete if user["uid"] == department_payload["admin_uid"]), None
        )
        assert department_admin is not None
        department_admin_id = department_admin["id"]

        delete_department_response = await test_client.delete(
            f"/api/departments/{department_id}", headers=admin_headers
        )
        assert delete_department_response.status_code == 200, delete_department_response.text
        assert delete_department_response.json()["success"] is True
        department_id = None

        deleted_department_response = await test_client.get(
            f"/api/departments/{create_department_response.json()['id']}",
            headers=admin_headers,
        )
        assert deleted_department_response.status_code == 404, deleted_department_response.text

        list_users_after_delete_response = await test_client.get("/api/auth/users", headers=admin_headers)
        assert list_users_after_delete_response.status_code == 200, list_users_after_delete_response.text
        users_after_delete = list_users_after_delete_response.json()

        # 删除部门后用户迁移到默认部门（代码中默认部门固定为 id=1，其名称可被用户修改）
        migrated_admin = next((user for user in users_after_delete if user["id"] == department_admin_id), None)
        assert migrated_admin is not None
        assert migrated_admin["department_id"] == 1

        migrated_user = next((user for user in users_after_delete if user["id"] == created_user_id), None)
        assert migrated_user is not None
        assert migrated_user["department_id"] == 1
    finally:
        cleanup_errors: list[Exception] = []
        user_ids = [user_id for user_id in (department_admin_id, created_user_id) if user_id is not None]
        try:
            for user_id in user_ids:
                response = await test_client.delete(f"/api/auth/users/{user_id}", headers=admin_headers)
                if response.status_code not in {200, 404}:
                    cleanup_errors.append(AssertionError(response.text))
        finally:
            try:
                await hard_delete_test_users(user_ids=user_ids)
            except Exception as exc:
                cleanup_errors.append(exc)
            if department_id is not None:
                response = await test_client.delete(f"/api/departments/{department_id}", headers=admin_headers)
                if response.status_code not in {200, 404}:
                    cleanup_errors.append(AssertionError(response.text))
        if cleanup_errors:
            raise cleanup_errors[0]


async def test_superadmin_cannot_delete_default_department(test_client, admin_headers):
    departments_response = await test_client.get("/api/departments", headers=admin_headers)
    assert departments_response.status_code == 200, departments_response.text
    departments = departments_response.json()

    # 默认部门在代码中固定为 id=1（受保护不可删除），其名称可被用户修改，故按 id 定位
    default_department = next((department for department in departments if department["id"] == 1), None)
    assert default_department is not None

    delete_response = await test_client.delete(f"/api/departments/{default_department['id']}", headers=admin_headers)
    assert delete_response.status_code == 400, delete_response.text
    assert delete_response.json()["detail"] == "默认部门不允许删除"
