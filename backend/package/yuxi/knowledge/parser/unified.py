"""Unified document parsers that produce Markdown and source metadata."""

from __future__ import annotations

import asyncio
import base64
import io
import os
import re
import tempfile
import threading
import time
import zipfile
from dataclasses import dataclass, field
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

import aiofiles
from docling.datamodel.base_models import InputFormat
from docling.document_converter import DocumentConverter
from langchain_community.document_loaders import PyPDFLoader
from markdownify import markdownify as md_convert

from yuxi.knowledge.parser.zip_utils import process_zip_file as _process_zip_file
from yuxi.storage.minio import get_minio_client
from yuxi.utils import logger

SUPPORTED_FILE_EXTENSIONS: tuple[str, ...] = (
    ".txt",
    ".md",
    ".pdf",
    ".docx",
    ".xlsx",
    ".pptx",
)


def is_supported_file_extension(file_name: str | os.PathLike[str]) -> bool:
    """Return whether a file belongs to the stable PR 2 ingestion set."""
    return Path(file_name).suffix.lower() in SUPPORTED_FILE_EXTENSIONS


@dataclass(slots=True)
class DocumentBlock:
    """A source-aware block produced by a document parser."""

    block_type: str
    order: int
    text: str | None = None
    markdown: str | None = None
    page_number: int | None = None
    sheet_name: str | None = None
    slide_number: int | None = None
    start_char_pos: int | None = None
    end_char_pos: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            key: value
            for key, value in {
                "block_type": self.block_type,
                "order": self.order,
                "text": self.text,
                "markdown": self.markdown,
                "page_number": self.page_number,
                "sheet_name": self.sheet_name,
                "slide_number": self.slide_number,
                "start_char_pos": self.start_char_pos,
                "end_char_pos": self.end_char_pos,
            }.items()
            if value is not None
        }


@dataclass(slots=True)
class MarkdownParseResult:
    """Unified Markdown and source-structure parsing result."""

    markdown: str
    document_title: str | None
    parser_name: str
    parser_version: str
    warnings: list[str] = field(default_factory=list)
    blocks: list[DocumentBlock] = field(default_factory=list)
    file_ext: str | None = None
    artifacts: dict[str, Any] = field(default_factory=dict)

    def to_metadata(self) -> dict[str, Any]:
        """Serialize metadata used later to map chunks back to source blocks."""
        return {
            "document_title": self.document_title,
            "parser_name": self.parser_name,
            "parser_version": self.parser_version,
            "warnings": list(self.warnings),
            "blocks": [block.to_dict() for block in self.blocks],
        }


ParseResult = MarkdownParseResult

_docling_converter: DocumentConverter | None = None
_docling_converter_lock = threading.Lock()


def _package_version(package_name: str) -> str:
    try:
        return version(package_name)
    except PackageNotFoundError:
        return "unknown"


def _contains_semantic_text(value: str) -> bool:
    return bool(re.search(r"[A-Za-z0-9\u3400-\u9fff]", value or ""))


def _minimum_valid_characters(params: dict | None = None) -> int:
    configured = (params or {}).get("min_valid_text_chars")
    if configured is None:
        configured = os.getenv("DOCUMENT_PARSE_MIN_VALID_CHARS", "1")
    try:
        return max(int(configured), 1)
    except (TypeError, ValueError):
        return 1


def validate_parse_result(result: MarkdownParseResult, params: dict | None = None) -> MarkdownParseResult:
    """Reject empty or placeholder-only output before Markdown is persisted."""
    semantic_chars = re.findall(r"[A-Za-z0-9\u3400-\u9fff]", result.markdown or "")
    valid_blocks = [block for block in result.blocks if _contains_semantic_text(block.text or block.markdown or "")]
    if len(semantic_chars) < _minimum_valid_characters(params) or not valid_blocks:
        raise ValueError("文档未提取到有效文本，解析结果为空")
    return result


def _blocks_from_markdown(markdown: str) -> tuple[str | None, list[DocumentBlock]]:
    blocks: list[DocumentBlock] = []
    title: str | None = None
    pattern = r"(?:^|\n\s*\n)(?P<block>\s*\S[\s\S]*?)(?=\n\s*\n|\Z)"
    for match in re.finditer(pattern, markdown):
        raw = match.group("block")
        content = raw.strip()
        if not content:
            continue
        heading = re.match(r"^(#{1,6})\s+(.+)$", content.splitlines()[0])
        is_table = "|" in content and any(re.match(r"^\s*\|?\s*:?-{3,}", line) for line in content.splitlines()[1:2])
        block_type = "table" if is_table else "heading" if heading else "paragraph"
        text = heading.group(2).strip() if heading else content
        if heading and title is None:
            title = text
        start = match.start("block") + len(raw) - len(raw.lstrip())
        blocks.append(
            DocumentBlock(
                block_type=block_type,
                order=len(blocks),
                text=text,
                markdown=content,
                start_char_pos=start,
                end_char_pos=start + len(content),
            )
        )
    return title, blocks


