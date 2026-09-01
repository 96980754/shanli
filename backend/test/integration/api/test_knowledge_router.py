"""
Integration tests for knowledge router endpoints.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from urllib.parse import quote

import pytest
from yuxi.knowledge.chunking.ragflow_like.presets import CHUNK_PRESET_IDS

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


def _assert_forbidden_response(response):
    """验证 403 禁止访问响应的格式"""
    assert response.status_code == 403
    payload = response.json()
    assert "detail" in payload
    assert isinstance(payload["detail"], str)


async def _create_test_department(test_client, admin_headers, prefix="pytest_dept"):
    suffix = uuid.uuid4().hex[:8]
    admin_uid = f"deptadmin_{suffix}"
    response = await test_client.post(
        "/api/departments",
        json={
            "name": f"{prefix}_{suffix}",
            "description": "pytest department",
            "admin_uid": admin_uid,
            "admin_password": f"Pw!{suffix}",
        },
        headers=admin_headers,
    )
    assert response.status_code == 201, response.text
    payload = response.json()
    payload["admin_uid"] = admin_uid
    return payload


async def _create_test_user(test_client, admin_headers, department_id):
    suffix = uuid.uuid4().hex[:8]
    password = f"Pw!{suffix}"
    response = await test_client.post(
        "/api/auth/users",
        json={
            "username": f"pytest_user_{suffix}",
            "password": password,
            "role": "user",
            "department_id": department_id,
        },
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text
    user = response.json()

    login_response = await test_client.post(
        "/api/auth/token",
        data={"username": user["uid"], "password": password},
    )
    assert login_response.status_code == 200, login_response.text
    return {"user": user, "headers": {"Authorization": f"Bearer {login_response.json()['access_token']}"}}


async def _delete_user_by_id(test_client, admin_headers, user_id):
    response = await test_client.delete(f"/api/auth/users/{user_id}", headers=admin_headers)
    assert response.status_code in (200, 404), response.text


async def _find_user_id_by_uid(test_client, admin_headers, uid):
    response = await test_client.get("/api/auth/users", headers=admin_headers)
    assert response.status_code == 200, response.text
    for user in response.json():
        if user["uid"] == uid:
            return user["id"]
    return None


async def _delete_department_with_admin(test_client, admin_headers, department):
    admin_user_id = await _find_user_id_by_uid(test_client, admin_headers, department["admin_uid"])
    if admin_user_id:
        await _delete_user_by_id(test_client, admin_headers, admin_user_id)
    response = await test_client.delete(f"/api/departments/{department['id']}", headers=admin_headers)
    assert response.status_code in (200, 404), response.text


async def _create_test_database(test_client, admin_headers, share_config=None):
    response = await test_client.post(
        "/api/knowledge/databases",
        json={
            "database_name": f"pytest_acl_{uuid.uuid4().hex[:8]}",
            "description": "Knowledge permission test",
            "embedding_model_spec": "siliconflow-cn:Pro/BAAI/bge-m3",
            "kb_type": "milvus",
            "additional_params": {},
            "share_config": share_config,
        },
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _accessible_kb_ids(test_client, headers):
    response = await test_client.get("/api/knowledge/databases/accessible", headers=headers)
    assert response.status_code == 200, response.text
    return {item["kb_id"] for item in response.json().get("databases", [])}


async def test_admin_can_manage_knowledge_databases(test_client, admin_headers, knowledge_database):
    kb_id = knowledge_database["kb_id"]

    list_response = await test_client.get("/api/knowledge/databases", headers=admin_headers)
    assert list_response.status_code == 200, list_response.text
    databases = list_response.json().get("databases", [])
    assert any(entry["kb_id"] == kb_id for entry in databases)

    get_response = await test_client.get(f"/api/knowledge/databases/{kb_id}", headers=admin_headers)
    assert get_response.status_code == 200, get_response.text
    assert get_response.json()["kb_id"] == kb_id

    update_response = await test_client.put(
        f"/api/knowledge/databases/{kb_id}",
        json={"name": knowledge_database["name"], "description": "Updated by pytest"},
        headers=admin_headers,
    )
    assert update_response.status_code == 200, update_response.text
    assert update_response.json()["database"]["description"] == "Updated by pytest"


async def test_update_database_embedding_model_spec(test_client, admin_headers, knowledge_database):
    """运行时可切换嵌入模型（内外同款 bge-m3 向量兼容的前提）。"""
    kb_id = knowledge_database["kb_id"]
    assert knowledge_database["embedding_model_spec"] == "siliconflow-cn:Pro/BAAI/bge-m3"

    update_response = await test_client.put(
        f"/api/knowledge/databases/{kb_id}",
        json={
            "name": knowledge_database["name"],
            "description": "Updated embedding spec",
            "embedding_model_spec": "siliconflow-cn:BAAI/bge-m3",
        },
        headers=admin_headers,
    )
    assert update_response.status_code == 200, update_response.text
    assert update_response.json()["database"]["embedding_model_spec"] == "siliconflow-cn:BAAI/bge-m3"

    get_response = await test_client.get(f"/api/knowledge/databases/{kb_id}", headers=admin_headers)
    assert get_response.status_code == 200, get_response.text
    assert get_response.json()["embedding_model_spec"] == "siliconflow-cn:BAAI/bge-m3"

    # 未随请求提交的字段保持原值（model_fields_set 语义）
    omit_response = await test_client.put(
        f"/api/knowledge/databases/{kb_id}",
        json={"name": knowledge_database["name"], "description": "No spec in body"},
        headers=admin_headers,
    )
    assert omit_response.status_code == 200, omit_response.text
    assert omit_response.json()["database"]["embedding_model_spec"] == "siliconflow-cn:BAAI/bge-m3"

    # 非法 spec → 400
    bad_response = await test_client.put(
        f"/api/knowledge/databases/{kb_id}",
        json={
            "name": knowledge_database["name"],
            "description": "Bad spec",
            "embedding_model_spec": "no-such-provider:no-model",
        },
        headers=admin_headers,
    )
    assert bad_response.status_code == 400, bad_response.text


async def test_document_exists_returns_false_for_missing_relative_path(test_client, admin_headers, knowledge_database):
    kb_id = knowledge_database["kb_id"]
    filename = f"google_drive/shared_drives/engineering/serving-runtime/dsid_{uuid.uuid4().hex}__missing-playbook.txt"

    response = await test_client.get(
        f"/api/knowledge/databases/{kb_id}/documents/exists",
        params={"filename": filename},
        headers=admin_headers,
    )

    assert response.status_code == 200, response.text
    assert response.json() == {"kb_id": kb_id, "filename": filename, "exists": False}


async def test_create_database_with_chunk_preset(test_client, admin_headers):
    db_name = f"pytest_chunk_preset_{uuid.uuid4().hex[:6]}"
    payload = {
        "database_name": db_name,
        "description": "Chunk preset create test",
        "embedding_model_spec": "siliconflow-cn:Pro/BAAI/bge-m3",
        "kb_type": "milvus",
        "additional_params": {"chunk_preset_id": "book"},
    }

    create_response = await test_client.post("/api/knowledge/databases", json=payload, headers=admin_headers)
    assert create_response.status_code == 200, create_response.text
    kb_id = create_response.json()["kb_id"]

    info_response = await test_client.get(f"/api/knowledge/databases/{kb_id}", headers=admin_headers)
    assert info_response.status_code == 200, info_response.text
    assert info_response.json()["additional_params"]["chunk_preset_id"] == "book"

    delete_response = await test_client.delete(f"/api/knowledge/databases/{kb_id}", headers=admin_headers)
    assert delete_response.status_code == 200, delete_response.text


async def test_get_chunk_presets_returns_configured_options(test_client, admin_headers):
    response = await test_client.get("/api/knowledge/chunk-presets", headers=admin_headers)
    assert response.status_code == 200, response.text

    payload = response.json()
    options = payload["chunk_presets"]
    assert payload["message"] == "success"
    assert {option["value"] for option in options} == CHUNK_PRESET_IDS
    assert all(set(option) == {"value", "label", "description"} for option in options)
    assert all(option["label"] and option["description"] for option in options)


async def test_update_database_additional_params_merge_keeps_chunk_preset(
    test_client, admin_headers, knowledge_database
):
    kb_id = knowledge_database["kb_id"]

    first_update = await test_client.put(
        f"/api/knowledge/databases/{kb_id}",
        json={
            "name": knowledge_database["name"],
            "description": "update with chunk preset",
            "additional_params": {"chunk_preset_id": "qa"},
        },
        headers=admin_headers,
    )
    assert first_update.status_code == 200, first_update.text

    second_update = await test_client.put(
        f"/api/knowledge/databases/{kb_id}",
        json={
            "name": knowledge_database["name"],
            "description": "update without additional params",
        },
        headers=admin_headers,
    )
    assert second_update.status_code == 200, second_update.text

    info_response = await test_client.get(f"/api/knowledge/databases/{kb_id}", headers=admin_headers)
    assert info_response.status_code == 200, info_response.text
    assert info_response.json()["additional_params"]["chunk_preset_id"] == "qa"


async def test_knowledge_routes_enforce_permissions(test_client, standard_user, knowledge_database):
    kb_id = knowledge_database["kb_id"]

    forbidden_create = await test_client.post(
        "/api/knowledge/databases",
        json={
            "database_name": "unauthorized_db",
            "description": "Should not succeed",
            "embedding_model_spec": "siliconflow-cn:Pro/BAAI/bge-m3",
        },
        headers=standard_user["headers"],
    )
    _assert_forbidden_response(forbidden_create)

    forbidden_list = await test_client.get("/api/knowledge/databases", headers=standard_user["headers"])
    _assert_forbidden_response(forbidden_list)

    forbidden_chunk_presets = await test_client.get("/api/knowledge/chunk-presets", headers=standard_user["headers"])
    _assert_forbidden_response(forbidden_chunk_presets)

    forbidden_get = await test_client.get(f"/api/knowledge/databases/{kb_id}", headers=standard_user["headers"])
    _assert_forbidden_response(forbidden_get)

    forbidden_exists = await test_client.get(
        f"/api/knowledge/databases/{kb_id}/documents/exists",
        params={"filename": "demo.txt"},
        headers=standard_user["headers"],
    )
    _assert_forbidden_response(forbidden_exists)


async def test_admin_can_create_vector_db_with_reranker(test_client, admin_headers):
    """测试创建向量库并配置 reranker 参数（通过 query_params.options）

    注意：数据库清理由 conftest.py 中的 session fixture 自动处理。
    """
    db_name = f"pytest_rerank_{uuid.uuid4().hex[:6]}"
    payload = {
        "database_name": db_name,
        "description": "Vector DB with reranker",
        "embedding_model_spec": "siliconflow-cn:Pro/BAAI/bge-m3",
        "kb_type": "milvus",
        "additional_params": {},
    }

    create_response = await test_client.post("/api/knowledge/databases", json=payload, headers=admin_headers)
    assert create_response.status_code == 200, create_response.text

    db_payload = create_response.json()
    kb_id = db_payload["kb_id"]

    # 获取查询参数配置
    params_response = await test_client.get(f"/api/knowledge/databases/{kb_id}/query-params", headers=admin_headers)
    assert params_response.status_code == 200, params_response.text

    params_payload = params_response.json()
    options = params_payload.get("params", {}).get("options", [])
    options_by_key = {option.get("key"): option for option in options}

    expected_initial_params = {
        "search_mode": "hybrid",
        "final_top_k": 10,
        "similarity_threshold": 0.2,
        "bm25_top_k": 50,
        "vector_weight": 0.7,
        "bm25_weight": 0.3,
        "bm25_drop_ratio_search": 0.1,
        "use_graph_retrieval": True,
        "graph_entity_top_k": 10,
        "graph_triple_top_k": 20,
        "graph_max_nodes": 5000,
        "graph_top_k": 20,
        "graph_weight": 0.5,
        "ppr_damping": 0.85,
        "use_reranker": True,
        "recall_top_k": 50,
    }
    for key, expected in expected_initial_params.items():
        assert options_by_key[key].get("default") == expected

    assert options_by_key["reranker_model"].get("default") == ""

    # 保存查询参数（模拟前端配置）
    update_params = {
        "final_top_k": 5,
        "use_reranker": True,
        "recall_top_k": 20,
    }
    update_response = await test_client.put(
        f"/api/knowledge/databases/{kb_id}/query-params", json=update_params, headers=admin_headers
    )
    assert update_response.status_code == 200, update_response.text

    # 再次获取参数，验证保存成功
    params_response2 = await test_client.get(f"/api/knowledge/databases/{kb_id}/query-params", headers=admin_headers)
    assert params_response2.status_code == 200, params_response2.text

    params_payload2 = params_response2.json()
    options2 = params_payload2.get("params", {}).get("options", [])

    # 验证保存的值
    final_top_k_option2 = next((opt for opt in options2 if opt.get("key") == "final_top_k"), None)
    assert final_top_k_option2 is not None
    assert final_top_k_option2.get("default") == 5  # 保存的值

    use_reranker_option2 = next((opt for opt in options2 if opt.get("key") == "use_reranker"), None)
    assert use_reranker_option2 is not None
    assert use_reranker_option2.get("default") is True  # 保存的值


async def test_create_dify_database_success(test_client, admin_headers):
    db_name = f"pytest_dify_{uuid.uuid4().hex[:6]}"
    payload = {
        "database_name": db_name,
        "description": "Dify KB create test",
        "kb_type": "dify",
        "additional_params": {
            "dify_api_url": "https://api.dify.ai/v1",
            "dify_token": "test-token",
            "dify_dataset_id": "dataset-123",
        },
    }

    create_response = await test_client.post("/api/knowledge/databases", json=payload, headers=admin_headers)
    assert create_response.status_code == 200, create_response.text
    created_payload = create_response.json()
    kb_id = created_payload["kb_id"]
    assert created_payload["embedding_model_spec"] is None
    assert "chunk_preset_id" not in created_payload["metadata"]

    info_response = await test_client.get(f"/api/knowledge/databases/{kb_id}", headers=admin_headers)
    assert info_response.status_code == 200, info_response.text
    additional_params = info_response.json()["additional_params"]
    assert additional_params["dify_api_url"] == "https://api.dify.ai/v1"
    assert additional_params["dify_token"] == "test-token"
    assert additional_params["dify_dataset_id"] == "dataset-123"


async def test_create_dify_database_missing_params_failed(test_client, admin_headers):
    payload = {
        "database_name": f"pytest_dify_missing_{uuid.uuid4().hex[:6]}",
        "description": "Dify KB missing params",
        "kb_type": "dify",
        "additional_params": {
            "dify_api_url": "https://api.dify.ai/v1",
            "dify_token": "",
            "dify_dataset_id": "",
        },
    }

    response = await test_client.post("/api/knowledge/databases", json=payload, headers=admin_headers)
    assert response.status_code == 400, response.text
    assert "Dify 参数缺失" in response.json()["detail"]


async def test_create_dify_database_invalid_api_url_failed(test_client, admin_headers):
    payload = {
        "database_name": f"pytest_dify_bad_url_{uuid.uuid4().hex[:6]}",
        "description": "Dify KB invalid api url",
        "kb_type": "dify",
        "additional_params": {
            "dify_api_url": "https://api.dify.ai",
            "dify_token": "test-token",
            "dify_dataset_id": "dataset-123",
        },
    }

    response = await test_client.post("/api/knowledge/databases", json=payload, headers=admin_headers)
    assert response.status_code == 400, response.text
    assert "/v1" in response.json()["detail"]


async def test_dify_query_params_and_documents_readonly(test_client, admin_headers):
    payload = {
        "database_name": f"pytest_dify_ro_{uuid.uuid4().hex[:6]}",
        "description": "Dify readonly routes",
        "kb_type": "dify",
        "additional_params": {
            "dify_api_url": "https://api.dify.ai/v1",
            "dify_token": "test-token",
            "dify_dataset_id": "dataset-123",
        },
    }

    create_response = await test_client.post("/api/knowledge/databases", json=payload, headers=admin_headers)
    assert create_response.status_code == 200, create_response.text
    kb_id = create_response.json()["kb_id"]

    params_response = await test_client.get(f"/api/knowledge/databases/{kb_id}/query-params", headers=admin_headers)
    assert params_response.status_code == 200, params_response.text
    options = params_response.json().get("params", {}).get("options", [])
    option_keys = {item.get("key") for item in options}
    assert option_keys == {"search_mode", "final_top_k", "score_threshold_enabled", "similarity_threshold"}

    add_response = await test_client.post(
        f"/api/knowledge/databases/{kb_id}/documents",
        json={"items": ["/tmp/demo.txt"], "params": {"content_type": "file"}},
        headers=admin_headers,
    )
    assert add_response.status_code == 400, add_response.text
    assert "只支持检索" in add_response.json()["detail"]

    parse_response = await test_client.post(
        f"/api/knowledge/databases/{kb_id}/documents/parse",
        json=["file_id_1"],
        headers=admin_headers,
    )
    assert parse_response.status_code == 400, parse_response.text
    assert "只支持检索" in parse_response.json()["detail"]

    index_response = await test_client.post(
        f"/api/knowledge/databases/{kb_id}/documents/index",
        json={"file_ids": ["file_id_1"], "params": {}},
        headers=admin_headers,
    )
    assert index_response.status_code == 400, index_response.text
    assert "只支持检索" in index_response.json()["detail"]


# =============================================================================
# === Mindmap Tests ===
# =============================================================================


async def test_get_databases_overview(test_client, admin_headers, knowledge_database):
    """测试获取所有知识库概览"""
    response = await test_client.get("/api/knowledge/mindmap/databases", headers=admin_headers)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["message"] == "success"
    assert "databases" in payload
    assert "total" in payload

    # 验证知识库在列表中
    kb_ids = [db["kb_id"] for db in payload["databases"]]
    assert knowledge_database["kb_id"] in kb_ids


async def test_get_database_files(test_client, admin_headers, knowledge_database):
    """测试获取知识库文件列表"""
    kb_id = knowledge_database["kb_id"]
    response = await test_client.get(f"/api/knowledge/databases/{kb_id}/mindmap/files", headers=admin_headers)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["message"] == "success"
    assert payload["kb_id"] == kb_id
    assert "files" in payload
    assert "total" in payload
    assert payload["db_name"] == knowledge_database["name"]


async def test_get_database_files_not_found(test_client, admin_headers):
    """测试获取不存在的知识库文件列表"""
    response = await test_client.get("/api/knowledge/databases/nonexistent_kb_id/mindmap/files", headers=admin_headers)
    assert response.status_code == 404


async def test_generate_mindmap_empty_files(test_client, admin_headers, knowledge_database):
    """测试空文件列表生成思维导图"""
    kb_id = knowledge_database["kb_id"]
    response = await test_client.post(
        f"/api/knowledge/databases/{kb_id}/mindmap/generate",
        json={"file_ids": [], "user_prompt": ""},
        headers=admin_headers,
    )
    # 空文件应该返回400错误
    assert response.status_code == 400
    assert "中没有文件" in response.json()["detail"]


async def test_get_database_mindmap_not_exists(test_client, admin_headers, knowledge_database):
    """测试获取不存在的思维导图"""
    kb_id = knowledge_database["kb_id"]
    response = await test_client.get(f"/api/knowledge/databases/{kb_id}/mindmap", headers=admin_headers)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["kb_id"] == kb_id
    assert payload["mindmap"] is None  # 尚未生成思维导图


async def test_generate_and_get_mindmap(test_client, admin_headers, knowledge_database):
    """测试生成并获取思维导图

    注意：此测试需要知识库中有文件才能完整测试核心功能。
    由于没有前置的文件上传 fixture，测试会先验证空文件场景（预期400），
    然后使用 xfail 标记等待后续完善。
    """
    kb_id = knowledge_database["kb_id"]

    # 空文件场景 - 预期返回400错误
    generate_response = await test_client.post(
        f"/api/knowledge/databases/{kb_id}/mindmap/generate",
        json={"file_ids": [], "user_prompt": ""},
        headers=admin_headers,
    )
    assert generate_response.status_code == 400
    assert "中没有文件" in generate_response.json()["detail"]

    # 标记此测试需要文件上传支持才能完整执行
    pytest.skip("需要先上传文件才能完整测试思维导图生成功能")


# =============================================================================
# === Knowledge Router Additional Tests ===
# =============================================================================


async def test_get_accessible_databases(test_client, admin_headers, knowledge_database):
    """测试获取可访问的知识库列表"""
    response = await test_client.get("/api/knowledge/databases/accessible", headers=admin_headers)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert "databases" in payload

    # 验证知识库在列表中，且轻量投影保留卡片需要的字段
    databases = {db["kb_id"]: db for db in payload["databases"]}
    assert knowledge_database["kb_id"] in databases
    item = databases[knowledge_database["kb_id"]]
    assert "file_count" in item
    assert "created_at" in item
    assert "stats" not in item


async def test_create_database_defaults_to_global_share_config(test_client, admin_headers):
    database = await _create_test_database(test_client, admin_headers)
    kb_id = database["kb_id"]
    try:
        assert database["share_config"] == {"access_level": "global", "department_ids": [], "user_uids": []}
    finally:
        await test_client.delete(f"/api/knowledge/databases/{kb_id}", headers=admin_headers)


async def test_department_share_config_filters_accessible_databases(test_client, admin_headers):
    department_a = await _create_test_department(test_client, admin_headers, "pytest_dept_a")
    department_b = await _create_test_department(test_client, admin_headers, "pytest_dept_b")
    user_a = user_b = None
    database = None

    try:
        user_a = await _create_test_user(test_client, admin_headers, department_a["id"])
        user_b = await _create_test_user(test_client, admin_headers, department_b["id"])
        database = await _create_test_database(
            test_client,
            admin_headers,
            {"access_level": "department", "department_ids": [department_a["id"]], "user_uids": []},
        )

        saved_config = database["share_config"]
        assert saved_config["access_level"] == "department"
        assert department_a["id"] in saved_config["department_ids"]

        assert database["kb_id"] in await _accessible_kb_ids(test_client, user_a["headers"])
        assert database["kb_id"] not in await _accessible_kb_ids(test_client, user_b["headers"])
    finally:
        if database:
            await test_client.delete(f"/api/knowledge/databases/{database['kb_id']}", headers=admin_headers)
        if user_a:
            await _delete_user_by_id(test_client, admin_headers, user_a["user"]["id"])
        if user_b:
            await _delete_user_by_id(test_client, admin_headers, user_b["user"]["id"])
        await _delete_department_with_admin(test_client, admin_headers, department_a)
        await _delete_department_with_admin(test_client, admin_headers, department_b)


async def test_user_share_config_filters_accessible_databases(test_client, admin_headers):
    department_a = await _create_test_department(test_client, admin_headers, "pytest_dept_a")
    department_b = await _create_test_department(test_client, admin_headers, "pytest_dept_b")
    user_a = user_b = None
    database = None

    try:
        user_a = await _create_test_user(test_client, admin_headers, department_a["id"])
        user_b = await _create_test_user(test_client, admin_headers, department_b["id"])
        database = await _create_test_database(
            test_client,
            admin_headers,
            {"access_level": "user", "department_ids": [], "user_uids": [user_a["user"]["uid"]]},
        )

        saved_config = database["share_config"]
        assert saved_config["access_level"] == "user"
        assert user_a["user"]["uid"] in saved_config["user_uids"]

        assert database["kb_id"] in await _accessible_kb_ids(test_client, user_a["headers"])
        assert database["kb_id"] not in await _accessible_kb_ids(test_client, user_b["headers"])
    finally:
        if database:
            await test_client.delete(f"/api/knowledge/databases/{database['kb_id']}", headers=admin_headers)
        if user_a:
            await _delete_user_by_id(test_client, admin_headers, user_a["user"]["id"])
        if user_b:
            await _delete_user_by_id(test_client, admin_headers, user_b["user"]["id"])
        await _delete_department_with_admin(test_client, admin_headers, department_a)
        await _delete_department_with_admin(test_client, admin_headers, department_b)


async def test_user_access_options_include_all_departments_for_admin(test_client, admin_headers):
    department = await _create_test_department(test_client, admin_headers, "pytest_access_options")
    user = None

    try:
        user = await _create_test_user(test_client, admin_headers, department["id"])
        response = await test_client.get("/api/auth/users/access-options", headers=admin_headers)
        assert response.status_code == 200, response.text
        uids = {item["uid"] for item in response.json()}
        assert user["user"]["uid"] in uids
        assert department["admin_uid"] in uids
    finally:
        if user:
            await _delete_user_by_id(test_client, admin_headers, user["user"]["id"])
        await _delete_department_with_admin(test_client, admin_headers, department)


async def test_get_knowledge_base_types(test_client, admin_headers):
    """测试获取支持的知识库类型"""
    response = await test_client.get("/api/knowledge/types", headers=admin_headers)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["message"] == "success"
    assert "kb_types" in payload
    assert "default_config" not in payload["kb_types"]["dify"]
    assert payload["kb_types"]["dify"]["name"] == "Dify"
    assert payload["kb_types"]["dify"]["description"] == "连接 Dify Dataset 的只读检索知识库"
    assert payload["kb_types"]["dify"]["requires_embedding_model"] is False
    assert payload["kb_types"]["dify"]["supports_documents"] is False
    assert [option["key"] for option in payload["kb_types"]["dify"]["create_params"]["options"]] == [
        "dify_api_url",
        "dify_token",
        "dify_dataset_id",
    ]
    assert "default_config" not in payload["kb_types"]["notion"]
    assert payload["kb_types"]["notion"]["name"] == "Notion"
    assert (
        payload["kb_types"]["notion"]["description"]
        == "连接 Notion Data Source 的只读知识库，支持检索、打开页面和页内查找"
    )
    assert payload["kb_types"]["notion"]["requires_embedding_model"] is False
    assert payload["kb_types"]["notion"]["supports_documents"] is False
    assert [option["key"] for option in payload["kb_types"]["notion"]["create_params"]["options"]] == [
        "notion_token",
        "notion_data_source_id",
        "notion_version",
    ]


async def test_get_knowledge_base_statistics(test_client, admin_headers):
    """测试获取知识库统计信息"""
    response = await test_client.get("/api/knowledge/stats", headers=admin_headers)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["message"] == "success"
    assert "stats" in payload


async def test_get_supported_file_types(test_client, admin_headers):
    """测试获取支持的文件类型"""
    response = await test_client.get("/api/knowledge/files/supported-types", headers=admin_headers)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["message"] == "success"
    assert "file_types" in payload
    assert isinstance(payload["file_types"], list)


async def test_markdown_endpoint_parses_uploaded_text_file(test_client, admin_headers):
    """测试 /files/markdown 能解析上传文件并返回 markdown。"""
    data_dir = Path(__file__).resolve().parents[2] / "data"
    test_file = data_dir / "A_Dream_of_Red_Mansions_10hui.txt"

    assert test_file.exists(), f"测试文件不存在: {test_file}"

    with test_file.open("rb") as f:
        response = await test_client.post(
            "/api/knowledge/files/markdown",
            headers=admin_headers,
            files={"file": (test_file.name, f, "text/plain")},
        )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["message"] == "success"
    assert isinstance(payload.get("markdown_content"), str)
    assert payload["markdown_content"].strip()


async def test_uploaded_file_preview_by_path(test_client, admin_headers, knowledge_database):
    """上传后尚无 file_id，按 MinIO 路径即可预览（/files/preview）。"""
    kb_id = knowledge_database["kb_id"]
    data_dir = Path(__file__).resolve().parents[2] / "data"
    test_file = data_dir / "A_Dream_of_Red_Mansions_10hui.txt"
    assert test_file.exists(), f"测试文件不存在: {test_file}"

    with test_file.open("rb") as f:
        upload_response = await test_client.post(
            "/api/knowledge/files/upload",
            headers=admin_headers,
            params={"kb_id": kb_id},
            files={"file": (test_file.name, f, "text/plain")},
        )
    assert upload_response.status_code == 200, upload_response.text
    file_path = upload_response.json()["file_path"]

    # 正常预览：文本文件返回 JSON payload
    preview_response = await test_client.get(
        "/api/knowledge/files/preview",
        headers=admin_headers,
        params={"kb_id": kb_id, "file_path": file_path, "filename": test_file.name},
    )
    assert preview_response.status_code == 200, preview_response.text
    payload = preview_response.json()
    assert payload["supported"] is True
    assert "binary" not in payload, "文本预览走 JSON payload，不应有 binary 字段"
    assert payload["preview_type"] == "text"
    assert payload["content"]

    # 越权路径拒绝：parsed 前缀不属于 upload，禁止预览
    bad_path = file_path.replace(f"{kb_id}/upload/", f"{kb_id}/parsed/")
    forbidden = await test_client.get(
        "/api/knowledge/files/preview",
        headers=admin_headers,
        params={"kb_id": kb_id, "file_path": bad_path},
    )
    assert forbidden.status_code == 400
    assert "只能预览当前知识库已上传的文件" in forbidden.json()["detail"]


async def test_duplicate_database_name(test_client, admin_headers, knowledge_database):
    """测试重复创建同名知识库"""
    db_name = knowledge_database["name"]
    response = await test_client.post(
        "/api/knowledge/databases",
        json={
            "database_name": db_name,
            "description": "Duplicate name test",
            "embedding_model_spec": "siliconflow-cn:Pro/BAAI/bge-m3",
            "kb_type": "milvus",
            "additional_params": {},
        },
        headers=admin_headers,
    )
    assert response.status_code == 409
    assert "已存在" in response.json()["detail"]


async def test_create_lightrag_knowledge_base_is_unsupported(test_client, admin_headers):
    db_name = f"pytest_lightrag_{uuid.uuid4().hex[:6]}"
    response = await test_client.post(
        "/api/knowledge/databases",
        json={
            "database_name": db_name,
            "description": "Unsupported LightRAG knowledge base",
            "embedding_model_spec": "siliconflow-cn:Pro/BAAI/bge-m3",
            "kb_type": "lightrag",
            "additional_params": {},
        },
        headers=admin_headers,
    )
    assert response.status_code == 400
    assert "Unsupported knowledge base type: lightrag" in response.json()["detail"]


async def test_create_milvus_knowledge_base(test_client, admin_headers):
    """测试创建 Milvus 知识库

    注意：数据库清理由 conftest.py 中的 session fixture 自动处理。
    """
    db_name = f"pytest_milvus_{uuid.uuid4().hex[:6]}"
    payload = {
        "database_name": db_name,
        "description": "Pytest Milvus knowledge base",
        "embedding_model_spec": "siliconflow-cn:Pro/BAAI/bge-m3",
        "kb_type": "milvus",
        "additional_params": {},
    }

    create_response = await test_client.post("/api/knowledge/databases", json=payload, headers=admin_headers)
    assert create_response.status_code == 200, create_response.text

    db_payload = create_response.json()
    assert db_payload["kb_type"] == "milvus"


async def test_sample_questions_endpoints(test_client, admin_headers, knowledge_database):
    """测试示例问题接口（空文件时预期返回400）"""
    kb_id = knowledge_database["kb_id"]

    # 获取示例问题（空知识库应该返回空列表）
    get_response = await test_client.get(f"/api/knowledge/databases/{kb_id}/sample-questions", headers=admin_headers)
    assert get_response.status_code == 200, get_response.text
    get_payload = get_response.json()
    assert get_payload["kb_id"] == kb_id
    assert "questions" in get_payload
    assert get_payload["count"] == 0  # 空知识库没有问题

    # 生成示例问题（空知识库应该返回400）
    generate_response = await test_client.post(
        f"/api/knowledge/databases/{kb_id}/sample-questions",
        json={"count": 5},
        headers=admin_headers,
    )
    assert generate_response.status_code == 400
    assert "中没有文件" in generate_response.json()["detail"]


async def test_mindmap_permissions(test_client, standard_user, knowledge_database):
    """测试思维导图接口的权限控制"""
    kb_id = knowledge_database["kb_id"]

    # 普通用户应该无法访问
    forbidden_list = await test_client.get("/api/knowledge/mindmap/databases", headers=standard_user["headers"])
    _assert_forbidden_response(forbidden_list)

    forbidden_files = await test_client.get(
        f"/api/knowledge/databases/{kb_id}/mindmap/files", headers=standard_user["headers"]
    )
    _assert_forbidden_response(forbidden_files)

    forbidden_generate = await test_client.post(
        f"/api/knowledge/databases/{kb_id}/mindmap/generate",
        json={"file_ids": []},
        headers=standard_user["headers"],
    )
    _assert_forbidden_response(forbidden_generate)


async def test_move_folder_to_root_accepts_null_parent(
    test_client, admin_headers, knowledge_database
):
    """移动到根目录时 new_parent_id 传 null 不应被 body 校验拒绝（回归 body.new_parent_id: Field required）"""
    kb_id = knowledge_database["kb_id"]

    create_response = await test_client.post(
        f"/api/knowledge/databases/{kb_id}/folders",
        json={"folder_name": f"sub_{uuid.uuid4().hex[:8]}", "parent_id": None},
        headers=admin_headers,
    )
    assert create_response.status_code == 200, create_response.text
    folder_id = create_response.json()["file_id"]

    # 显式传 null 移动到根目录：修复前必填 Body(...) 把 null 当作缺失 → 422
    move_response = await test_client.put(
        f"/api/knowledge/databases/{kb_id}/documents/{folder_id}/move",
        json={"new_parent_id": None},
        headers=admin_headers,
    )
    assert move_response.status_code == 200, move_response.text
    assert move_response.json()["parent_id"] is None

    # 移动到真实文件夹路径不受影响（可选项校验仍在）
    move_back_response = await test_client.put(
        f"/api/knowledge/databases/{kb_id}/documents/{folder_id}/move",
        json={"new_parent_id": folder_id},
        headers=admin_headers,
    )
    assert move_back_response.status_code == 400
    assert "into itself" in move_back_response.json()["detail"]


# =============================================================================
# === 重命名（入库后文件/文件夹改名）Tests ===
# =============================================================================


def _local_db_engine():
    """返回绑定当前事件循环的独立 async 引擎。

    conftest 的 session 级 fixture 在 anyio.run 自己的循环里初始化了 pg_manager，
    测试函数循环里直接复用该引擎会因跨 loop 报「Event loop is closed」。这里用
    容器继承的 POSTGRES_URL 建独立引擎，用完即 dispose。
    """
    import os

    from sqlalchemy.ext.asyncio import create_async_engine

    url = os.environ.get("POSTGRES_URL") or "postgresql+asyncpg://postgres:postgres@postgres:5432/yuxi"
    return create_async_engine(url)


async def _seed_knowledge_file(
    kb_id: str,
    *,
    file_id: str,
    filename: str,
    is_folder: bool = False,
    parent_id: str | None = None,
) -> None:
    """直接落库一条 knowledge_files 记录，供改名测试构造数据（绕过完整上传/解析链路）。"""
    from sqlalchemy import delete

    from yuxi.repositories.knowledge_file_repository import normalize_document_filename
    from yuxi.storage.postgres.models_knowledge import KnowledgeFile

    engine = _local_db_engine()
    try:
        async with engine.begin() as conn:
            # 清理同 file_id 残留（幂等）
            await conn.execute(delete(KnowledgeFile).where(KnowledgeFile.file_id == file_id))
            await conn.execute(
                KnowledgeFile.__table__.insert().values(
                    file_id=file_id,
                    kb_id=kb_id,
                    filename=filename,
                    normalized_name=normalize_document_filename(filename),
                    is_folder=is_folder,
                    parent_id=parent_id,
                    is_current=True,
                    status="done",
                    view_count=0,
                    processing_progress=0,
                    processing_task_attempt=0,
                )
            )
    finally:
        await engine.dispose()


async def _get_filename_by_id(kb_id: str, file_id: str) -> str | None:
    from sqlalchemy import select

    from yuxi.storage.postgres.models_knowledge import KnowledgeFile

    engine = _local_db_engine()
    try:
        async with engine.begin() as conn:
            row = (
                await conn.execute(
                    select(KnowledgeFile.filename).where(KnowledgeFile.kb_id == kb_id, KnowledgeFile.file_id == file_id)
                )
            ).scalar_one_or_none()
            return row
    finally:
        await engine.dispose()


async def test_rename_real_file_success(test_client, admin_headers, knowledge_database):
    """根目录真实文件改名：仅改 filename，且不改动其它字段。"""
    kb_id = knowledge_database["kb_id"]
    file_id = f"f_{uuid.uuid4().hex[:12]}"
    await _seed_knowledge_file(kb_id, file_id=file_id, filename="report.docx")

    response = await test_client.put(
        f"/api/knowledge/databases/{kb_id}/documents/{file_id}/rename",
        json={"filename": "年度报告.docx"},
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["filename"] == "年度报告.docx"
    assert await _get_filename_by_id(kb_id, file_id) == "年度报告.docx"


async def test_rename_file_keeps_virtual_dir_prefix(test_client, admin_headers, knowledge_database):
    """虚拟目录下的文件改名：保留目录前缀，只替换叶子。"""
    kb_id = knowledge_database["kb_id"]
    file_id = f"f_{uuid.uuid4().hex[:12]}"
    await _seed_knowledge_file(kb_id, file_id=file_id, filename="poc资料/readme.md")

    response = await test_client.put(
        f"/api/knowledge/databases/{kb_id}/documents/{file_id}/rename",
        json={"filename": "intro.md"},
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["filename"] == "poc资料/intro.md"


async def test_rename_real_folder_success_no_cascade(test_client, admin_headers, knowledge_database):
    """真实文件夹改名：只改文件夹行，子文件靠 parent_id 关联无需级联。"""
    kb_id = knowledge_database["kb_id"]
    folder_id = f"f_{uuid.uuid4().hex[:12]}"
    child_id = f"f_{uuid.uuid4().hex[:12]}"
    await _seed_knowledge_file(kb_id, file_id=folder_id, filename="docs", is_folder=True)
    await _seed_knowledge_file(kb_id, file_id=child_id, filename="readme.md", parent_id=folder_id)

    response = await test_client.put(
        f"/api/knowledge/databases/{kb_id}/documents/{folder_id}/rename",
        json={"filename": "documentation"},
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text
    assert response.json()["filename"] == "documentation"
    assert await _get_filename_by_id(kb_id, child_id) == "readme.md"


async def test_rename_virtual_folder_rewrites_prefix(test_client, admin_headers, knowledge_database):
    """虚拟文件夹改名：级联重写其下所有当前版本文件的前缀。"""
    kb_id = knowledge_database["kb_id"]
    ids = [f"f_{uuid.uuid4().hex[:12]}" for _ in range(2)]
    await _seed_knowledge_file(kb_id, file_id=ids[0], filename="poc资料/readme.md")
    await _seed_knowledge_file(kb_id, file_id=ids[1], filename="poc资料/细则/rule.docx")
    virtual_folder_id = "__virtual_folder__:root:poc资料/"

    response = await test_client.put(
        f"/api/knowledge/databases/{kb_id}/documents/{quote(virtual_folder_id.rstrip('/'))}/rename",
        json={"filename": "资料库"},
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text
    assert await _get_filename_by_id(kb_id, ids[0]) == "资料库/readme.md"
    assert await _get_filename_by_id(kb_id, ids[1]) == "资料库/细则/rule.docx"


async def test_rename_duplicate_file_rejected(test_client, admin_headers, knowledge_database):
    """同目录重名报错：改名成既有文件名返回 400，且不落库。"""
    kb_id = knowledge_database["kb_id"]
    id_a = f"f_{uuid.uuid4().hex[:12]}"
    id_b = f"f_{uuid.uuid4().hex[:12]}"
    await _seed_knowledge_file(kb_id, file_id=id_a, filename="a.docx")
    await _seed_knowledge_file(kb_id, file_id=id_b, filename="b.docx")

    response = await test_client.put(
        f"/api/knowledge/databases/{kb_id}/documents/{id_a}/rename",
        json={"filename": "b.docx"},
        headers=admin_headers,
    )
    assert response.status_code == 400, response.text
    assert "已存在同名文件" in response.json()["detail"]
    assert await _get_filename_by_id(kb_id, id_a) == "a.docx"


async def test_rename_virtual_folder_collision_rejected(test_client, admin_headers, knowledge_database):
    """虚拟文件夹改名目标前缀已存在 → 400。"""
    kb_id = knowledge_database["kb_id"]
    id_a = f"f_{uuid.uuid4().hex[:12]}"
    id_b = f"f_{uuid.uuid4().hex[:12]}"
    await _seed_knowledge_file(kb_id, file_id=id_a, filename="dir/one.docx")
    await _seed_knowledge_file(kb_id, file_id=id_b, filename="dir2/two.docx")

    response = await test_client.put(
        f"/api/knowledge/databases/{kb_id}/documents/{quote('__virtual_folder__:root:dir/'.rstrip('/'))}/rename",
        json={"filename": "dir2"},
        headers=admin_headers,
    )
    assert response.status_code == 400, response.text
    assert "已存在同名目录或文件" in response.json()["detail"]


async def test_rename_invalid_name_rejected(test_client, admin_headers, knowledge_database):
    """非法名（含路径分隔符 / 纯空白）→ 400。"""
    kb_id = knowledge_database["kb_id"]
    file_id = f"f_{uuid.uuid4().hex[:12]}"
    await _seed_knowledge_file(kb_id, file_id=file_id, filename="a.docx")

    slash_response = await test_client.put(
        f"/api/knowledge/databases/{kb_id}/documents/{file_id}/rename",
        json={"filename": "a/b.docx"},
        headers=admin_headers,
    )
    assert slash_response.status_code == 400, slash_response.text

    empty_response = await test_client.put(
        f"/api/knowledge/databases/{kb_id}/documents/{file_id}/rename",
        json={"filename": "  "},
        headers=admin_headers,
    )
    assert empty_response.status_code == 400, empty_response.text
    assert await _get_filename_by_id(kb_id, file_id) == "a.docx"
