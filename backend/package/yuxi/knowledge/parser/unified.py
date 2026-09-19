"""Unified parser module for markdown conversion."""

from __future__ import annotations

import asyncio
import base64
import os
import posixpath
import re
import tempfile
import threading
import time
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import aiofiles
from bs4 import BeautifulSoup
from docling.datamodel.base_models import InputFormat
from docling.document_converter import DocumentConverter
from langchain_community.document_loaders import PyPDFLoader
from markdownify import markdownify as md_convert

from yuxi.knowledge.parser.markdown_normalize import find_dangling_image_refs, strip_presentational_html
from yuxi.knowledge.parser.zip_utils import process_zip_file as _process_zip_file
from yuxi.storage.minio import get_minio_client
from yuxi.utils import logger

SUPPORTED_FILE_EXTENSIONS: tuple[str, ...] = (
    ".txt",
    ".md",
    ".docx",
    ".html",
    ".htm",
    ".json",
    ".csv",
    ".xls",
    ".xlsx",
    ".pdf",
    ".pptx",
    ".jpg",
    ".jpeg",
    ".png",
    ".bmp",
    ".tiff",
    ".tif",
    ".zip",
)


def is_supported_file_extension(file_name: str | os.PathLike[str]) -> bool:
    """Check whether the given file path has a supported extension."""
    return Path(file_name).suffix.lower() in SUPPORTED_FILE_EXTENSIONS


@dataclass(slots=True)
class MarkdownParseResult:
    """统一的 Markdown 解析结果。"""

    markdown: str
    file_ext: str | None = None
    artifacts: dict[str, Any] = field(default_factory=dict)


_docling_converter: DocumentConverter | None = None
_docling_converter_lock = threading.Lock()


def _get_docling_converter() -> DocumentConverter:
    """获取 Docling 文档转换器单例。"""
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
    """上传图片到 MinIO，返回 URL。"""
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


async def _prepend_original_image(text: str, file_path: Path, display_name: str, params: dict[str, Any] | None) -> str:
    """把图片输入的原图发布到公开桶，并置于识别文本之前。

    引擎把整张图当文档页处理，只输出它切出的图块（logo、印章等碎片），原图本身会丢；
    这里补回原图，让图片类文件的解析结果保持"这张图 + 识别出的文字"。
    原文件在私有的 knowledgebases 桶里（匿名读 403），必须复制一份到公开桶才能被引用。
    """
    image_bucket, image_prefix = _resolve_image_storage_params(params)
    try:
        image_data = await asyncio.to_thread(file_path.read_bytes)
        url = await asyncio.to_thread(_upload_image_to_minio, image_data, display_name, image_bucket, image_prefix)
    except Exception as e:  # noqa: BLE001
        logger.error(f"原图上传失败，解析结果仅保留识别文本: {display_name}, {e}")
        return text

    # 标签后留一个空行：cleaning._repair_soft_line_breaks 会把不以致句符结尾的行与下一行
    # 合并，</div> 结尾是 ">"，无标题的图片会被粘连成一行
    tag = f'<div style="text-align: center;"><img src="{url}" alt="{display_name}" /></div>'
    return f"{tag}\n\n{text}" if text.strip() else tag


def _parse_data_uri(data_uri: str) -> tuple[bytes, str]:
    """解析 data URI，返回 (image_data, mime_type)。"""
    header, base64_data = data_uri.split(",", 1)
    mime_type = header.split(":")[1].split(";")[0]
    image_data = base64.b64decode(base64_data)
    return image_data, mime_type


# WPS 单元格嵌入图片在单元格里只留公式，导出 markdown 后形如 =DISPIMG("ID_..",1)
# （Excel 里则显示为 =_xlfn.DISPIMG，两种前缀都要接住）
_WPS_DISPIMG_FORMULA_RE = re.compile(r'=?(?:_xlfn\.)?DISPIMG\("([^"]+)"\s*,\s*\d+\)')
_XDR_PIC_TAG = "{http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing}pic"
_XDR_CNVPR_TAG = "{http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing}cNvPr"
_A_BLIP_TAG = "{http://schemas.openxmlformats.org/drawingml/2006/main}blip"
_R_EMBED_ATTR = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed"