def _markdown_table(rows: list[list[Any]]) -> str:
    width = max((len(row) for row in rows), default=0)
    normalized = [
        [str(value if value is not None else "").replace("|", r"\|").replace("\n", " ").strip() for value in row]
        + [""] * (width - len(row))
        for row in rows
    ]
    while normalized and not any(normalized[-1]):
        normalized.pop()
    if not normalized or not any(any(row) for row in normalized):
        return ""
    header = normalized[0]
    lines = [f"| {' | '.join(header)} |", f"| {' | '.join(['---'] * width)} |"]
    lines.extend(f"| {' | '.join(row)} |" for row in normalized[1:])
    return "\n".join(lines)


def _compose_blocks(
    blocks: list[DocumentBlock],
    prefixes: dict[int, str] | None = None,
) -> str:
    rendered: list[str] = []
    cursor = 0
    prefixes = prefixes or {}
    for block in blocks:
        prefix = prefixes.get(block.order, "")
        value = block.markdown or block.text or ""
        part = f"{prefix}{value}"
        if rendered:
            cursor += 2
        block.start_char_pos = cursor + len(prefix)
        block.end_char_pos = block.start_char_pos + len(value)
        rendered.append(part)
        cursor += len(part)
    return "\n\n".join(rendered)


def validate_document_bytes(filename: str, content: bytes) -> None:
    """Validate the stable file list and basic file/container signatures."""
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED_FILE_EXTENSIONS:
        raise ValueError(f"不支持的文件格式: {suffix or '无扩展名'}")
    if suffix in {".txt", ".md"}:
        if b"\x00" in content:
            raise ValueError("文本文件包含二进制内容")
        try:
            content.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ValueError("文本文件必须使用 UTF-8 编码") from exc
        return
    if suffix == ".pdf":
        if not content.startswith(b"%PDF-"):
            raise ValueError("PDF 文件签名不匹配")
        return

    required_members = {
        ".docx": ("word/document.xml", "DOCX"),
        ".xlsx": ("xl/workbook.xml", "XLSX"),
        ".pptx": ("ppt/presentation.xml", "PPTX"),
    }
    member, label = required_members[suffix]
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            names = set(archive.namelist())
    except (OSError, zipfile.BadZipFile) as exc:
        raise ValueError(f"{label} 文件不是有效的 Office Open XML 容器") from exc
    if "[Content_Types].xml" not in names or member not in names:
        raise ValueError(f"{label} 文件容器类型与扩展名不匹配")


def _get_docling_converter() -> DocumentConverter:
    global _docling_converter
    if _docling_converter is None:
        _docling_converter = DocumentConverter(
            format_options={
                InputFormat.DOCX: None,
                InputFormat.XLSX: None,
                InputFormat.PPTX: None,
            }
        )
    return _docling_converter


def _resolve_image_storage_params(params: dict | None) -> tuple[str, str]:
    params = params or {}
    image_bucket = params.get("image_bucket") or "public"
    image_prefix = params.get("image_prefix")
    if image_prefix:
        normalized_prefix = str(image_prefix).strip("/")
        if normalized_prefix:
            return image_bucket, normalized_prefix
    return image_bucket, "unknown/kb-images"


def _resolve_ocr_engine_params(params: dict | None) -> tuple[str, dict[str, Any]]:
    from yuxi import config

    params = params or {}
    engine = str(params.get("ocr_engine") if "ocr_engine" in params else config.default_ocr_engine)
    engine = engine.strip() or config.default_ocr_engine
    engine_config = params.get("ocr_engine_config")
    processor_params = dict(params)
    if isinstance(engine_config, dict):
        processor_params.update(engine_config)
    return engine, processor_params


def _upload_image_to_minio(image_data: bytes, filename: str, bucket_name: str, object_prefix: str) -> str:
    minio_client = get_minio_client()
    minio_client.ensure_bucket_exists(bucket_name)
    normalized_prefix = object_prefix.strip("/") or "unknown/kb-images"
    timestamp = int(time.time() * 1000000)
    object_name = f"{normalized_prefix}/{timestamp}_{Path(filename).name}"
    result = minio_client.upload_file(
        bucket_name=bucket_name,
        object_name=object_name,
        data=image_data,
    )
    return result.url


