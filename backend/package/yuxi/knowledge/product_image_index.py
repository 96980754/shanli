"""产品参照图视觉索引。

把知识库每款产品的精选参照图向量化，存 Milvus，用于"按外观检索产品"（贴牌/无标识场景的信号②）。

参照图约定：MinIO `public` 桶下 `{kb_id}/product-images/{产品名}.jpg`（每款产品一张，文件名即产品名）。
视觉特征模型：SiliconFlow `Qwen/Qwen3-VL-Embedding-8B`（支持图片输入，768 维）。

说明：图片本体留在 MinIO 对象存储，本模块只把特征向量写入向量库，不复制、不移动图片。
"""

from __future__ import annotations

import asyncio
import base64
import os
from typing import Any

import httpx
from pymilvus import Collection, CollectionSchema, DataType, FieldSchema, connections, db, utility

from yuxi.storage.minio.client import MinIOClient
from yuxi.utils import hashstr, logger

COLLECTION_NAME = "product_images"
IMAGE_DIM = 768
VL_EMBEDDING_MODEL = "Qwen/Qwen3-VL-Embedding-8B"
VL_EMBEDDING_URL = "https://api.siliconflow.cn/v1/embeddings"
VL_EMBEDDING_BATCH = 8
REFERENCE_IMAGE_PREFIX = "product-images"
_IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp", ".bmp")


async def _vl_embed(images: list[bytes | str]) -> list[list[float]]:
    """把图片转成视觉向量。元素为原始字节、data URI 或可访问的图片 URL。"""
    inputs: list[dict[str, str]] = []
    for item in images:
        if isinstance(item, bytes):
            inputs.append({"image": f"data:image/jpeg;base64,{base64.b64encode(item).decode()}"})
        else:
            inputs.append({"image": item})
    api_key = os.getenv("SILICONFLOW_API_KEY", "")
    if not api_key:
        raise ValueError("SILICONFLOW_API_KEY 未配置，无法使用视觉特征模型")
    async with httpx.AsyncClient() as client:
        response = await client.post(
            VL_EMBEDDING_URL,
            json={"model": VL_EMBEDDING_MODEL, "input": inputs, "dimensions": IMAGE_DIM},
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            timeout=180,
        )
    response.raise_for_status()
    data = response.json()
    return [item["embedding"] for item in data["data"]]


