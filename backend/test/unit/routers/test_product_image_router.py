"""knowledge_router 产品参照图管理端点的单测。"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest

import server.routers.knowledge_router as knowledge_router
from server.routers.knowledge_router import knowledge
from server.utils.auth_middleware import get_admin_user, get_db, get_required_user
from yuxi.storage.postgres.models_business import User


def _build_app(*, role: str = "admin") -> TestClient:
    app = FastAPI()
    app.include_router(knowledge, prefix="/api")

    async def fake_db():
        return None

    async def fake_required_user():
        return User(username=role, uid=role, password_hash="x", role=role, department_id=1)

    async def fake_admin_user():
        if role not in {"admin", "superadmin"}:
            raise HTTPException(status_code=403, detail="需要管理员权限")
        return await fake_required_user()

    app.dependency_overrides[get_db] = fake_db
    app.dependency_overrides[get_required_user] = fake_required_user
    app.dependency_overrides[get_admin_user] = fake_admin_user
    return TestClient(app)


class FakeIndex:
    """替代 ProductImageIndex：实例由 monkeypatch 工厂共享，可跨端点调用断言状态。"""

    def __init__(self, images: list[dict] | None = None, indexed: set[str] | None = None):
        self.images = images or []
        self.indexed = indexed or set()
        self.deleted: list[tuple[str, str]] = []

    def list_reference_images(self, kb_id: str) -> list[dict]:
        return self.images

    async def list_indexed(self, kb_id: str) -> set[str]:
        return self.indexed

    async def delete_image(self, kb_id: str, product: str) -> None:
        self.deleted.append((kb_id, product))


class FakeMinio:
    public_endpoint = "localhost:9000"

    def __init__(self):
        self.ensure_calls: list[str] = []
        self.deleted: list[tuple[str, str]] = []

    def ensure_bucket_exists(self, bucket: str) -> None:
        self.ensure_calls.append(bucket)

    async def adelete_file(self, bucket: str, object_name: str) -> None:
        self.deleted.append((bucket, object_name))


def _install_product_image_doubles(monkeypatch, *, index: FakeIndex | None = None, minio: FakeMinio | None = None):
    """把参照图端点依赖的权限/存在性校验关掉，并注入 FakeIndex / FakeMinio。"""

    async def noop(*args, **kwargs):
        return None

    monkeypatch.setattr(knowledge_router, "_require_product_image_kb", noop)
    if index is not None:
        monkeypatch.setattr("yuxi.knowledge.product_image_index.ProductImageIndex", lambda: index)
    if minio is not None:
        monkeypatch.setattr(knowledge_router, "get_minio_client", lambda: minio)


# ── 鉴权与辅助校验 ──────────────────────────────────────────────────


def test_non_admin_forbidden():
    response = _build_app(role="user").get("/api/knowledge/databases/kb_1/product-images")
    assert response.status_code == 403


async def test_require_product_image_kb_calls_permission_then_doc_check(monkeypatch):
    calls: list[tuple[str, str]] = []

    async def fake_permission(user, kb_id, action):
        calls.append(("perm", action))

    async def fake_doc_check(kb_id, operation):
        calls.append(("doc", operation))

    monkeypatch.setattr(knowledge_router, "_require_kb_permission", fake_permission)
    monkeypatch.setattr(knowledge_router, "_ensure_database_supports_documents", fake_doc_check)

    await knowledge_router._require_product_image_kb(
        User(username="admin", uid="admin", password_hash="x", role="admin", department_id=1),
        "kb_1",
    )
    assert calls == [("perm", "can_manage"), ("doc", "产品参照图管理")]


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("产品A.jpg", "产品A"),
        ("公模贴牌 v2.png", "公模贴牌 v2"),
        ("a/b/c.jpg", "c"),  # 路径会被 basename 剥离，仅保留文件名
    ],
)
def test_normalize_reference_product_name_keeps_chinese_and_strips_path(filename, expected):
    assert knowledge_router._normalize_reference_product_name(filename) == expected


@pytest.mark.parametrize("filename", ["", "a\\b.jpg", 'a"b.jpg'])
def test_normalize_reference_product_name_rejects_invalid(filename):
    with pytest.raises(HTTPException) as exc:
        knowledge_router._normalize_reference_product_name(filename)
    assert exc.value.status_code == 400


# ── 列表 ───────────────────────────────────────────────────────────


def test_list_product_images_merges_indexed_status(monkeypatch):
    index = FakeIndex(
        images=[
            {"object_name": "kb_1/product-images/产品A.jpg", "product": "产品A"},
            {"object_name": "kb_1/product-images/产品B.jpg", "product": "产品B"},
        ],
        indexed={"产品A"},
    )
    _install_product_image_doubles(monkeypatch, index=index, minio=FakeMinio())

    response = _build_app().get("/api/knowledge/databases/kb_1/product-images")

    assert response.status_code == 200, response.text
    images = response.json()["images"]
    assert images[0] == {
        "product": "产品A",
        "object_name": "kb_1/product-images/产品A.jpg",
        "image_url": "http://localhost:9000/public/kb_1/product-images/产品A.jpg",
        "indexed": True,
    }
    assert images[1]["indexed"] is False


# ── 跨库聚合列表 ───────────────────────────────────────────────────


def test_list_all_product_images_forbidden_for_non_admin():
    response = _build_app(role="user").get("/api/knowledge/product-images")
    assert response.status_code == 403


def test_list_all_product_images_aggregates_across_kbs(monkeypatch):
    class PerKbIndex(FakeIndex):
        def __init__(self):
            self.by_kb = {
                "kb_1": [{"object_name": "kb_1/product-images/产品A.jpg", "product": "产品A"}],
                "kb_2": [{"object_name": "kb_2/product-images/产品B.jpg", "product": "产品B"}],
            }
            self.indexed = {"产品A"}

        def list_reference_images(self, kb_id: str) -> list[dict]:
            return self.by_kb.get(kb_id, [])

    class FakeKbBase:
        async def get_databases_by_uid(self, uid, category_id=None):
            return {
                "databases": [
                    {"kb_id": "kb_1", "name": "客服知识库", "kb_type": "milvus"},
                    {"kb_id": "kb_2", "name": "产品库", "kb_type": "milvus"},
                    {"kb_id": "kb_3", "name": "连接器", "kb_type": "connector"},
                ]
            }

    monkeypatch.setattr(knowledge_router, "knowledge_base", FakeKbBase())
    _install_product_image_doubles(monkeypatch, index=PerKbIndex(), minio=FakeMinio())

    response = _build_app().get("/api/knowledge/product-images")

    assert response.status_code == 200, response.text
    images = response.json()["images"]
    assert len(images) == 2
    assert images[0] == {
        "kb_id": "kb_1",
        "kb_name": "客服知识库",
        "product": "产品A",
        "object_name": "kb_1/product-images/产品A.jpg",
        "image_url": "http://localhost:9000/public/kb_1/product-images/产品A.jpg",
        "indexed": True,
    }
    assert images[1]["kb_id"] == "kb_2"
    assert images[1]["kb_name"] == "产品库"
    assert images[1]["indexed"] is False


# ── 上传 ───────────────────────────────────────────────────────────


def test_upload_product_images_writes_to_public(monkeypatch):
    minio = FakeMinio()
    uploads: list[tuple[str, str]] = []

    async def fake_upload(bucket_name: str, object_name: str, data: bytes) -> str:
        uploads.append((bucket_name, object_name))
        return f"http://localhost:9000/{bucket_name}/{object_name}"

    monkeypatch.setattr(knowledge_router, "aupload_file_to_minio", fake_upload)
    _install_product_image_doubles(monkeypatch, minio=minio)

    response = _build_app().post(
        "/api/knowledge/databases/kb_1/product-images",
        files=[
            ("files", ("产品A.jpg", b"\xff\xd8fake", "image/jpeg")),
            ("files", ("产品B.png", b"pngdata", "image/png")),
        ],
    )

    assert response.status_code == 200, response.text
    assert uploads == [
        ("public", "kb_1/product-images/产品A.jpg"),
        ("public", "kb_1/product-images/产品B.png"),
    ]
    assert minio.ensure_calls == ["public"]  # 上传前重设匿名读策略
    products = {item["product"] for item in response.json()["images"]}
    assert products == {"产品A", "产品B"}


def test_upload_rejects_unsupported_extension(monkeypatch):
    _install_product_image_doubles(monkeypatch)

    response = _build_app().post(
        "/api/knowledge/databases/kb_1/product-images",
        files=[("files", ("notes.txt", b"hello", "text/plain"))],
    )

    assert response.status_code == 400
    assert "不支持的图片格式" in response.json()["detail"]


def test_upload_maps_oversize_error_to_400(monkeypatch):
    async def too_large(upload, *, max_size_bytes, too_large_message):
        raise ValueError("参照图过大，单张限 20 MB 以内")

    monkeypatch.setattr(knowledge_router, "read_upload_with_limit", too_large)
    _install_product_image_doubles(monkeypatch)

    response = _build_app().post(
        "/api/knowledge/databases/kb_1/product-images",
        files=[("files", ("big.jpg", b"x" * 100, "image/jpeg"))],
    )

    assert response.status_code == 400
    assert "20 MB" in response.json()["detail"]


# ── 删除 ───────────────────────────────────────────────────────────


def test_delete_product_image_removes_minio_object_and_vector(monkeypatch):
    index = FakeIndex(images=[{"object_name": "kb_1/product-images/产品A.jpg", "product": "产品A"}])
    minio = FakeMinio()
    _install_product_image_doubles(monkeypatch, index=index, minio=minio)

    response = _build_app().delete("/api/knowledge/databases/kb_1/product-images/%E4%BA%A7%E5%93%81A")

    assert response.status_code == 200, response.text
    assert minio.deleted == [("public", "kb_1/product-images/产品A.jpg")]
    assert index.deleted == [("kb_1", "产品A")]


def test_delete_missing_product_image_returns_404(monkeypatch):
    _install_product_image_doubles(monkeypatch, index=FakeIndex())

    response = _build_app().delete("/api/knowledge/databases/kb_1/product-images/%E4%BA%A7%E5%93%81A")

    assert response.status_code == 404


# ── 重建索引 ───────────────────────────────────────────────────────


def test_rebuild_calls_build_product_image_index(monkeypatch):
    async def fake_build(kb_id: str):
        assert kb_id == "kb_1"
        return {"kb_id": kb_id, "indexed": 2, "errors": 0}

    monkeypatch.setattr("yuxi.knowledge.product_image_index.build_product_image_index", fake_build)
    _install_product_image_doubles(monkeypatch)

    response = _build_app().post("/api/knowledge/databases/kb_1/product-images/rebuild")

    assert response.status_code == 200, response.text
    assert response.json() == {"kb_id": "kb_1", "indexed": 2, "errors": 0}