def _parse_data_uri(data_uri: str) -> tuple[bytes, str]:
    header, base64_data = data_uri.split(",", 1)
    mime_type = header.split(":")[1].split(";")[0]
    return base64.b64decode(base64_data), mime_type


def _convert_with_docling(file_path: Path, params: dict | None = None) -> str:
    """Convert Office Open XML through the existing Docling adapter."""
    params = params or {}
    image_bucket, image_prefix = _resolve_image_storage_params(params)
    with _docling_converter_lock:
        result = _get_docling_converter().convert(file_path)
    if result.status.name != "SUCCESS":
        raise RuntimeError(f"Docling 转换失败: {result.status}")
    doc = result.document
    if not getattr(doc, "pictures", None):
        return doc.export_to_markdown()

    replacements: list[str] = []
    for picture in doc.pictures:
        uri = str(picture.image.uri) if hasattr(picture, "image") and hasattr(picture.image, "uri") else ""
        if not uri.startswith("data:"):
            replacements.append("")
            continue
        filename = "image"
        try:
            image_data, mime_type = _parse_data_uri(uri)
            filename = f"image_{int(time.time() * 1000000)}.{mime_type.split('/')[-1]}"
            url = _upload_image_to_minio(image_data, filename, image_bucket, image_prefix)
            replacements.append(f"![{filename}]({url})")
        except Exception as exc:  # noqa: BLE001
            logger.error("上传解析图片失败 {}: {}", filename, exc)
            replacements.append(f"[图片: {filename}]")

    markdown = doc.export_to_markdown()
    for replacement in replacements:
        markdown = re.sub(r"<!--\s*image\s*-->", replacement, markdown, count=1)
    return markdown


def _parse_docx_fallback(file_path: Path) -> MarkdownParseResult:
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    document = Document(str(file_path))
    blocks: list[DocumentBlock] = []
    title: str | None = None
    for child in document.element.body.iterchildren():
        if child.tag.endswith("}p"):
            paragraph = Paragraph(child, document)
            text = paragraph.text.strip()
            if not text:
                continue
            style_name = str(getattr(paragraph.style, "name", "") or "")
            heading_match = re.match(r"Heading\s+(\d+)", style_name, re.IGNORECASE)
            block_type = "heading" if heading_match or style_name.lower() == "title" else "paragraph"
            level = int(heading_match.group(1)) if heading_match else 1
            markdown = f"{'#' * min(max(level, 1), 6)} {text}" if block_type == "heading" else text
            if block_type == "heading" and title is None:
                title = text
            blocks.append(
                DocumentBlock(
                    block_type=block_type,
                    order=len(blocks),
                    text=text,
                    markdown=markdown,
                )
            )
        elif child.tag.endswith("}tbl"):
            table = Table(child, document)
            markdown = _markdown_table([[cell.text for cell in row.cells] for row in table.rows])
            if markdown:
                blocks.append(
                    DocumentBlock(
                        block_type="table",
                        order=len(blocks),
                        text="\n".join(cell.text for row in table.rows for cell in row.cells),
                        markdown=markdown,
                    )
                )
    markdown = _compose_blocks(blocks)
    return MarkdownParseResult(
        markdown=markdown,
        document_title=title or file_path.stem,
        parser_name="python-docx",
        parser_version=_package_version("python-docx"),
        warnings=["Docling 解析失败，已回退到 python-docx"],
        blocks=blocks,
        file_ext=".docx",
    )


def _parse_docx(file_path: Path, params: dict | None = None) -> MarkdownParseResult:
    try:
        markdown = _convert_with_docling(file_path, params=params)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Docling 解析 DOCX 失败，回退到 python-docx: {}, {}", file_path.name, exc)
        return _parse_docx_fallback(file_path)
    title, blocks = _blocks_from_markdown(markdown)
    return MarkdownParseResult(
        markdown=markdown,
        document_title=title or file_path.stem,
        parser_name="docling",
        parser_version=_package_version("docling"),
        blocks=blocks,
        file_ext=".docx",
    )


def _convert_docx_with_python_docx(file_path: Path) -> str:
    """Compatibility helper retained for existing callers and tests."""
    return _parse_docx_fallback(file_path).markdown