def _extract_wps_cell_images(file_path: Path) -> dict[str, tuple[bytes, str]]:
    """提取 WPS「嵌入单元格」图片，返回 DISPIMG ID → (图片字节, 文件名)。

    WPS 把单元格图片存在自定义部件 xl/cellimages.xml（Excel/Docling 都不认识，图片
    本体在 xl/media/，ID 与 rId 的关系在 cellimages 的 rels 里）；缺少该部件时返回空，
    非法 zip 等异常也吞掉——提取失败不应让整个文档解析失败。
    """
    images: dict[str, tuple[bytes, str]] = {}
    try:
        with zipfile.ZipFile(file_path) as zf:
            names = set(zf.namelist())
            if "xl/cellimages.xml" not in names:
                return images
            rels_root = ET.fromstring(zf.read("xl/_rels/cellimages.xml.rels"))
            rid_to_target = {
                rel.get("Id"): rel.get("Target")
                for rel in rels_root
                if rel.get("Id") and rel.get("Target")
            }
            root = ET.fromstring(zf.read("xl/cellimages.xml"))
            for pic in root.iter(_XDR_PIC_TAG):
                name_el = pic.find(f".//{_XDR_CNVPR_TAG}")
                blip = pic.find(f".//{_A_BLIP_TAG}")
                if name_el is None or blip is None:
                    continue
                image_id, rid = name_el.get("name"), blip.get(_R_EMBED_ATTR)
                target = rid_to_target.get(rid or "")
                if not image_id or not target:
                    continue
                # Target 相对 xl/ 目录（如 media/image1.png），归一化后取 zip 内路径
                media = posixpath.normpath(posixpath.join("xl", target))
                if media in names:
                    images[image_id] = (zf.read(media), posixpath.basename(media))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"提取 WPS 单元格图片失败（公式将原样保留）: {file_path.name}, {e}")
    return images


def _inline_wps_cell_images(
    markdown: str, file_path: Path, image_bucket: str, image_prefix: str
) -> str:
    """把 markdown 里漏出的 =DISPIMG 公式替换回它引用的单元格图片。

    Docling 看不到 cellimages.xml，公式文本会以 =DISPIMG("ID_..",1) 形式漏进表格格子
    （图片与行号完全丢失）。这里按 ID 上传图片本体，以行内 ![](...) 放回原格子——
    同一 ID 的图片只上传一次、URL 复用；找不到图片的公式残渣清成空串，不给检索留噪声。
    """
    if "DISPIMG" not in markdown:
        return markdown
    images = _extract_wps_cell_images(file_path)
    # ID → 行内图片 markdown（未命中/上传失败缓存为空串，重复引用不再重试）
    resolved: dict[str, str] = {}

    def _replace(match: re.Match) -> str:
        image_id = match.group(1)
        if image_id in resolved:
            return resolved[image_id]
        replacement = ""
        entry = images.get(image_id)
        if entry is not None:
            data, filename = entry
            try:
                url = _upload_image_to_minio(data, filename, image_bucket, image_prefix)
                replacement = f"![{filename}]({url})"
            except Exception as e:  # noqa: BLE001
                logger.error(f"上传 WPS 单元格图片失败 {filename}: {e}")
        resolved[image_id] = replacement
        return replacement

    return _WPS_DISPIMG_FORMULA_RE.sub(_replace, markdown)


def _convert_with_docling(file_path: Path, params: dict | None = None) -> str:
    """使用 Docling 将 docx/xlsx/pptx 转换为 Markdown。"""
    params = params or {}
    image_bucket, image_prefix = _resolve_image_storage_params(params)

    with _docling_converter_lock:
        converter = _get_docling_converter()
        result = converter.convert(file_path)

    if result.status.name != "SUCCESS":
        raise RuntimeError(f"Docling 转换失败: {result.status}")

    doc = result.document
    _collapse_horizontal_merges(doc)

    if hasattr(doc, "pictures") and doc.pictures:
        replacements: list[str] = []
        for pic in doc.pictures:
            uri = str(pic.image.uri) if hasattr(pic, "image") and hasattr(pic.image, "uri") else ""
            if uri.startswith("data:"):
                filename = "image"
                try:
                    image_data, mime_type = _parse_data_uri(uri)
                    filename = f"image_{int(time.time() * 1000000)}.{mime_type.split('/')[-1]}"
                    url = _upload_image_to_minio(image_data, filename, image_bucket, image_prefix)
                    replacements.append(f"![{filename}]({url})")
                except Exception as e:  # noqa: BLE001
                    logger.error(f"上传图片失败 {filename}: {e}")
                    replacements.append(f"[图片: {filename}]")
            else:
                replacements.append("")

        markdown = doc.export_to_markdown(compact_tables=True)
        for replacement in replacements:
            markdown = re.sub(r"<!--\s*image\s*-->", replacement, markdown, count=1)
    else:
        markdown = doc.export_to_markdown(compact_tables=True)

    # WPS 单元格嵌入图片：Docling 看不到 cellimages.xml，公式残渣在表格格子里，这里换回图片
    return _inline_wps_cell_images(markdown, file_path, image_bucket, image_prefix)