class ProductImageIndex:
    """产品参照图视觉索引：建索引、按外观检索。

    使用 pymilvus 直连独立的 `product_images` 集合，与文本检索层（按 kb_id 建集合）完全隔离。
    """

    def __init__(self) -> None:
        self.milvus_uri = os.getenv("MILVUS_URI", "http://localhost:19530")
        self.milvus_token = os.getenv("MILVUS_TOKEN", "")
        self.milvus_db = os.getenv("MILVUS_DB_NAME", "yuxi")
        self._alias = "yuxi_product_image"
        self._ensure_connection()

    def _ensure_connection(self) -> None:
        connections.connect(alias=self._alias, uri=self.milvus_uri, token=self.milvus_token)
        try:
            if self.milvus_db not in db.list_database(using=self._alias):
                db.create_database(self.milvus_db, using=self._alias)
            db.using_database(self.milvus_db, using=self._alias)
        except Exception as exc:  # 数据库操作失败时沿用连接默认库
            logger.warning(f"切换 Milvus 数据库 {self.milvus_db} 失败，沿用默认库: {exc}")
        if not utility.has_collection(COLLECTION_NAME, using=self._alias):
            self._create_collection()

    def _create_collection(self) -> None:
        fields = [
            FieldSchema(name="id", dtype=DataType.VARCHAR, max_length=100, is_primary=True),
            FieldSchema(name="kb_id", dtype=DataType.VARCHAR, max_length=100),
            FieldSchema(name="product", dtype=DataType.VARCHAR, max_length=255),
            FieldSchema(name="image_url", dtype=DataType.VARCHAR, max_length=512),
            FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=IMAGE_DIM),
        ]
        schema = CollectionSchema(fields=fields, description="Product reference image visual index")
        collection = Collection(name=COLLECTION_NAME, schema=schema, using=self._alias)
        collection.create_index(
            "embedding",
            {"metric_type": "COSINE", "index_type": "IVF_FLAT", "params": {"nlist": 128}},
        )
        logger.info(f"Created Milvus collection {COLLECTION_NAME}")

    def _collection(self) -> Collection:
        return Collection(name=COLLECTION_NAME, using=self._alias)

    def list_reference_images(self, kb_id: str) -> list[dict[str, str]]:
        """列出 MinIO public/{kb_id}/product-images/ 下的参照图。"""
        client = MinIOClient()
        prefix = f"{kb_id}/{REFERENCE_IMAGE_PREFIX}/"
        images: list[dict[str, str]] = []
        for obj in client.client.list_objects("public", prefix=prefix, recursive=True):
            name = obj.object_name
            if name.lower().endswith(_IMAGE_EXTENSIONS):
                images.append(
                    {
                        "object_name": name,
                        "product": os.path.splitext(os.path.basename(name))[0],
                    }
                )
        return images

    async def build_index(self, kb_id: str) -> dict[str, int]:
        """扫描 KB 的 product-images 目录，向量化并 upsert 进 `product_images` 集合。幂等，可重复执行。"""
        images = self.list_reference_images(kb_id)
        if not images:
            return {"kb_id": kb_id, "indexed": 0, "errors": 0}
        client = MinIOClient()
        rows: list[dict[str, Any]] = []
        errors = 0
        for i in range(0, len(images), VL_EMBEDDING_BATCH):
            batch = images[i : i + VL_EMBEDDING_BATCH]
            payloads: list[bytes] = []
            for item in batch:
                try:
                    payloads.append(client.download_file("public", item["object_name"]))
                except Exception as exc:
                    logger.error(f"下载参照图失败 {item['object_name']}: {exc}")
                    errors += 1
                    payloads.append(b"")
            try:
                vectors = await _vl_embed(payloads)
            except Exception as exc:
                logger.error(f"参照图向量化失败 {kb_id}: {exc}")
                errors += len(batch)
                continue
            for item, vector in zip(batch, vectors):
                if not vector:
                    continue
                rows.append(
                    {
                        "id": hashstr(f"{kb_id}:{item['object_name']}", 32),
                        "kb_id": kb_id,
                        "product": item["product"],
                        "image_url": f"http://{client.public_endpoint}/public/{item['object_name']}",
                        "embedding": vector,
                    }
                )
        if rows:
            self._collection().upsert(rows)
            await asyncio.to_thread(self._collection().flush)
        return {"kb_id": kb_id, "indexed": len(rows), "errors": errors}

    async def search(
        self,
        image: bytes | str,
        top_k: int = 5,
        kb_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """按外观检索：用户图片向量化后与参照图向量做余弦相似度，返回 Top-K 候选。"""
        vector = (await _vl_embed([image]))[0]
        collection = self._collection()
        try:
            await asyncio.to_thread(collection.load)
        except Exception:
            pass
        expr = f'kb_id == "{kb_id}"' if kb_id else None
        results = collection.search(
            data=[vector],
            anns_field="embedding",
            param={"metric_type": "COSINE", "params": {}},
            limit=top_k,
            output_fields=["kb_id", "product", "image_url"],
            expr=expr,
        )
        matches: list[dict[str, Any]] = []
        for hit in results[0]:
            matches.append(
                {
                    "product": hit.entity.get("product"),
                    "kb_id": hit.entity.get("kb_id"),
                    "image_url": hit.entity.get("image_url"),
                    "score": round(float(hit.distance), 4),
                }
            )
        return matches

    async def list_indexed(self, kb_id: str) -> set[str]:
        """返回该知识库已建立索引的产品名集合。"""
        collection = self._collection()
        try:
            await asyncio.to_thread(collection.load)
        except Exception:
            pass
        rows = collection.query(expr=f'kb_id == "{kb_id}"', output_fields=["product"])
        return {str(row["product"]) for row in rows}

    async def delete_image(self, kb_id: str, product: str) -> None:
        """从索引中删除单个产品参照图的特征向量（产品名在写入前已校验不含引号/反斜杠）。"""
        expr = f'kb_id == "{kb_id}" and product == "{product}"'
        await asyncio.to_thread(self._collection().delete, expr)

    def clear(self, kb_id: str | None = None) -> None:
        """删除索引（可选按 kb_id 过滤）。"""
        collection = self._collection()
        expr = f'kb_id == "{kb_id}"' if kb_id else ""
        collection.delete(expr=expr) if expr else None


async def build_product_image_index(kb_id: str) -> dict[str, int]:
    """运维入口：为一个知识库建立产品参照图索引。"""
    return await ProductImageIndex().build_index(kb_id)


if __name__ == "__main__":
    import sys

    kb_id = sys.argv[1] if len(sys.argv) > 1 else ""
    if not kb_id:
        print("用法: python -m yuxi.knowledge.product_image_index <kb_id>")
        sys.exit(1)
    print(asyncio.run(build_product_image_index(kb_id)))