def _parse_xlsx(file_path: Path) -> MarkdownParseResult:
    from openpyxl import load_workbook

    workbook = load_workbook(file_path, read_only=True, data_only=True)
    blocks: list[DocumentBlock] = []
    prefixes: dict[int, str] = {}
    try:
        for sheet in workbook.worksheets:
            rows = [list(row) for row in sheet.iter_rows(values_only=True)]
            markdown = _markdown_table(rows)
            if not markdown:
                continue
            order = len(blocks)
            prefixes[order] = f"## {sheet.title}\n\n"
            blocks.append(
                DocumentBlock(
                    block_type="table",
                    order=order,
                    text="\n".join(
                        str(value) for row in rows for value in row if value is not None and str(value).strip()
                    ),
                    markdown=markdown,
                    sheet_name=sheet.title,
                )
            )
    finally:
        workbook.close()
    return MarkdownParseResult(
        markdown=_compose_blocks(blocks, prefixes),
        document_title=file_path.stem,
        parser_name="openpyxl",
        parser_version=_package_version("openpyxl"),
        blocks=blocks,
        file_ext=".xlsx",
    )


def _parse_pptx(file_path: Path) -> MarkdownParseResult:
    from pptx import Presentation

    presentation = Presentation(str(file_path))
    blocks: list[DocumentBlock] = []
    prefixes: dict[int, str] = {}
    title: str | None = None
    for slide_number, slide in enumerate(presentation.slides, 1):
        slide_start = len(blocks)
        for shape in slide.shapes:
            if getattr(shape, "has_table", False):
                markdown = _markdown_table([[cell.text for cell in row.cells] for row in shape.table.rows])
                if markdown:
                    blocks.append(
                        DocumentBlock(
                            block_type="table",
                            order=len(blocks),
                            text="\n".join(cell.text for row in shape.table.rows for cell in row.cells),
                            markdown=markdown,
                            slide_number=slide_number,
                        )
                    )
                continue
            if not getattr(shape, "has_text_frame", False):
                continue
            text = shape.text.strip()
            if not text:
                continue
            is_title = shape == slide.shapes.title
            block_type = "title" if is_title else "paragraph"
            markdown = f"### {text}" if is_title else text
            if is_title and title is None:
                title = text
            blocks.append(
                DocumentBlock(
                    block_type=block_type,
                    order=len(blocks),
                    text=text,
                    markdown=markdown,
                    slide_number=slide_number,
                )
            )
        if len(blocks) > slide_start:
            prefixes[slide_start] = f"## Slide {slide_number}\n\n"
    return MarkdownParseResult(
        markdown=_compose_blocks(blocks, prefixes),
        document_title=title or file_path.stem,
        parser_name="python-pptx",
        parser_version=_package_version("python-pptx"),
        blocks=blocks,
        file_ext=".pptx",
    )


def _parse_text_pdf(file_path: Path) -> MarkdownParseResult:
    docs = PyPDFLoader(str(file_path)).load()
    blocks: list[DocumentBlock] = []
    prefixes: dict[int, str] = {}
    for default_index, doc in enumerate(docs):
        text = (doc.page_content or "").strip()
        if not text:
            continue
        page_number = int((doc.metadata or {}).get("page", default_index)) + 1
        order = len(blocks)
        prefixes[order] = f"<!-- page: {page_number} -->\n\n"
        blocks.append(
            DocumentBlock(
                block_type="paragraph",
                order=order,
                text=text,
                markdown=text,
                page_number=page_number,
            )
        )
    if not blocks:
        raise ValueError("文本型 PDF 未提取到有效文本，可能是扫描 PDF，需要启用 OCR")
    return MarkdownParseResult(
        markdown=_compose_blocks(blocks, prefixes),
        document_title=file_path.stem,
        parser_name="native_pdf",
        parser_version=_package_version("pypdf"),
        blocks=blocks,
        file_ext=".pdf",
    )


def _parse_text_file(file_path: Path, *, markdown_source: bool) -> MarkdownParseResult:
    try:
        content = file_path.read_bytes().decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("文本文件必须使用 UTF-8 编码") from exc
    if markdown_source:
        title, blocks = _blocks_from_markdown(content)
        markdown = content
        parser_name = "native_markdown"
    else:
        blocks = []
        cursor = 0
        for paragraph in re.split(r"\n\s*\n", content):
            text = paragraph.strip()
            if not text:
                continue
            start = content.find(paragraph, cursor) + len(paragraph) - len(paragraph.lstrip())
            cursor = start + len(text)
            blocks.append(
                DocumentBlock(
                    block_type="paragraph",
                    order=len(blocks),
                    text=text,
                    markdown=text,
                    start_char_pos=start,
                    end_char_pos=start + len(text),
                )
            )
        title = None
        markdown = content.strip()
        parser_name = "native_text"
    return MarkdownParseResult(
        markdown=markdown,
        document_title=title or file_path.stem,
        parser_name=parser_name,
        parser_version="1",
        blocks=blocks,
        file_ext=file_path.suffix.lower(),
    )