def _collapse_horizontal_merges(doc) -> None:
    """把横向合并单元格的跨度收回到锚点格子。

    docling 保留了合并信息（col_span），但导出 markdown 时逐格取文本，会把合并内容复制
    到它覆盖的每一列——Excel 里常见的整行合并标题会变成整行 8 列同一个标题。这里只收拢
    横向跨度；纵向合并（row_span）保持 docling 的填下行为，让每一行自带分组值。
    """
    for table in doc.tables:
        for cell in table.data.table_cells:
            if cell.end_col_offset_idx - cell.start_col_offset_idx > 1:
                cell.end_col_offset_idx = cell.start_col_offset_idx + 1


def _markdown_cell_text(cell) -> str:
    parts = [paragraph.text.strip() for paragraph in cell.paragraphs if paragraph.text.strip()]
    for nested_table in cell.tables:
        nested_rows = [
            " / ".join(_markdown_cell_text(nested_cell) for nested_cell in row.cells)
            for row in nested_table.rows
        ]
        parts.extend(row for row in nested_rows if row.strip(" /"))
    return "; ".join(parts).replace("\n", " ").replace("|", "\\|")


def _convert_docx_with_python_docx(file_path: Path) -> str:
    """使用 python-docx 解析 DOCX（Docling 失败时兜底）。"""
    from docx import Document

    document = Document(str(file_path))
    blocks: list[str] = []

    for para in document.paragraphs:
        text = para.text.strip()
        if text:
            blocks.append(text)

    for table in document.tables:
        rows = [[_markdown_cell_text(cell) for cell in row.cells] for row in table.rows]
        rows = [row for row in rows if any(row)]
        if not rows:
            continue

        header = rows[0]
        blocks.append(f"| {' | '.join(header)} |")
        blocks.append(f"| {' | '.join(['---'] * len(header))} |")

        for row in rows[1:]:
            normalized_row = row + [""] * (len(header) - len(row))
            blocks.append(f"| {' | '.join(normalized_row[: len(header)])} |")

        blocks.append("")

    return "\n\n".join(blocks).strip()


def _convert_html_to_markdown(content: str) -> str:
    soup = BeautifulSoup(content, "html.parser")
    for table in reversed(soup.find_all("table")):
        if table.find_parent("table") is None:
            continue
        rows = []
        for row in table.find_all("tr"):
            if row.find_parent("table") is not table:
                continue
            cells = [
                cell.get_text(" ", strip=True).replace("|", "\\|")
                for cell in row.find_all(["td", "th"], recursive=False)
            ]
            if any(cells):
                rows.append(" / ".join(cells))
        table.replace_with("; ".join(rows))
    return md_convert(str(soup), heading_style="ATX")


def _convert_csv_to_markdown(file_path: Path) -> str:
    import pandas as pd

    dataframe = pd.read_csv(file_path)
    tables: list[str] = []
    for i in range(len(dataframe)):
        row_dataframe = dataframe.iloc[[i]]
        tables.append(row_dataframe.to_markdown(index=False))
    return "\n\n".join(tables)


def pdfreader(file_path, params=None):
    """读取 PDF 文件并返回 text 文本。"""
    if isinstance(file_path, str):
        file_path = Path(file_path)

    assert file_path.exists(), "File not found"
    assert file_path.suffix.lower() == ".pdf", "File format not supported"

    loader = PyPDFLoader(str(file_path))
    docs = loader.load()
    text = "\n\n".join([d.page_content for d in docs])
    return text


