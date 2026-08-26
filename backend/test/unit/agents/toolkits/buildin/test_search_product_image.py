"""内置 search_product_image 工具（按外观检索产品参照图）的单测。"""

import asyncio
from types import SimpleNamespace

from langchain_core.messages import HumanMessage
from yuxi.agents.toolkits.buildin import tools


def _runtime_with_image(image_url: str) -> SimpleNamespace:
    return SimpleNamespace(
        state=SimpleNamespace(
            messages=[
                HumanMessage(
                    content=[
                        {"type": "text", "text": "这是什么产品？"},
                        {"type": "image_url", "image_url": {"url": image_url}},
                    ]
                )
            ]
        )
    )


def test_search_product_image_tool_schema_is_json_serializable():
    """工具 schema 必须可生成 JSON Schema（回归：runtime: ToolRuntime 注入参数曾被推理进 schema，
    其 TypedDict 含 Callable 字段，触发 PydanticInvalidForJsonSchema 导致流式失败）。"""
    schema = tools.search_product_image.args_schema.model_json_schema()
    assert schema["type"] == "object"
    assert set(schema["properties"]) == {"query_text"}


def test_search_product_image_returns_error_without_user_image():
    result = asyncio.run(
        tools.search_product_image.coroutine(
            query_text="",
            runtime=SimpleNamespace(state=SimpleNamespace(messages=[])),
        )
    )

    assert result["error"]
    assert result["matches"] == []


def test_search_product_image_calls_index_and_returns_matches(monkeypatch):
    matches = [
        {"product": "产品A", "kb_id": "kb_1", "image_url": "http://localhost:9000/public/1.jpg", "score": 0.98},
        {"product": "产品B", "kb_id": "kb_1", "image_url": "http://localhost:9000/public/2.jpg", "score": 0.41},
    ]

    class FakeIndex:
        async def search(self, image, top_k=5):
            return matches

    monkeypatch.setattr("yuxi.knowledge.product_image_index.ProductImageIndex", lambda: FakeIndex())

    result = asyncio.run(
        tools.search_product_image.coroutine(
            query_text="",
            runtime=_runtime_with_image("data:image/jpeg;base64,iVBORw0KGgo="),
        )
    )

    assert result["matches"] == matches


def test_search_product_image_swallows_index_errors(monkeypatch):
    class BrokenIndex:
        async def search(self, image, top_k=5):
            raise RuntimeError("milvus down")

    monkeypatch.setattr("yuxi.knowledge.product_image_index.ProductImageIndex", lambda: BrokenIndex())

    result = asyncio.run(
        tools.search_product_image.coroutine(
            query_text="",
            runtime=_runtime_with_image("data:image/jpeg;base64,iVBORw0KGgo="),
        )
    )

    assert result["error"]
    assert result["matches"] == []