def _convert_csv_to_markdown(file_path: Path) -> str:
    import pandas as pd

    dataframe = pd.read_csv(file_path)
    return "\n\n".join(dataframe.iloc[[index]].to_markdown(index=False) for index in range(len(dataframe)))


def pdfreader(file_path, params=None):
    """Compatibility native PDF text reader."""
    del params
    path = Path(file_path)
    assert path.exists(), "File not found"
    assert path.suffix.lower() == ".pdf", "File format not supported"
    return "\n\n".join(doc.page_content for doc in PyPDFLoader(str(path)).load())


def parse_pdf(file, params=None):
    """Existing configurable OCR entry retained outside the stable text-PDF route."""
    from yuxi.knowledge.parser.base import DocumentProcessorException
    from yuxi.knowledge.parser.factory import DocumentProcessorFactory

    opt_ocr, processor_params = _resolve_ocr_engine_params(params)
    if opt_ocr == "disable":
        return pdfreader(file, params=processor_params)
    image_bucket, image_prefix = _resolve_image_storage_params(processor_params)
    processor_params.setdefault("image_bucket", image_bucket)
    processor_params.setdefault("image_prefix", image_prefix)
    try:
        return DocumentProcessorFactory.process_file(opt_ocr, file, processor_params)
    except DocumentProcessorException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise DocumentProcessorException(f"PDF解析失败: {exc}", opt_ocr, "parsing_failed") from exc


def parse_image(file, params=None):
    """Existing configurable image OCR entry retained for the later OCR PR."""
    from yuxi.knowledge.parser.base import DocumentProcessorException
    from yuxi.knowledge.parser.factory import DocumentProcessorFactory

    opt_ocr, processor_params = _resolve_ocr_engine_params(params)
    if opt_ocr == "disable":
        raise ValueError("图像文件必须启用OCR才能提取文本内容")
    image_bucket, image_prefix = _resolve_image_storage_params(processor_params)
    processor_params.setdefault("image_bucket", image_bucket)
    processor_params.setdefault("image_prefix", image_prefix)
    try:
        return DocumentProcessorFactory.process_file(opt_ocr, file, processor_params)
    except DocumentProcessorException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise DocumentProcessorException(f"图像解析失败: {exc}", opt_ocr, "parsing_failed") from exc


async def parse_pdf_async(file, params=None):
    return await asyncio.to_thread(parse_pdf, file, params=params)


async def parse_image_async(file, params=None):
    return await asyncio.to_thread(parse_image, file, params=params)


async def _download_source_if_needed(file_path: str) -> tuple[Path, bool]:
    from yuxi.knowledge.utils.kb_utils import is_minio_url, parse_minio_url
    from yuxi.storage.minio.client import get_minio_client

    if not is_minio_url(file_path):
        return Path(file_path), False
    clean_path = file_path.split("?", 1)[0]
    suffix = Path(clean_path.rsplit("/", 1)[-1]).suffix
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
        temp_path = Path(temp_file.name)
    try:
        bucket_name, object_name = parse_minio_url(file_path)
        content = await get_minio_client().adownload_file(bucket_name, object_name)
        async with aiofiles.open(temp_path, "wb") as stream:
            await stream.write(content)
        return temp_path, True
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