def parse_pdf(file, params=None):
    """解析 PDF 文件，支持多种 OCR 方式。"""
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
    except DocumentProcessorException as e:
        logger.error(f"文档处理失败: {e.service_name} - {str(e)}")
        raise
    except Exception as e:  # noqa: BLE001
        logger.error(f"PDF 解析失败: {str(e)}")
        raise DocumentProcessorException(f"PDF解析失败: {str(e)}", opt_ocr, "parsing_failed")


def parse_image(file, params=None):
    """解析图像文件，支持多种 OCR 方式。"""
    from yuxi.knowledge.parser.base import DocumentProcessorException
    from yuxi.knowledge.parser.factory import DocumentProcessorFactory

    opt_ocr, processor_params = _resolve_ocr_engine_params(params)

    if opt_ocr == "disable":
        raise ValueError(
            "图像文件必须启用OCR才能提取文本内容。"
            "请选择OCR方式 "
            "(rapid_ocr/mineru_ocr/mineru_official/pp_structure_v3_ocr/deepseek_ocr/"
            "paddleocr_vl_1_6/paddleocr_pp_ocrv6) 或移除该文件。"
        )

    image_bucket, image_prefix = _resolve_image_storage_params(processor_params)
    processor_params.setdefault("image_bucket", image_bucket)
    processor_params.setdefault("image_prefix", image_prefix)

    try:
        return DocumentProcessorFactory.process_file(opt_ocr, file, processor_params)
    except DocumentProcessorException as e:
        logger.error(f"图像处理失败: {e.service_name} - {str(e)}")
        raise
    except Exception as e:  # noqa: BLE001
        logger.error(f"图像解析失败: {str(e)}")
        raise DocumentProcessorException(f"图像解析失败: {str(e)}", opt_ocr, "parsing_failed")


async def parse_pdf_async(file, params=None):
    return await asyncio.to_thread(parse_pdf, file, params=params)


async def parse_image_async(file, params=None):
    return await asyncio.to_thread(parse_image, file, params=params)


