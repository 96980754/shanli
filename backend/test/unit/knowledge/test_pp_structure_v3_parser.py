"""PP-Structure-V3 解析结果图片改写测试（悬空相对路径裂图修复）。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

import yuxi.knowledge.parser.pp_structure_v3 as pp_structure_v3
from yuxi.knowledge.parser.pp_structure_v3 import PPStructureV3Parser


@dataclass
class FakeResponse:
    status_code: int
    content: bytes = b""
    headers: dict[str, str] | None = None


def make_api_result(text: str, images: dict[str, str]) -> dict[str, Any]:
    # PP-Structure-V3 /layout-parsing 接口的 layoutParsingResults 结构
    return {
        "errorCode": 0,
        "result": {
            "layoutParsingResults": [
                {
                    "markdown": {"text": text, "images": images},
                    "prunedResult": {"table_result": [], "formula_result": []},
                }
            ]
        },
    }


def test_rewrites_html_img_src_to_uploaded_url(monkeypatch: pytest.MonkeyPatch) -> None:
    text = (
        '<div style="text-align: center;"><img src="imgs/img_in_seal_box_1_2_3_4.jpg" '
        'alt="Image" width="9%" /></div>\n\n善理通益信息科技（深圳）有限公司'
    )
    uploaded: dict[str, Any] = {}

    class FakeMinioClient:
        def ensure_bucket_exists(self, bucket_name):
            uploaded["bucket_name"] = bucket_name

        def upload_file(self, bucket_name, object_name, data):
            uploaded["object_name"] = object_name
            uploaded["data"] = data
            return type("UploadResult", (), {"url": "http://localhost:9000/public/kb_x/kb-images/1_seal.jpg"})()

    monkeypatch.setattr(pp_structure_v3.requests, "get", lambda url, timeout=None: FakeResponse(200, b"seal-bytes"))
    monkeypatch.setattr(pp_structure_v3, "get_minio_client", lambda: FakeMinioClient())
    monkeypatch.setattr(pp_structure_v3.time, "time", lambda: 1.0)

    parser = PPStructureV3Parser()
    result = parser._parse_api_result(
        make_api_result(text, {"imgs/img_in_seal_box_1_2_3_4.jpg": "https://remote/seal.jpg"}),
        "/tmp/sample.pdf",
        {"image_bucket": "public", "image_prefix": "kb_x/kb-images"},
    )

    assert result["full_text"] == (
        '<div style="text-align: center;">'
        '<img src="http://localhost:9000/public/kb_x/kb-images/1_seal.jpg" '
        'alt="Image" width="9%" /></div>\n\n善理通益信息科技（深圳）有限公司'
    )
    assert uploaded == {
        "bucket_name": "public",
        "object_name": "kb_x/kb-images/1000000_img_in_seal_box_1_2_3_4.jpg",
        "data": b"seal-bytes",
    }


def test_rewrites_markdown_image_links(monkeypatch: pytest.MonkeyPatch) -> None:
    text = "前文\n\n![Image](imgs/figure_1.jpg)\n\n后文"

    class FakeMinioClient:
        def ensure_bucket_exists(self, bucket_name):
            return None

        def upload_file(self, bucket_name, object_name, data):
            return type("UploadResult", (), {"url": "http://localhost:9000/public/kb_x/kb-images/2_figure_1.jpg"})()

    monkeypatch.setattr(pp_structure_v3.requests, "get", lambda url, timeout=None: FakeResponse(200, b"figure"))
    monkeypatch.setattr(pp_structure_v3, "get_minio_client", lambda: FakeMinioClient())

    parser = PPStructureV3Parser()
    result = parser._parse_api_result(
        make_api_result(text, {"imgs/figure_1.jpg": "https://remote/figure_1.jpg"}),
        "/tmp/sample.pdf",
        {},
    )

    assert result["full_text"] == "前文\n\n![Image](http://localhost:9000/public/kb_x/kb-images/2_figure_1.jpg)\n\n后文"