async def _process_file_to_result_core(
    file_path: str,
    params: dict | None = None,
) -> MarkdownParseResult:
    actual_path, temporary = await _download_source_if_needed(file_path)
    try:
        suffix = actual_path.suffix.lower()
        if suffix == ".txt":
            result = await asyncio.to_thread(_parse_text_file, actual_path, markdown_source=False)
        elif suffix == ".md":
            result = await asyncio.to_thread(_parse_text_file, actual_path, markdown_source=True)
        elif suffix == ".pdf":
            result = await asyncio.to_thread(_parse_text_pdf, actual_path)
        elif suffix == ".docx":
            result = await asyncio.to_thread(_parse_docx, actual_path, params)
        elif suffix == ".xlsx":
            result = await asyncio.to_thread(_parse_xlsx, actual_path)
        elif suffix == ".pptx":
            result = await asyncio.to_thread(_parse_pptx, actual_path)
        elif suffix in {".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif"}:
            text = await parse_image_async(str(actual_path), params=params)
            title, blocks = _blocks_from_markdown(str(text))
            result = MarkdownParseResult(
                markdown=str(text),
                document_title=title or actual_path.stem,
                parser_name="configured_ocr",
                parser_version="unknown",
                blocks=blocks,
                file_ext=suffix,
            )
        elif suffix in {".html", ".htm"}:
            async with aiofiles.open(actual_path, encoding="utf-8") as stream:
                markdown = await asyncio.to_thread(md_convert, await stream.read(), heading_style="ATX")
            title, blocks = _blocks_from_markdown(markdown)
            result = MarkdownParseResult(
                markdown=markdown,
                document_title=title or actual_path.stem,
                parser_name="markdownify",
                parser_version=_package_version("markdownify"),
                blocks=blocks,
                file_ext=suffix,
            )
        elif suffix == ".csv":
            markdown = await asyncio.to_thread(_convert_csv_to_markdown, actual_path)
            title, blocks = _blocks_from_markdown(markdown)
            result = MarkdownParseResult(
                markdown=markdown,
                document_title=actual_path.stem,
                parser_name="pandas",
                parser_version=_package_version("pandas"),
                blocks=blocks,
                file_ext=suffix,
            )
        elif suffix == ".json":
            import json

            async with aiofiles.open(actual_path, encoding="utf-8") as stream:
                data = json.loads(await stream.read())
            markdown = f"```json\n{json.dumps(data, ensure_ascii=False, indent=2)}\n```"
            _, blocks = _blocks_from_markdown(markdown)
            result = MarkdownParseResult(
                markdown=markdown,
                document_title=actual_path.stem,
                parser_name="json",
                parser_version="1",
                blocks=blocks,
                file_ext=suffix,
            )
        elif suffix == ".zip":
            image_bucket, image_prefix = _resolve_image_storage_params(params)
            zip_result = await _process_zip_file(
                str(actual_path),
                image_bucket=image_bucket,
                image_prefix=image_prefix,
            )
            markdown = zip_result["markdown_content"]
            title, blocks = _blocks_from_markdown(markdown)
            result = MarkdownParseResult(
                markdown=markdown,
                document_title=title or actual_path.stem,
                parser_name="zip",
                parser_version="1",
                blocks=blocks,
                file_ext=suffix,
                artifacts={
                    "zip_images_info": zip_result["images_info"],
                    "zip_content_hash": zip_result["content_hash"],
                    "zip_image_bucket": image_bucket,
                    "zip_image_prefix": image_prefix,
                },
            )
        else:
            raise ValueError(f"Unsupported file type: {suffix}")
        return validate_parse_result(result, params)
    finally:
        if temporary:
            actual_path.unlink(missing_ok=True)


async def _process_file_to_markdown_core(
    file_path: str,
    params: dict | None = None,
) -> tuple[str, str | None, dict[str, Any]]:
    """Compatibility adapter for callers that still expect the old tuple."""
    result = await _process_file_to_result_core(file_path, params=params)
    return result.markdown, result.file_ext, result.artifacts


async def parse_source_to_markdown(source: str, params: dict | None = None) -> MarkdownParseResult:
    """Parse a source into the unified result (legacy function name retained)."""
    return await _process_file_to_result_core(source, params=params)


async def parse_source_to_result(source: str, params: dict | None = None) -> MarkdownParseResult:
    return await _process_file_to_result_core(source, params=params)


class Parser:
    """Facade with structured and legacy string-returning parsing methods."""

    @staticmethod
    async def aparse_result(source: str, params: dict | None = None) -> MarkdownParseResult:
        return await parse_source_to_result(source=source, params=params)

    @staticmethod
    async def aparse(source: str, params: dict | None = None) -> str:
        return (await Parser.aparse_result(source=source, params=params)).markdown

    @classmethod
    def parse_result(cls, source: str, params: dict | None = None) -> MarkdownParseResult:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(cls.aparse_result(source=source, params=params))
        raise RuntimeError("当前处于异步上下文，请使用 `await Parser.aparse_result(...)`")

    @classmethod
    def parse(cls, source: str, params: dict | None = None) -> str:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(cls.aparse(source=source, params=params))
        raise RuntimeError("当前处于异步上下文，请使用 `await Parser.aparse(...)`")
