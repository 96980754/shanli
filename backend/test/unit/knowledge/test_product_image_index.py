"""ProductImageIndex 新增方法（list_indexed / delete_image）的单测。"""

from __future__ import annotations

import asyncio

from yuxi.knowledge.product_image_index import ProductImageIndex


class FakeCollection:
    """替代 Milvus Collection：记录 query/delete 调用，不触网。"""

    def __init__(self, query_rows: list[dict] | None = None):
        self.query_rows = query_rows if query_rows is not None else []
        self.query_expr: str | None = None
        self.query_fields: list[str] | None = None
        self.load_calls = 0
        self.delete_exprs: list[str] = []

    def load(self) -> None:
        self.load_calls += 1

    def query(self, expr: str, output_fields: list[str]):
        self.query_expr = expr
        self.query_fields = output_fields
        return self.query_rows

    def delete(self, expr: str) -> None:
        self.delete_exprs.append(expr)


def _index_with_collection(collection: FakeCollection) -> ProductImageIndex:
    index = ProductImageIndex.__new__(ProductImageIndex)  # 跳过 __init__（会连 Milvus）
    index._collection = lambda: collection
    return index


def test_list_indexed_returns_products_for_kb():
    collection = FakeCollection(query_rows=[{"product": "产品A"}, {"product": "产品B"}])

    result = asyncio.run(_index_with_collection(collection).list_indexed("kb_1"))

    assert result == {"产品A", "产品B"}
    assert collection.query_expr == 'kb_id == "kb_1"'
    assert collection.query_fields == ["product"]
    assert collection.load_calls == 1


def test_list_indexed_returns_empty_set_when_no_rows():
    collection = FakeCollection(query_rows=[])

    result = asyncio.run(_index_with_collection(collection).list_indexed("kb_1"))

    assert result == set()


def test_delete_image_deletes_by_kb_and_product():
    collection = FakeCollection()

    asyncio.run(_index_with_collection(collection).delete_image("kb_1", "产品A"))

    assert collection.delete_exprs == ['kb_id == "kb_1" and product == "产品A"']
