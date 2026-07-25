from __future__ import annotations

import io

import fitz
import pytest
from PIL import Image, ImageDraw, ImageFont
from yuxi.knowledge.runtime import knowledge_base
from yuxi.knowledge.utils.kb_utils import parse_minio_url
from yuxi.repositories.knowledge_chunk_repository import KnowledgeChunkRepository
from yuxi.repositories.knowledge_file_repository import KnowledgeFileRepository
from yuxi.storage.minio.client import get_minio_client

pytestmark = [pytest.mark.asyncio, pytest.mark.integration]


def _font(size: int = 56):
    for name in ("NotoSansCJK-Regular.ttc", "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _image_bytes(text: str, *, image_format: str) -> bytes:
    image = Image.new("RGB", (1600, 360), "white")
    draw = ImageDraw.Draw(image)
    draw.text((70, 120), text, font=_font(), fill="black")
    buffer = io.BytesIO()
    image.save(buffer, format=image_format, quality=95)
    return buffer.getvalue()


def _scanned_pdf_bytes(text: str) -> bytes:
    image = _image_bytes(text, image_format="PNG")
    document = fitz.open()
    page = document.new_page(width=800, height=360)
    page.insert_image(page.rect, stream=image)
    value = document.tobytes()
    document.close()
    return value


async def _upload_and_create(test_client, headers, kb_id: str, filename: str, content: bytes) -> str:
    upload = await test_client.post(
        "/api/knowledge/files/upload",
        params={"kb_id": kb_id},
        files={"file": (filename, content, "application/octet-stream")},
        headers=headers,
    )
    assert upload.status_code == 200, upload.text
    payload = upload.json()
    item = payload["file_path"]
    create = await test_client.post(
        f"/api/knowledge/databases/{kb_id}/documents/add",
        json={
            "items": [item],
            "params": {
                "source_paths": {item: payload["filename"]},
                "content_hashes": {item: "client-value-is-not-trusted"},
                "file_sizes": {item: 1},
            },
        },
        headers=headers,
    )
    assert create.status_code == 200, create.text
    return create.json()["items"][0]["file_id"]


def _result_file_ids(results: list[dict]) -> set[str]:
    return {str(item.get("metadata", {}).get("file_id") or item.get("file_id") or "") for item in results}


async def test_png_jpeg_and_scanned_pdf_ocr_chunk_index_and_retrieve(
    test_client,
    admin_headers,
    knowledge_database,
    monkeypatch,
) -> None:
    kb_id = knowledge_database["kb_id"]
    kb = await knowledge_base.aget_kb(kb_id)
    if kb_id not in kb.databases_meta:
        await kb._load_metadata()
    collection = await kb._get_milvus_collection(kb_id)
    assert collection is not None
    embedding_field = next(field for field in collection.schema.fields if field.name == "embedding")
    dimension = int(embedding_field.params["dim"])
    documents = (
        ("scan-cn.png", _image_bytes("知识导入 OCRPNG ALPHA 73195", image_format="PNG"), "ALPHA"),
        ("scan-en.jpg", _image_bytes("DOCUMENT OCRJPG BRAVO 84206", image_format="JPEG"), "BRAVO"),
        ("scan.pdf", _scanned_pdf_bytes("SCANNED PDF CHARLIE 95317"), "CHARLIE"),
    )

    def deterministic_vectors(texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            vector = [0.0] * dimension
            for index, marker in enumerate(("ALPHA", "BRAVO", "CHARLIE")):
                if marker in text.upper():
                    vector[index] = 1.0
                    break
            else:
                vector[-1] = 1.0
            vectors.append(vector)
        return vectors

    async def async_embed(texts: list[str]) -> list[list[float]]:
        return deterministic_vectors(texts)

    monkeypatch.setattr(
        kb,
        "_get_embedding_function",
        lambda _model_spec, *, sync=False: deterministic_vectors if sync else async_embed,
    )

    file_repository = KnowledgeFileRepository()
    chunk_repository = KnowledgeChunkRepository()
    minio_client = get_minio_client()
    for filename, content, marker in documents:
        file_id = await _upload_and_create(
            test_client,
            admin_headers,
            kb_id,
            filename,
            content,
        )
        uploaded_record = await file_repository.get_by_file_id(file_id)
        assert uploaded_record is not None
        source_bucket, source_object = parse_minio_url(uploaded_record.path)
        assert await minio_client.astat_file(source_bucket, source_object) == len(content)

        parsed = await kb.parse_file(kb_id, file_id)
        assert parsed["status"] == "parsed"
        assert parsed["markdown_file"]
        assert marker in parsed["parse_metadata"]["blocks"][0]["text"].upper()
        assert parsed["parse_metadata"]["attempts"]
        assert parsed["parse_metadata"]["attempts"][-1]["provider"] == "rapid_ocr"
        assert parsed["parse_metadata"]["attempts"][-1]["status"] == "accepted"
        assert parsed["parse_metadata"]["quality"]["accepted"] is True
        if filename.endswith(".pdf"):
            assert parsed["parse_metadata"]["classification"]["type"] == "scanned_pdf"
        else:
            assert parsed["parse_metadata"]["classification"]["type"] == "image"

        indexed = await kb.index_file(kb_id, file_id)
        assert indexed["status"] == "indexed"
        chunks = await chunk_repository.list_by_file_id(file_id)
        assert chunks
        assert all(chunk.source_metadata["parser_name"] == "rapid_ocr" for chunk in chunks)
        assert all(chunk.source_metadata["page_number"] == 1 for chunk in chunks)

        results = await kb.aquery(
            marker,
            kb_id,
            search_mode="vector",
            final_top_k=20,
            similarity_threshold=-1.0,
        )
        assert file_id in _result_file_ids(results)
