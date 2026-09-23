"""Bounded PDF rendering for flowchart vision analysis."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

import fitz

from yuxi import config


class FlowchartRenderError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class PageImage:
    page_number: int
    role: Literal["global", "crop"]
    crop_box: tuple[float, float, float, float] | None
    width: int
    height: int
    png_bytes: bytes
    warnings: tuple[str, ...] = ()
    requested_dpi: int | None = None
    effective_dpi: int | None = None


def render_flowchart_pdf(pdf_bytes: bytes) -> list[PageImage]:
    """Render ordered RGB PNG pages; no rendered images are persisted."""
    if not pdf_bytes.startswith(b"%PDF-"):
        raise FlowchartRenderError("INVALID_FLOWCHART_PDF", "文件不是有效的 PDF")
    try:
        document = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:
        raise FlowchartRenderError("INVALID_FLOWCHART_PDF", "PDF 已损坏或无法打开") from exc

    with document:
        if document.needs_pass:
            raise FlowchartRenderError("INVALID_FLOWCHART_PDF", "加密 PDF 暂不支持")
        if document.page_count == 0:
            raise FlowchartRenderError("INVALID_FLOWCHART_PDF", "PDF 没有页面")
        if document.page_count > config.flowchart_max_pages:
            raise FlowchartRenderError("FLOWCHART_TOO_MANY_PAGES", "PDF 页数超过流程图解析上限")

        images: list[PageImage] = []
        total_bytes = 0
        requested_dpi = config.flowchart_render_dpi
        min_dpi = config.flowchart_min_render_dpi
        if min_dpi > requested_dpi:
            raise FlowchartRenderError("FLOWCHART_PDF_RENDER_FAILED", "流程图最低 DPI 不能高于请求 DPI")
        for page_number in range(1, document.page_count + 1):
            try:
                page = document.load_page(page_number - 1)
                area = page.rect.width * page.rect.height
                if area <= 0:
                    raise FlowchartRenderError("INVALID_FLOWCHART_PDF", f"第 {page_number} 页尺寸无效")
                effective_dpi = min(
                    requested_dpi,
                    math.floor(72 * math.sqrt(config.flowchart_max_image_pixels / area)),
                )
                while effective_dpi >= min_dpi:
                    width = math.ceil(page.rect.width * effective_dpi / 72)
                    height = math.ceil(page.rect.height * effective_dpi / 72)
                    if width * height <= config.flowchart_max_image_pixels:
                        break
                    effective_dpi -= 1
                if effective_dpi < min_dpi:
                    raise FlowchartRenderError(
                        "FLOWCHART_IMAGE_TOO_LARGE", f"第 {page_number} 页在最低 DPI 下仍超过像素上限"
                    )
                if width <= 0 or height <= 0:
                    raise FlowchartRenderError("INVALID_FLOWCHART_PDF", f"第 {page_number} 页尺寸无效")

                warnings = []
                if effective_dpi < requested_dpi:
                    warnings.append("FLOWCHART_RENDER_DPI_REDUCED")
                aspect_ratio = max(width, height) / min(width, height)
                if aspect_ratio >= 4:
                    warnings.append("FLOWCHART_EXTREME_ASPECT_RATIO")
                if width * height >= config.flowchart_max_image_pixels * 0.75 or effective_dpi * 2 < requested_dpi:
                    warnings.append("FLOWCHART_COMPLEX_PAGE")

                pixmap = page.get_pixmap(dpi=effective_dpi, colorspace=fitz.csRGB, alpha=False)
                if pixmap.width * pixmap.height > config.flowchart_max_image_pixels:
                    raise FlowchartRenderError("FLOWCHART_IMAGE_TOO_LARGE", f"第 {page_number} 页渲染像素超过上限")
                png_bytes = pixmap.tobytes("png")
                if len(png_bytes) > config.flowchart_max_image_bytes:
                    raise FlowchartRenderError("FLOWCHART_IMAGE_TOO_LARGE", f"第 {page_number} 页 PNG 超过上限")
                total_bytes += len(png_bytes)
                if total_bytes > config.flowchart_max_render_bytes:
                    raise FlowchartRenderError("FLOWCHART_IMAGE_TOO_LARGE", "PDF 总渲染量超过上限")
                images.append(
                    PageImage(
                        page_number=page_number,
                        role="global",
                        crop_box=None,
                        width=pixmap.width,
                        height=pixmap.height,
                        png_bytes=png_bytes,
                        warnings=tuple(warnings),
                        requested_dpi=requested_dpi,
                        effective_dpi=effective_dpi,
                    )
                )
            except FlowchartRenderError:
                raise
            except Exception as exc:
                raise FlowchartRenderError("FLOWCHART_PDF_RENDER_FAILED", f"第 {page_number} 页渲染失败") from exc
        return images