async def _process_file_to_markdown_core(
    file_path: str, params: dict | None = None
) -> tuple[str, str | None, dict[str, Any]]:
    """将不同类型的文件转换为 markdown，支持本地文件和 MinIO 文件。"""
    from yuxi.knowledge.utils.kb_utils import is_minio_url, parse_minio_url
    from yuxi.storage.minio.client import get_minio_client

    if is_minio_url(file_path):
        logger.debug(f"Downloading file from MinIO: {file_path}")

        if "?" in file_path:
            file_path_clean = file_path.split("?")[0]
        else:
            file_path_clean = file_path

        original_filename = file_path_clean.split("/")[-1]

        with tempfile.NamedTemporaryFile(delete=False, suffix=Path(original_filename).suffix) as temp_file:
            temp_path = temp_file.name

        try:
            bucket_name, object_name = parse_minio_url(file_path)
            minio_client = get_minio_client()
            file_content = await minio_client.adownload_file(bucket_name, object_name)

            async with aiofiles.open(temp_path, "wb") as f:
                await f.write(file_content)

            logger.debug(f"File downloaded to temp path: {temp_path}")
            actual_file_path = temp_path

        except Exception as e:  # noqa: BLE001
            if os.path.exists(temp_path):
                os.unlink(temp_path)
            logger.error(f"Failed to download file from MinIO: {e}")
            raise ValueError(f"无法从MinIO下载文件: {e}")
    else:
        actual_file_path = file_path
        original_filename = Path(file_path).name

    file_ext: str | None = None
    artifacts: dict[str, Any] = {}

    try:
        file_path_obj = Path(actual_file_path)
        file_ext = file_path_obj.suffix.lower()

        if file_ext == ".pdf":
            text = await parse_pdf_async(str(file_path_obj), params=params)
            result = f"{text}"

        elif file_ext in [".txt", ".md"]:
            async with aiofiles.open(file_path_obj, encoding="utf-8") as f:
                content = await f.read()
            result = f"{content}"

        elif file_ext == ".docx":
            try:
                result = await asyncio.to_thread(_convert_with_docling, file_path_obj, params=params)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"Docling 解析 DOCX 失败，回退到 python-docx: {file_path_obj.name}, {e}")
                result = await asyncio.to_thread(_convert_docx_with_python_docx, file_path_obj)

        elif file_ext == ".pptx":
            result = await asyncio.to_thread(_convert_with_docling, file_path_obj, params=params)

        elif file_ext == ".doc":
            from langchain_community.document_loaders import UnstructuredWordDocumentLoader

            loader = UnstructuredWordDocumentLoader(str(file_path_obj))
            docs = await asyncio.to_thread(loader.load)
            result = "\n".join(doc.page_content for doc in docs).strip()

        elif file_ext in [".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif"]:
            text = await parse_image_async(str(file_path_obj), params=params)
            result = await _prepend_original_image(text, file_path_obj, original_filename, params)

        elif file_ext in [".html", ".htm"]:
            async with aiofiles.open(file_path_obj, encoding="utf-8") as f:
                content = await f.read()
            text = await asyncio.to_thread(_convert_html_to_markdown, content)
            result = f"{text}"

        elif file_ext == ".csv":
            result = await asyncio.to_thread(_convert_csv_to_markdown, file_path_obj)

        elif file_ext in [".xls", ".xlsx"]:
            result = await asyncio.to_thread(_convert_with_docling, file_path_obj, params=params)

        elif file_ext == ".json":
            import json

            async with aiofiles.open(file_path_obj, encoding="utf-8") as f:
                content = await f.read()
            data = json.loads(content)
            json_str = json.dumps(data, ensure_ascii=False, indent=2)
            result = f"```json\n{json_str}\n```"

        elif file_ext == ".zip":
            image_bucket, image_prefix = _resolve_image_storage_params(params)
            zip_result = await _process_zip_file(
                str(file_path_obj),
                image_bucket=image_bucket,
                image_prefix=image_prefix,
            )

            artifacts = {
                "zip_images_info": zip_result["images_info"],
                "zip_content_hash": zip_result["content_hash"],
                "zip_image_bucket": image_bucket,
                "zip_image_prefix": image_prefix,
            }

            result = zip_result["markdown_content"]

        else:
            raise ValueError(f"Unsupported file type: {file_ext}")

    except Exception:
        if is_minio_url(file_path) and os.path.exists(actual_file_path):
            try:
                os.unlink(actual_file_path)
                logger.debug(f"Cleaned up temp file: {actual_file_path}")
            except Exception as cleanup_e:  # noqa: BLE001
                logger.warning(f"Failed to clean up temp file {actual_file_path}: {cleanup_e}")
        raise

    finally:
        if is_minio_url(file_path) and os.path.exists(actual_file_path):
            try:
                os.unlink(actual_file_path)
                logger.debug(f"Cleaned up temp file: {actual_file_path}")
            except Exception as e:  # noqa: BLE001
                logger.warning(f"Failed to clean up temp file {actual_file_path}: {e}")

    return result, file_ext, artifacts


async def parse_source_to_markdown(source: str, params: dict | None = None) -> MarkdownParseResult:
    """统一入口: 将文件解析为 Markdown（URL 解析已废弃）。"""
    markdown, file_ext, artifacts = await _process_file_to_markdown_core(source, params=params)

    markdown = strip_presentational_html(markdown)
    dangling_refs = find_dangling_image_refs(markdown)
    if dangling_refs:
        logger.warning(
            f"解析结果存在无法渲染的图片引用（{len(dangling_refs)} 处，疑似解析器漏转存/漏改写）: "
            f"{Path(source).name} - 例如 {dangling_refs[:3]}"
        )

    return MarkdownParseResult(
        markdown=markdown,
        file_ext=file_ext,
        artifacts=artifacts,
    )


class Parser:
    """Lightweight facade for converting file sources to markdown."""

    @staticmethod
    async def aparse(source: str, params: dict | None = None) -> str:
        """Asynchronously parse source content and return markdown text."""
        parsed = await parse_source_to_markdown(source=source, params=params)
        return parsed.markdown

    @classmethod
    def parse(cls, source: str, params: dict | None = None) -> str:
        """Synchronously parse source content and return markdown text."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(cls.aparse(source=source, params=params))

        raise RuntimeError("当前处于异步上下文，请使用 `await Parser.aparse(...)`")
